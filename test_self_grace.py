"""Regression test for the device's own self-service grace request
(POST /api/v1/devices/{id}/request-grace, Device.self_grace_uses_remaining,
and the admin's reset-self-grace-uses undo) - the on-device Settings-menu
counterpart to the admin dashboard's Grant grace button, added because
Kyle wanted the device itself to be able to trigger this, budgeted (not
unlimited) so a venue can't use it to dodge billing forever. Fresh temp
SQLite DB, TestClient, same pattern as the other test_*.py scripts here.
"""
import os
import sys
import tempfile

db_fd, db_path = tempfile.mkstemp(suffix=".db")
os.close(db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
os.environ["SELF_GRACE_USES"] = "3"  # small number so exhausting it is fast to test

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


r = client.post("/api/v1/admin/customers", json={"name": "Self Grace Co"}, headers=H)
cust_id = r.json()["id"]
r = client.post("/api/v1/admin/devices", json={"customer_id": cust_id, "name": "Self Grace Device"}, headers=H)
device_id, secret = r.json()["device_id"], r.json()["secret"]
DH = {"Authorization": f"Bearer {secret}"}
other_device_id, other_secret, _ = (None, None, None)

d = get_device_out(device_id)
check("new device starts with SELF_GRACE_USES (3) uses", d["self_grace_uses_remaining"] == 3)

# --- 1. A wrong/no secret can't request grace for this device ---
r = client.post(f"/api/v1/devices/{device_id}/request-grace", headers={"Authorization": "Bearer wrong"})
check("wrong secret 401s on request-grace", r.status_code == 401)

# --- 2. First self-request succeeds, spends one use ---
r = client.post(f"/api/v1/devices/{device_id}/request-grace", headers=DH)
check("first self-request succeeds", r.status_code == 200)
check("first self-request is granted", r.json()["granted"] is True)
check("first self-request leaves 2 uses", r.json()["uses_remaining"] == 2)
check("first self-request sets grace_until", r.json()["grace_until"] is not None)

d = get_device_out(device_id)
check("admin listing reflects the spend", d["self_grace_uses_remaining"] == 2)
check("device now reads back as grace", d["effective_status"] == "grace")

# --- 3. Spend the remaining two ---
r = client.post(f"/api/v1/devices/{device_id}/request-grace", headers=DH)
check("second self-request leaves 1 use", r.json()["uses_remaining"] == 1)
r = client.post(f"/api/v1/devices/{device_id}/request-grace", headers=DH)
check("third self-request leaves 0 uses", r.json()["uses_remaining"] == 0)

# --- 4. Budget exhausted - further requests are denied, not granted ---
r = client.post(f"/api/v1/devices/{device_id}/request-grace", headers=DH)
check("exhausted request still 200s (not an error)", r.status_code == 200)
check("exhausted request is NOT granted", r.json()["granted"] is False)
check("exhausted request reports 0 uses remaining", r.json()["uses_remaining"] == 0)

d = get_device_out(device_id)
check("uses_remaining never goes negative", d["self_grace_uses_remaining"] == 0)

# --- 5. A second device's budget is independent of the first's ---
r = client.post("/api/v1/admin/devices", json={"customer_id": cust_id, "name": "Second Device"}, headers=H)
device_id2, secret2 = r.json()["device_id"], r.json()["secret"]
r = client.post(f"/api/v1/devices/{device_id2}/request-grace", headers={"Authorization": f"Bearer {secret2}"})
check("a second device has its own separate budget", r.json()["granted"] is True and r.json()["uses_remaining"] == 2)

# --- 6. A device cannot spend another device's budget (path device_id
#        must match the Bearer secret's own device - authenticate_device
#        already guarantees this, confirming it holds here too). ---
r = client.post(f"/api/v1/devices/{device_id2}/request-grace", headers=DH)  # device_id's secret against device_id2's path
check("a device's secret doesn't work against another device's id", r.status_code == 401)

# --- 7. Admin can top the exhausted device's budget back up ---
r = client.post(f"/api/v1/admin/devices/{device_id}/reset-self-grace-uses", headers=H)
check("reset-self-grace-uses succeeds", r.status_code == 200)
check("reset-self-grace-uses restores the configured default (3)", r.json()["self_grace_uses_remaining"] == 3)

r = client.post(f"/api/v1/devices/{device_id}/request-grace", headers=DH)
check("device can self-request again after an admin reset", r.json()["granted"] is True and r.json()["uses_remaining"] == 2)

# --- 8. reset-self-grace-uses on an unknown device 404s ---
r = client.post("/api/v1/admin/devices/dev_doesnotexist/reset-self-grace-uses", headers=H)
check("reset-self-grace-uses on unknown device 404s", r.status_code == 404)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All checks passed.")
