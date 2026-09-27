"""Regression test for the Wi-Fi based device-location feature
(request-location admin endpoint, request_wifi_scan check-in round trip,
resolve_wifi_location resolution, and the startup column migration).
Run with a fresh temp SQLite DB each time - not part of any CI, just a
one-off verification script matching the pattern used throughout this
project's other rounds of changes.
"""
import os
import sys
import tempfile

# Fresh temp SQLite DB and a known admin key, set BEFORE importing app.main
# (database.py/auth.py read these at import time).
db_fd, db_path = tempfile.mkstemp(suffix=".db")
os.close(db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["ADMIN_API_KEY"] = "test-admin-key"

sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

client = TestClient(main.app)
ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}

failures = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        failures.append(label)


# --- Setup: a customer and a device ---
r = client.post("/api/v1/admin/customers", json={"name": "Test Venue"}, headers=ADMIN_HEADERS)
customer_id = r.json()["id"]

r = client.post("/api/v1/admin/devices", json={"customer_id": customer_id, "name": "Test Device"}, headers=ADMIN_HEADERS)
device_id = r.json()["device_id"]
secret = r.json()["secret"]
DEVICE_HEADERS = {"Authorization": f"Bearer {secret}"}


def checkin(body):
    return client.post(f"/api/v1/devices/{device_id}/checkin", json=body, headers=DEVICE_HEADERS)


def get_device_out():
    r = client.get("/api/v1/admin/devices", headers=ADMIN_HEADERS)
    devices = r.json()
    return next(d for d in devices if d["device_id"] == device_id)


# --- 1. Ordinary check-in with no location activity ---
r = checkin({"firmware_version": "1.1.2"})
check("plain check-in succeeds", r.status_code == 200)
check("plain check-in: request_wifi_scan defaults false", r.json()["request_wifi_scan"] is False)

d = get_device_out()
check("fresh device has no location fields set", d["location_lat"] is None and d["location_address"] is None)
check("fresh device location_requested is false", d["location_requested"] is False)

# --- 2. Admin clicks Locate ---
r = client.post(f"/api/v1/admin/devices/{device_id}/request-location", headers=ADMIN_HEADERS)
check("request-location succeeds", r.status_code == 200)
check("request-location sets location_requested true", r.json()["location_requested"] is True)

# --- 3. Next check-in (no wifi_aps yet) should be told to scan ---
r = checkin({"firmware_version": "1.1.2"})
check("check-in after Locate click asks device to scan", r.json()["request_wifi_scan"] is True)

d = get_device_out()
check("location_requested still true (device hasn't scanned yet)", d["location_requested"] is True)

# --- 4. Device scans and reports wifi_aps - mock the Google API calls so
#        this test doesn't depend on network access or a real API key. ---
FAKE_LOCATION = {"lat": 40.123, "lng": -75.456, "accuracy": 22.0, "address": "123 Main St, Anytown, PA"}
orig_resolve = main.resolve_wifi_location
main.resolve_wifi_location = lambda aps: FAKE_LOCATION

try:
    r = checkin({
        "firmware_version": "1.1.2",
        "wifi_aps": [
            {"mac": "aa:bb:cc:dd:ee:01", "rssi": -55, "channel": 6},
            {"mac": "aa:bb:cc:dd:ee:02", "rssi": -70, "channel": 11},
        ],
    })
    check("check-in with wifi_aps succeeds", r.status_code == 200)
    check("request_wifi_scan clears once resolved", r.json()["request_wifi_scan"] is False)

    d = get_device_out()
    check("location_lat stored", d["location_lat"] == FAKE_LOCATION["lat"])
    check("location_lng stored", d["location_lng"] == FAKE_LOCATION["lng"])
    check("location_accuracy_m stored", d["location_accuracy_m"] == FAKE_LOCATION["accuracy"])
    check("location_address stored", d["location_address"] == FAKE_LOCATION["address"])
    check("location_updated_at stored", d["location_updated_at"] is not None)
    check("location_requested cleared", d["location_requested"] is False)
finally:
    main.resolve_wifi_location = orig_resolve

# --- 5. A second Locate, but the device reports an EMPTY scan (saw
#        nothing) - should still clear location_requested without erroring
#        or touching the previously-resolved location. ---
client.post(f"/api/v1/admin/devices/{device_id}/request-location", headers=ADMIN_HEADERS)
r = checkin({"firmware_version": "1.1.2", "wifi_aps": []})
check("empty wifi_aps check-in succeeds", r.status_code == 200)
d = get_device_out()
check("empty scan still clears location_requested", d["location_requested"] is False)
check("empty scan doesn't wipe the previously-resolved location", d["location_address"] == FAKE_LOCATION["address"])

# --- 6. A third Locate, but resolve_wifi_location fails (simulating no
#        API key / API error) - should clear the flag without erroring and
#        leave the last-known-good location alone. ---
client.post(f"/api/v1/admin/devices/{device_id}/request-location", headers=ADMIN_HEADERS)
main.resolve_wifi_location = lambda aps: None
try:
    r = checkin({
        "firmware_version": "1.1.2",
        "wifi_aps": [{"mac": "aa:bb:cc:dd:ee:03", "rssi": -60, "channel": 1}],
    })
    check("check-in survives a resolve failure", r.status_code == 200)
    d = get_device_out()
    check("resolve failure still clears location_requested", d["location_requested"] is False)
    check("resolve failure doesn't wipe the last-known-good address", d["location_address"] == FAKE_LOCATION["address"])
finally:
    main.resolve_wifi_location = orig_resolve

# --- 7. request-location on an unknown device 404s ---
r = client.post("/api/v1/admin/devices/dev_doesnotexist/request-location", headers=ADMIN_HEADERS)
check("request-location on unknown device 404s", r.status_code == 404)

# --- 8. Migration: an old-schema `devices` table (no location_* columns,
#        no location_requested) with a real row survives _ensure_device_columns(). ---
import sqlite3  # noqa: E402

mig_fd, mig_path = tempfile.mkstemp(suffix=".db")
os.close(mig_fd)
conn = sqlite3.connect(mig_path)
conn.execute(
    """CREATE TABLE devices (
        id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, customer_id INTEGER NOT NULL,
        name TEXT NOT NULL, last_checkin_at TIMESTAMP, grace_started_at TIMESTAMP,
        manually_suspended BOOLEAN NOT NULL DEFAULT 0,
        device_locally_suspended BOOLEAN NOT NULL DEFAULT 0,
        firmware_version TEXT, wifi_rssi_dbm INTEGER, local_ip TEXT,
        last_reboot_at TIMESTAMP, slide_sync_status TEXT, slide_synced_at TIMESTAMP,
        recent_log TEXT, target_firmware_version TEXT, created_at TIMESTAMP
    )"""
)
conn.execute(
    "INSERT INTO devices (id, secret_hash, customer_id, name) VALUES (?, ?, ?, ?)",
    ("dev_oldschema", "hash", 1, "Old Schema Device"),
)
conn.commit()
conn.close()

old_db_url = os.environ["DATABASE_URL"]
os.environ["DATABASE_URL"] = f"sqlite:///{mig_path}"
import importlib  # noqa: E402
from app import database as database_module  # noqa: E402

importlib.reload(database_module)
from sqlalchemy import inspect as sa_inspect  # noqa: E402

# Re-run the migration function against this old-schema DB directly
# (re-importing app.main would re-run its whole module-level setup against
# a NEW engine bound to the reloaded database module, which is exactly
# what we want to exercise here).
importlib.reload(main)

inspector = sa_inspect(database_module.engine)
cols = {c["name"] for c in inspector.get_columns("devices")}
check("migration added location_lat", "location_lat" in cols)
check("migration added location_requested", "location_requested" in cols)

row = database_module.engine.connect().execute(
    __import__("sqlalchemy").text("SELECT id, name, location_requested FROM devices WHERE id = 'dev_oldschema'")
).fetchone()
check("old row survived migration", row is not None and row[1] == "Old Schema Device")
check("old row's location_requested defaulted to false", row is not None and row[2] in (0, False))

os.environ["DATABASE_URL"] = old_db_url

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All checks passed.")
