"""Regression test for the manual 'Grant grace' override (grant-grace /
clear-grace-override admin endpoints, Device.manual_grace_until, and its
precedence in compute_effective_state) - covers the case Kyle asked for:
a way to keep a device off the on-device 'Back soon' screen for a while
regardless of which underlying cause (offline cap, canceled subscription,
expired payment-failure grace) is actually behind it, without overriding a
deliberate manual suspend. Fresh temp SQLite DB, TestClient, same pattern
as test_location_feature.py / test_full_regression.py.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

db_fd, db_path = tempfile.mkstemp(suffix=".db")
os.close(db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["ADMIN_API_KEY"] = "test-admin-key"

sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app import models  # noqa: E402
from app.database import SessionLocal  # noqa: E402

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


def make_device(name):
    r = client.post("/api/v1/admin/customers", json={"name": f"{name} Co"}, headers=H)
    cust_id = r.json()["id"]
    r = client.post("/api/v1/admin/devices", json={"customer_id": cust_id, "name": name}, headers=H)
    return r.json()["device_id"], r.json()["secret"], cust_id


# --- 1. A device that's simply never checked in (offline cap / 504) ---
device_id, secret, cust_id = make_device("Offline Device")
d = get_device_out(device_id)
check("brand new device with no check-in is failed/504", d["effective_status"] == "failed" and d["failure_code"] == 504)

r = client.post(f"/api/v1/admin/devices/{device_id}/grant-grace", headers=H)
check("grant-grace succeeds", r.status_code == 200)
check("grant-grace immediately reports grace", r.json()["effective_status"] == "grace")
check("grant-grace sets manual_grace_until", r.json()["manual_grace_until"] is not None)
check("grant-grace's grace_expires_at reflects the override", r.json()["grace_expires_at"] == r.json()["manual_grace_until"])

d = get_device_out(device_id)
check("override still active on a fresh admin read", d["effective_status"] == "grace")

r = client.post(f"/api/v1/admin/devices/{device_id}/clear-grace-override", headers=H)
check("clear-grace-override succeeds", r.status_code == 200)
check("clearing drops manual_grace_until", r.json()["manual_grace_until"] is None)
check("clearing reveals the real underlying state again (504)", r.json()["effective_status"] == "failed" and r.json()["failure_code"] == 504)

# --- 2. Override beats a canceled subscription (402) ---
device_id2, secret2, cust_id2 = make_device("Canceled Sub Device")
# Give it a successful check-in first so it's not ALSO hitting the offline cap.
client.post(f"/api/v1/devices/{device_id2}/checkin", json={"firmware_version": "1.1.7"}, headers={"Authorization": f"Bearer {secret2}"})
db = SessionLocal()
cust = db.query(models.Customer).filter(models.Customer.id == cust_id2).first()
cust.subscription_status = "canceled"
db.add(cust)
db.commit()
db.close()

d = get_device_out(device_id2)
check("canceled subscription is failed/402 before override", d["effective_status"] == "failed" and d["failure_code"] == 402)

client.post(f"/api/v1/admin/devices/{device_id2}/grant-grace", headers=H)
d = get_device_out(device_id2)
check("override beats a canceled subscription too", d["effective_status"] == "grace")

# And the device's own check-in response reflects it, not just the admin listing.
r = client.post(f"/api/v1/devices/{device_id2}/checkin", json={"firmware_version": "1.1.7"}, headers={"Authorization": f"Bearer {secret2}"})
check("device-facing check-in also sees the override", r.json()["subscription_status"] == "grace")

# --- 3. Override does NOT beat a manual suspend - that's deliberate, not a clock ---
device_id3, secret3, cust_id3 = make_device("Suspended Device")
client.post(f"/api/v1/admin/devices/{device_id3}/force-suspend", headers=H)
client.post(f"/api/v1/admin/devices/{device_id3}/grant-grace", headers=H)
d = get_device_out(device_id3)
check("manual suspend still wins over a grace override", d["effective_status"] == "failed" and d["failure_code"] == 423)

# --- 4. Re-clicking grant-grace extends the window rather than erroring ---
device_id4, secret4, cust_id4 = make_device("Re-click Device")
r1 = client.post(f"/api/v1/admin/devices/{device_id4}/grant-grace", headers=H)
until1 = r1.json()["manual_grace_until"]
r2 = client.post(f"/api/v1/admin/devices/{device_id4}/grant-grace", headers=H)
until2 = r2.json()["manual_grace_until"]
check("re-clicking grant-grace pushes the window further out", until2 >= until1)

# --- 5. Unknown device 404s on both endpoints ---
r = client.post("/api/v1/admin/devices/dev_doesnotexist/grant-grace", headers=H)
check("grant-grace on unknown device 404s", r.status_code == 404)
r = client.post("/api/v1/admin/devices/dev_doesnotexist/clear-grace-override", headers=H)
check("clear-grace-override on unknown device 404s", r.status_code == 404)

# --- 6. A lapsed (past) override doesn't linger - falls back to the real state ---
device_id5, secret5, cust_id5 = make_device("Lapsed Override Device")
client.post(f"/api/v1/devices/{device_id5}/checkin", json={"firmware_version": "1.1.7"}, headers={"Authorization": f"Bearer {secret5}"})
db = SessionLocal()
dev = db.query(models.Device).filter(models.Device.id == device_id5).first()
dev.manual_grace_until = datetime.now(timezone.utc) - timedelta(hours=1)  # already expired
db.add(dev)
db.commit()
db.close()
d = get_device_out(device_id5)
check("a lapsed override falls back to active (real state)", d["effective_status"] == "active")

# --- 7. Migration: old-schema devices table (no manual_grace_until) survives ---
import sqlite3  # noqa: E402
import importlib  # noqa: E402
from sqlalchemy import inspect as sa_inspect, text as sa_text  # noqa: E402

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
        recent_log TEXT, target_firmware_version TEXT,
        location_lat REAL, location_lng REAL, location_accuracy_m REAL,
        location_address TEXT, location_updated_at TIMESTAMP,
        location_requested BOOLEAN NOT NULL DEFAULT 0, created_at TIMESTAMP
    )"""
)
conn.execute(
    "INSERT INTO devices (id, secret_hash, customer_id, name) VALUES (?, ?, ?, ?)",
    ("dev_oldschema2", "hash", 1, "Old Schema Device 2"),
)
conn.commit()
conn.close()

old_db_url = os.environ["DATABASE_URL"]
os.environ["DATABASE_URL"] = f"sqlite:///{mig_path}"
from app import database as database_module  # noqa: E402

importlib.reload(database_module)
importlib.reload(main)

inspector = sa_inspect(database_module.engine)
cols = {c["name"] for c in inspector.get_columns("devices")}
check("migration added manual_grace_until", "manual_grace_until" in cols)

row = database_module.engine.connect().execute(
    sa_text("SELECT id, name FROM devices WHERE id = 'dev_oldschema2'")
).fetchone()
check("old row survived migration", row is not None and row[1] == "Old Schema Device 2")

os.environ["DATABASE_URL"] = old_db_url

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All checks passed.")
