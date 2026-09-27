"""Broader regression pass alongside test_location_feature.py - re-checks
the existing admin flows (pairing, rename, suspend/reactivate, delete,
firmware push) still work with the models.py/schemas.py/main.py changes
made for the Wi-Fi location feature (new Device columns, new CheckinRequest/
CheckinResponse/DeviceOut fields). Fresh temp SQLite DB, TestClient."""
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


# Customer + device
r = client.post("/api/v1/admin/customers", json={"name": "Regr Co"}, headers=H)
cust_id = r.json()["id"]
r = client.post("/api/v1/admin/devices", json={"customer_id": cust_id, "name": "Regr Device"}, headers=H)
device_id, secret = r.json()["device_id"], r.json()["secret"]
DH = {"Authorization": f"Bearer {secret}"}

# Check-in still works and returns the required new field with a sane default
r = client.post(f"/api/v1/devices/{device_id}/checkin", json={"firmware_version": "1.1.2"}, headers=DH)
check("checkin OK", r.status_code == 200)
check("checkin response has name", r.json().get("name") == "Regr Device")
check("checkin response has request_wifi_scan=false", r.json().get("request_wifi_scan") is False)

# Wrong secret still 401s (not swallowed by new optional fields)
r = client.post(f"/api/v1/devices/{device_id}/checkin", json={"firmware_version": "1.1.2"}, headers={"Authorization": "Bearer wrong"})
check("wrong secret still 401s", r.status_code == 401)

# Rename
r = client.post(f"/api/v1/admin/devices/{device_id}/rename", json={"name": "Renamed Device"}, headers=H)
check("rename OK", r.status_code == 200 and r.json()["name"] == "Renamed Device")

# Force suspend / reactivate
r = client.post(f"/api/v1/admin/devices/{device_id}/force-suspend", headers=H)
check("force-suspend OK", r.status_code == 200 and r.json()["manually_suspended"] is True)
r = client.post(f"/api/v1/admin/devices/{device_id}/force-reactivate", headers=H)
check("force-reactivate OK", r.status_code == 200 and r.json()["manually_suspended"] is False)

# Reset grace
r = client.post(f"/api/v1/admin/devices/{device_id}/reset-grace", headers=H)
check("reset-grace OK", r.status_code == 200)

# Pairing flow
r = client.post("/api/v1/pairing/request")
code = r.json()["code"]
check("pairing request OK", r.status_code == 200 and len(code) == 6)
r = client.post(f"/api/v1/admin/pairing/{code}/claim", json={"customer_id": cust_id, "name": "Paired Device"}, headers=H)
check("pairing claim OK", r.status_code == 200)
paired_id = r.json()["device_id"]
r = client.get(f"/api/v1/pairing/{code}/status")
check("pairing status reflects claim", r.json()["claimed"] is True and r.json()["device_id"] == paired_id)

# Firmware upload + push + cancel
r = client.post(
    "/api/v1/admin/firmware/upload",
    data={"version": "9.9.9", "notes": "regression test build"},
    files={"file": ("fw.bin", b"fakebinarydata", "application/octet-stream")},
    headers=H,
)
check("firmware upload OK", r.status_code == 200)
r = client.post(f"/api/v1/admin/devices/{device_id}/push-firmware", json={"version": "9.9.9"}, headers=H)
check("push-firmware OK", r.status_code == 200 and r.json()["target_firmware_version"] == "9.9.9")
r = client.post(f"/api/v1/admin/devices/{device_id}/cancel-firmware-push", headers=H)
check("cancel-firmware-push OK", r.status_code == 200 and r.json()["target_firmware_version"] is None)

# Device log endpoint
r = client.get(f"/api/v1/admin/devices/{device_id}/log", headers=H)
check("device log endpoint OK", r.status_code == 200)

# Delete device
r = client.delete(f"/api/v1/admin/devices/{device_id}", headers=H)
check("delete device OK", r.status_code == 200)
r = client.get(f"/api/v1/admin/devices/{device_id}/log", headers=H)
check("deleted device's log 404s", r.status_code == 404)

# Dashboard page itself still renders
r = client.get("/admin")
check("dashboard page loads", r.status_code == 200 and "Location" in r.text)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("All checks passed.")
