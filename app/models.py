import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .database import Base


def now_utc():
    return datetime.now(timezone.utc)


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
