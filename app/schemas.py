from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class WifiApIn(BaseModel):
    """One nearby Wi-Fi access point seen during a device's scan (see
    CheckinRequest.wifi_aps below) - mirrors the shape Google's
    Geolocation API expects for a wifiAccessPoints entry."""

    mac: str
    rssi: int
    channel: Optional[int] = None


class CheckinRequest(BaseModel):
    firmware_version: Optional[str] = None
    battery_pct: Optional[float] = None
    current_slide_id: Optional[str] = None
    wifi_rssi_dbm: Optional[int] = None
    # The name (SSID) of the WiFi network the device is currently joined to
    # (WiFi.SSID()). Optional so older firmware that doesn't send it yet
    # doesn't fail validation.
    wifi_ssid: Optional[str] = None
    # The device's own local IP on its current WiFi network (WiFi.localIP()),
    # so the admin dashboard can link to the device's own on-device web page.
    # Optional so older firmware that doesn't send it yet doesn't fail
    # validation.
    local_ip: Optional[str] = None
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
    # Nearby Wi-Fi access points from a scan, sent only on the one check-in
    # following a response that set request_wifi_scan=true (see
    # CheckinResponse below) - never on a schedule, since a physically-
    # installed table tent's location doesn't change. An empty list (device
    # scanned but saw nothing) is treated differently from an omitted field
    # (device hasn't scanned yet, or is on firmware too old to) - see
    # checkin()'s handling of this in main.py.
    wifi_aps: Optional[List[WifiApIn]] = None


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
    # True exactly when an admin has pushed a firmware version (see
    # Device.target_firmware_version) that differs from the firmware_version
    # this device just reported above. firmware_update_version is only
    # populated alongside it, naming which version to fetch from
    # GET /api/v1/devices/{device_id}/firmware/{version}. Both default so a
    # normal check-in with no pending update just gets false/None.
    firmware_update_available: bool = False
    firmware_update_version: Optional[str] = None
    # True for exactly one check-in response after an admin clicks "Locate"
    # on the dashboard (see Device.location_requested) - tells the firmware
    # to scan nearby Wi-Fi access points and send them on its next check-in
    # (see CheckinRequest.wifi_aps above). Defaults false so a normal
    # check-in with no pending location request just gets false.
    request_wifi_scan: bool = False


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
    wifi_ssid: Optional[str] = None
    local_ip: Optional[str] = None
    last_reboot_at: Optional[datetime] = None
    slide_sync_status: Optional[str] = None
    slide_synced_at: Optional[datetime] = None
    # Set while an admin has pushed a firmware version this device hasn't
    # yet reported back as its own (see Device.target_firmware_version) -
    # None means no update is pending. Lets the dashboard show a "pending"
    # note on a device row until it's actually flashed and rebooted.
    target_firmware_version: Optional[str] = None
    # Wi-Fi based geolocation - see Device.location_* in models.py. All
    # None until a "Locate" request has actually resolved once.
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    location_accuracy_m: Optional[float] = None
    location_address: Optional[str] = None
    location_updated_at: Optional[datetime] = None
    # True while a "Locate" request is waiting on the device's next one or
    # two check-ins to actually produce a scan - lets the dashboard show a
    # "Locating…" note instead of just a stale/blank location.
    location_requested: bool = False
    # Set while an operator's "Grant grace" override is active - see
    # Device.manual_grace_until in models.py. The dashboard uses this (rather
    # than just effective_status == "grace") to tell a manual override apart
    # from an ordinary payment-failure grace window, since both render the
    # same Grace badge/countdown otherwise.
    manual_grace_until: Optional[datetime] = None
    # Remaining budget for the device's own self-service "Grant Grace
    # Period" Settings menu item - see Device.self_grace_uses_remaining in
    # models.py. Shown on the dashboard so an operator can see when a
    # device is running low and reset it if needed.
    self_grace_uses_remaining: int


class DeviceGraceRequestOut(BaseModel):
    """Response to the device's own POST /api/v1/devices/{id}/request-grace -
    deliberately separate from DeviceOut (which is admin-only) since this is
    device-facing and only needs enough for the on-device confirmation
    screen to say something accurate."""
    granted: bool
    uses_remaining: int
    grace_until: Optional[datetime] = None


class DeviceListOut(BaseModel):
    devices: List[DeviceOut]


class FirmwareBuildOut(BaseModel):
    version: str
    size_bytes: int
    notes: Optional[str] = None
    uploaded_at: datetime

    class Config:
        from_attributes = True


class FirmwareListOut(BaseModel):
    builds: List[FirmwareBuildOut]


class FirmwarePushIn(BaseModel):
    version: str


class FirmwarePushAllOut(BaseModel):
    version: str
    devices_updated: int


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
