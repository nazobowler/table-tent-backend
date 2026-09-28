import secrets
import string
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import relationship

from .database import Base
from .state import SELF_GRACE_USES


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
    # The name (SSID) of the WiFi network the device is currently joined to,
    # reported on every check-in (WiFi.SSID() in the firmware). Shown on the
    # dashboard next to the signal-strength reading so Kyle can tell at a
    # glance which network a device is on - handy when a venue has more than
    # one AP/SSID, or when confirming a device actually moved onto a new
    # network after a WiFi reset. Just a label; nothing here authenticates or
    # otherwise depends on it.
    wifi_ssid = Column(String, nullable=True)
    # The device's own local IP on whatever WiFi network it's joined
    # (WiFi.localIP() in the firmware), reported on every check-in. Lets the
    # dashboard link straight to a device's own on-device web page (its
    # local Suspend/Reactivate toggle + live log tail) - only reachable by
    # someone on the same physical network as the unit, which is the point:
    # this is "find/view the device on your own network," not a
    # backend-side remote view. A device whose IP changes (DHCP lease
    # renewal, reconnect) just reports the new one on its next check-in;
    # nothing here is a stable/reserved address.
    local_ip = Column(String, nullable=True)
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

    # Wi-Fi based geolocation. Populated by resolving the device's most
    # recent nearby-Wi-Fi scan (see CheckinRequest.wifi_aps in schemas.py)
    # through Google's Geolocation API for a lat/lng + accuracy radius,
    # then reverse-geocoding that through Google's Geocoding API for a
    # human-readable address - see resolve_wifi_location() in main.py. All
    # nullable: a device that's never been asked to locate itself, or whose
    # most recent attempt failed (no API key configured, no network match
    # found), just has none of this set and the dashboard shows "-" same as
    # any other not-yet-known field.
    location_lat = Column(Float, nullable=True)
    location_lng = Column(Float, nullable=True)
    location_accuracy_m = Column(Float, nullable=True)
    location_address = Column(String, nullable=True)
    location_updated_at = Column(DateTime(timezone=True), nullable=True)
    # Set by POST /api/v1/admin/devices/{id}/request-location (the
    # dashboard's "Locate" button). Tells the device, on its *next*
    # check-in response, to scan nearby Wi-Fi access points and include
    # them on the check-in right after that (see
    # CheckinResponse.request_wifi_scan and checkin()'s handling of
    # body.wifi_aps) - cleared automatically once that follow-up check-in
    # comes in, whether or not the location resolve itself succeeded.
    # Physically-installed table tents don't move, so this is on-demand
    # rather than something every device does on every 20-minute check-in -
    # no reason to burn Google Geolocation API quota on a location that
    # isn't going to have changed.
    location_requested = Column(Boolean, nullable=False, default=False)

    # Operator "give this device more time, whatever the actual cause" override
    # - set by POST /api/v1/admin/devices/{id}/grant-grace (the dashboard's
    # "Grant grace" button). While this is set and in the future,
    # compute_effective_state() in state.py returns "grace" unconditionally,
    # ahead of the offline cap / canceled-subscription / payment-failure
    # checks - so it covers a device about to (or already) show the on-device
    # "Back soon" screen for ANY reason (dead Wi-Fi during a router swap,
    # Stripe webhook lagging behind a fixed card, device being relocated,
    # etc.), without the operator needing to first work out which of those it
    # actually is. Nullable/unset by default; doesn't touch last_checkin_at
    # or grace_started_at, so once it lapses the real underlying state (again,
    # whatever it is) just resumes as if this had never been set.
    manual_grace_until = Column(DateTime(timezone=True), nullable=True)

    # Self-service budget for the device's OWN "Grant Grace Period" Settings
    # menu item (as opposed to the admin dashboard's "Grant grace" button,
    # which isn't limited) - see SELF_GRACE_USES in state.py and
    # POST /api/v1/devices/{id}/request-grace in main.py. Decrements by one
    # on each successful self-triggered grant; once it hits 0 the device's
    # own request just fails with "ask your operator" rather than granting
    # forever. Doesn't refill automatically - an operator tops it back up
    # from the dashboard (Reset uses) if a venue legitimately needs more.
    self_grace_uses_remaining = Column(Integer, nullable=False, default=SELF_GRACE_USES)

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
    # Free-text release notes Kyle types in at upload time (e.g. "fixes the
    # blank device-name bug", "first OTA-capable build") - purely for his own
    # reference on the dashboard, never sent to or read by a device.
    notes = Column(String, nullable=True)
    uploaded_at = Column(DateTime(timezone=True), default=now_utc)
