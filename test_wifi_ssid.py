"""Regression test for tracking a device's WiFi network name (SSID) on
check-in. Kyle asked to be able to see, on the backend, which WiFi network
each device is tied to - this adds Device.wifi_ssid, populated from the new
CheckinRequest.wifi_ssid field (sent by the firmware as WiFi.SSID()) and
surfaced on DeviceOut for the dashboard, right alongside the existing
wifi_rssi_dbm/local_ip diagnostics. Fresh temp SQLite DB, TestClient, same
pattern as the other test_*.py scripts here.
"""
import os
import sys
import tempfile

db_fd, db_path = tempfile.mkstemp(suffix=".db")
os.close(db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["ADMIN_API_KEY"] = "test-admin-key"

sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

client = TestClient(main.app)
H = {"X-Admin-Key": "test-admin-key"}

failures = []


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)


def get_device_out(device_id):
    r = client.get("/api/v1/admin/devices", headers=H)
    return next(d for d in r.json() if d["device_id"] == device_id)


r = client.post("/api/v1/admin/customers", json={"name": "SSID Co"}, headers=H)
cust_id = r.json()["id"]
r = client.post("/api/v1/admin/devices", json={"customer_id": cust_id, "name": "SSID Device"}, headers=H)
device_id, secret = r.json()["device_id"], r.json()["secret"]
DH = {"Authorization": f"Bearer {secret}"}

# --- 1. Brand new device has no SSID yet ---
d = get_device_out(device_id)
check("device starts with no wifi_ssid", d["wifi_ssid"] is None)

# --- 2. A check-in reporting wifi_ssid stores and surfaces it ---
r = client.post(
    f"/api/v1/devices/{device_id}/checkin",
    json={"firmware_version": "1.1.9", "wifi_ssid": "Bar Wifi 5G", "wifi_rssi_dbm": -52},
    headers=DH,
)
check("check-in with wifi_ssid succeeds", r.status_code == 200)
d = get_device_out(device_id)
check("wifi_ssid stored and returned", d["wifi_ssid"] == "Bar Wifi 5G")
check("wifi_rssi_dbm still tracked alongside it", d["wifi_rssi_dbm"] == -52)

# --- 3. A later check-in with a different SSID (e.g. device moved to a new
#        network after a WiFi reset) overwrites the old value ---
r = client.post(
    f"/api/v1/devices/{device_id}/checkin",
    json={"firmware_version": "1.1.9", "wifi_ssid": "Back Office Net"},
    headers=DH,
)
d = get_device_out(device_id)
check("wifi_ssid updates on a later check-in", d["wifi_ssid"] == "Back Office Net")

# --- 4. An omitted wifi_ssid (older firmware) doesn't clobber the existing
#        value - same "field omitted vs blank" treatment as the other
#        optional diagnostics on this endpoint ---
r = client.post(
    f"/api/v1/devices/{device_id}/checkin",
    json={"firmware_version": "1.1.9"},
    headers=DH,
)
check("check-in with no wifi_ssid field still 200s", r.status_code == 200)
d = get_device_out(device_id)
check("omitted wifi_ssid does not clear the previously-known value", d["wifi_ssid"] == "Back Office Net")

# --- 5. A blank/whitespace-only wifi_ssid is also not stored (mirrors the
#        local_ip handling: don't store an empty string as if it were real) ---
r = client.post(
    f"/api/v1/devices/{device_id}/checkin",
    json={"firmware_version": "1.1.9", "wifi_ssid": "   "},
    headers=DH,
)
d = get_device_out(device_id)
check("blank wifi_ssid does not overwrite the last known value", d["wifi_ssid"] == "Back Office Net")

# --- 6. A very long/unusual SSID (unicode, quotes) round-trips safely and is
#        capped rather than erroring ---
long_ssid = "☃ Café \"Guest\" Wi-Fi " + ("x" * 100)
r = client.post(
    f"/api/v1/devices/{device_id}/checkin",
    json={"firmware_version": "1.1.9", "wifi_ssid": long_ssid},
    headers=DH,
)
check("check-in with a long/unicode wifi_ssid still 200s", r.status_code == 200)
d = get_device_out(device_id)
check("wifi_ssid is capped rather than stored unbounded", len(d["wifi_ssid"]) == 64)
check("capped wifi_ssid keeps the original prefix", d["wifi_ssid"] == long_ssid[:64])

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All checks passed.")
