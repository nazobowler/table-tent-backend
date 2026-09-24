import secrets
import string
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
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

    # Diagnostics, all optional - reported by the device's check-in payload.
    firmware_version = Column(String, nullable=True)
    wifi_rssi_dbm = Column(Integer, nullable=True)
    last_reboot_at = Column(DateTime(timezone=True), nullable=True)
    slide_sync_status = Column(String, nullable=True)  # synced | pending | stale
    slide_synced_at = Column(DateTime(timezone=True), nullable=True)

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
