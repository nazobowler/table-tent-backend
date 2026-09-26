import secrets
import string
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import relationship

from .database import Base


def now_utc():
    return datetime.now(timezone.utc)


# Avoid visually-ambiguous characters on a small touchscreen font: no 0/O,
# 1/I/L.
_PAIRING_CODE_ALPHABET = "".join(
    c for c in string.ascii_uppercase + string.digits if c not in "0O1IL"
)


def generate_pairing_code(length: int = 6) -> str:
    return "".join(secrets.choice(_PAIRING_CODE_ALPHABET) for _ in range(length))


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    stripe_customer_id = Column(String, nullable=True, index=True)
    stripe_subscription_id = Column(String, nullable=True)
    # active | failed | canceled
    subscription_status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), default=now_utc)

    devices = relationship("Device", back_populates="customer")


class Device(Base):
    __tablename__ = "devices"

    id = Column(String, primary_key=True, default=lambda: "dev_" + uuid.uuid4().hex[:12])
    secret_hash = Column(String, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    name = Column(String, nullable=False)  # venue / device label

    last_checkin_at = Column(DateTime(timezone=True), nullable=True)
    grace_started_at = Column(DateTime(timezone=True), nullable=True)
    manually_suspended = Column(Boolean, nullable=False, default=False)
    # Mirrors the device's own local Suspend/Reactivate toggle (its on-device
    # web page), reported on every check-in. Previously this had no backend
    # visibility at all - a device suspended locally looked "Active" on the
    # admin dashboard even though it was showing "Back soon" in person. Kept
    # as its own column rather than folded into manually_suspended, since
    # they're different actors (whoever's standing at the unit vs an admin
    # suspending for billing) and only the device itself can ever clear this
    # one - reactivating from the dashboard can't reach into the device.
    device_locally_suspended = Column(Boolean, nullable=False, default=False)

    # Diagnostics, all optional - reported by the device's check-in payload.
    firmware_version = Column(String, nullable=True)
    wifi_rssi_dbm = Column(Integer, nullable=True)
    last_reboot_at = Column(DateTime(timezone=True), nullable=True)
    slide_sync_status = Column(String, nullable=True)  # synced | pending | stale
    slide_synced_at = Column(DateTime(timezone=True), nullable=True)

    # Tail of the device's in-RAM log buffer, sent with each check-in (see
    # app/main.py's checkin() and the firmware's performCheckin()). A
    # snapshot as of last_checkin_at, not a live stream - fetched on demand
    # via GET /api/v1/admin/devices/{id}/log rather than included in the
    # fleet list, since it can be a few KB and isn't needed for every row.
    recent_log = Column(String, nullable=True)

    # Set by an admin action (POST /push-firmware or /firmware/{version}/push-all
    # in main.py) to request this device install a specific firmware version.
    # Compared against the device's own self-reported firmware_version above
    # on every check-in (see checkin()) - as long as they differ, the
    # check-in response tells the device an update is waiting and hands it a
    # download URL. Once the device flashes it and reboots, its next
    # check-in reports the matching version and this "pending update" state
    # clears itself - same computed-not-stored pattern already used for
    # manually_suspended/effective_status, no separate "mark complete" step.
    target_firmware_version = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=now_utc)

    customer = relationship("Customer", back_populates="devices")


class PendingClaim(Base):
    """A short-lived pairing code an unprovisioned device generates and
    displays on its own screen. Kyle claims it from his computer (picking
    which customer it belongs to) instead of typing device_id/secret by
    hand on the touchscreen - the device then picks up its real credentials
    by polling. See /api/v1/pairing/* in main.py."""

    __tablename__ = "pending_claims"

    code = Column(String, primary_key=True, default=generate_pairing_code)
    created_at = Column(DateTime(timezone=True), default=now_utc)
    claimed = Column(Boolean, nullable=False, default=False)

    # Filled in at claim time; device_id/secret are returned to whatever
    # polls GET /api/v1/pairing/{code}/status once claimed=true. Not wiped
    # after the first read - codes are short-lived and claiming one already
    # requires the admin key, so it's fine for the device to keep picking
    # this up on retries.
    device_id = Column(String, nullable=True)
    secret = Column(String, nullable=True)


class FirmwareBuild(Base):
    """A compiled firmware .bin uploaded from the dashboard, stored right in
    Postgres rather than on Railway's local disk - the disk doesn't survive
    a redeploy, the database does, and these binaries are only a couple MB
    at most, well within what a bytea column comfortably holds. `version` is
    a free-form label Kyle assigns on upload (e.g. "1.1.0") - re-uploading
    the same version string overwrites the existing row, which is
    convenient while iterating on a build before it's ready to push
    anywhere, but means a version string that HAS already been pushed to a
    device shouldn't be reused for a different binary without meaning to
    replace what that device will fetch next."""

    __tablename__ = "firmware_builds"

    version = Column(String, primary_key=True)
    size_bytes = Column(Integer, nullable=False)
    data = Column(LargeBinary, nullable=False)
    uploaded_at = Column(DateTime(timezone=True), default=now_utc)
