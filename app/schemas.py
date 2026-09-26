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
    # Whether the device's own local Suspend/Reactivate toggle (its on-device
    # web page) is currently engaged - independent of admin-side
    # manually_suspended. Optional so older firmware that doesn't send it
    # yet doesn't fail validation.
    locally_suspended: Optional[bool] = None
    # Tail of the device's in-RAM log buffer (a few KB at most). A snapshot
    # as of this check-in, not a live stream.
    recent_log: Optional[str] = None
    # Set only once the customer has renamed the device from its own Settings
    # menu (Settings -> Device Name) - the device's local name override is
    # blank by default (never sent) so it doesn't clobber whatever name an
    # admin set at pairing/creation time until the customer actually renames
    # it themselves. From that point on, the device is the source of truth
    # for its own name on every check-in - see the note on the rename
    # endpoint below about the resulting two-sided-edit conflict.
    name: Optional[str] = None


class CheckinResponse(BaseModel):
    subscription_status: str
    failure_code: Optional[int] = None
    grace_expires_at: Optional[datetime] = None
    server_time: datetime
    # Echoes the device's current name back on every check-in, so the
    # firmware can show it on the on-device "Device Name" settings page even
    # when the customer has never renamed it there themselves - otherwise
    # that page has no way to know whatever name an admin set at
    # pairing/creation time (see CheckinRequest.name above) and would show
    # blank instead of the real current name.
    name: str


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
    device_locally_suspended: bool
    firmware_version: Optional[str] = None
    wifi_rssi_dbm: Optional[int] = None
    last_reboot_at: Optional[datetime] = None
    slide_sync_status: Optional[str] = None
    slide_synced_at: Optional[datetime] = None


class DeviceListOut(BaseModel):
    devices: List[DeviceOut]


class DeviceDeleteOut(BaseModel):
    device_id: str
    name: str


class DeviceRenameIn(BaseModel):
    name: str


class DeviceLogOut(BaseModel):
    device_id: str
    log: Optional[str] = None
    as_of: Optional[datetime] = None  # the check-in that produced this snapshot


class PairingRequestOut(BaseModel):
    code: str
    expires_in_seconds: int


class PairingStatusOut(BaseModel):
    claimed: bool
    device_id: Optional[str] = None
    secret: Optional[str] = None  # present once claim.claimed is true


class PairingClaimIn(BaseModel):
    customer_id: int
    name: str
