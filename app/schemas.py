from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class CheckinRequest(BaseModel):
    firmware_version: Optional[str] = None
    battery_pct: Optional[float] = None
    current_slide_id: Optional[str] = None
    wifi_rssi_dbm: Optional[int] = None
    uptime_seconds: Optional[int] = None
    slide_sync_status: Optional[str] = None
    device_time: Optional[str] = None


class CheckinResponse(BaseModel):
    subscription_status: str
    failure_code: Optional[int] = None
    grace_expires_at: Optional[datetime] = None
    server_time: datetime


class CustomerCreate(BaseModel):
    name: str
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None


class CustomerOut(BaseModel):
    id: int
    name: str
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    subscription_status: str

    class Config:
        from_attributes = True


class DeviceCreate(BaseModel):
    customer_id: int
    name: str


class DeviceCreateOut(BaseModel):
    device_id: str
    secret: str  # plaintext - only ever returned this once


class DeviceOut(BaseModel):
    device_id: str
    name: str
    customer_id: int
    customer_name: str
    effective_status: str
    failure_code: Optional[int] = None
    grace_expires_at: Optional[datetime] = None
    last_checkin_at: Optional[datetime] = None
    manually_suspended: bool
    firmware_version: Optional[str] = None
    wifi_rssi_dbm: Optional[int] = None
    last_reboot_at: Optional[datetime] = None
    slide_sync_status: Optional[str] = None
    slide_synced_at: Optional[datetime] = None


class DeviceListOut(BaseModel):
    devices: List[DeviceOut]
