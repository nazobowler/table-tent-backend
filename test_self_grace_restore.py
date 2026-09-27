"""Regression test for automatically restoring a device's self-service grace
budget (Device.self_grace_uses_remaining) once a check-in reports the device
back in genuine good standing (effective_status == "active"). Kyle asked for
this so a device that burned through its Grant Grace Period budget during one
billing hiccup isn't stuck at a reduced count forever once that's resolved -
previously the only way back to full was an operator noticing and clicking
"Reset uses" on the dashboard.

Deliberately scoped to *active*, not just "the manual override expired" -
covers the case where a device's own self-granted override lapses but the
underlying subscription is still genuinely failed, where it must NOT restore
the budget (that would let self-service grace requests refill themselves
indefinitely, defeating the whole point of it being a budget). Fresh temp
SQLite DB, TestClient, same pattern as the other test_*.py scripts here.
"""
import os
import sys
import tempfile
import time

db_fd, db_path = tempfile.mkstemp(suffix=".db")
os.close(db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
os.environ["SELF_GRACE_USES"] = "2"  # small so exhausting it is fast to test
os.environ["GRACE_HOURS"] = "0.0006"  # ~2.16 seconds - both the manual-grace-
# override window (request-grace) and the payment-failure grace window use
# this same constant, so both can be waited past quickly in this test.

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


def do_checkin(device_id, secret):
    return client.post(
        f"/api/v1/devices/{device_id}/checkin",
        json={"firmware_version": "1.1.8"},
        headers={"Authorization": f"Bearer {secret}"},
    )


r = client.post("/api/v1/admin/customers", json={"name": "Auto Restore Co"}, headers=H)
cust_id = r.json()["id"]
r = client.post("/api/v1/admin/devices", json={"customer_id": cust_id, "name": "Restore Device"}, headers=H)
device_id, secret = r.json()["device_id"], r.json()["secret"]
DH = {"Authorization": f"Bearer {secret}"}

do_checkin(device_id, secret)  # baseline, so last_checkin_at is fresh
d = get_device_out(device_id)
check("device starts with full self-grace budget (2)", d["self_grace_uses_remaining"] == 2)

# --- 1. Spend a self-grace use - this also sets manual_grace_until, so the
#        device is legitimately in "grace" (not "active") right after ---
r = client.post(f"/api/v1/devices/{device_id}/request-grace", headers=DH)
check("self-grace request granted", r.json()["granted"] is True)

r = do_checkin(device_id, secret)
check("check-in right after spending a use reads as grace, not active", r.json()["subscription_status"] == "grace")
d = get_device_out(device_id)
check("budget NOT restored while still under the override (grace, not active)", d["self_grace_uses_remaining"] == 1)

# --- 2. Once the override lapses and the underlying subscription is fine,
#        the next check-in reads as active and the budget comes back on its
#        own, with no admin action ---
time.sleep(2.3)
r = do_checkin(device_id, secret)
check("check-in after the override lapses reads as active", r.json()["subscription_status"] == "active")
d = get_device_out(device_id)
check("budget auto-restored to full (2) on the active check-in", d["self_grace_uses_remaining"] == 2)

# --- 3. A device that's already full stays full and doesn't error on a
#        further active check-in (the no-op branch) ---
r = do_checkin(device_id, secret)
check("further active check-in still 200s and stays active", r.json()["subscription_status"] == "active")
d = get_device_out(device_id)
check("budget stays at full (2), no negative side effect", d["self_grace_uses_remaining"] == 2)

# --- 4. Spend both uses again, then let a REAL payment failure be the
#        reason the device isn't active once the self-granted override
#        lapses - the budget must NOT be restored here, since the device
#        genuinely isn't in good standing (this is the guard that keeps
#        the budget from refilling itself indefinitely) ---
client.post(f"/api/v1/devices/{device_id}/request-grace", headers=DH)
client.post(f"/api/v1/devices/{device_id}/request-grace", headers=DH)
d = get_device_out(device_id)
check("budget exhausted again (0) ahead of the real-failure check", d["self_grace_uses_remaining"] == 0)

client.post(f"/api/v1/admin/customers/{cust_id}/force-payment-failed", headers=H)
time.sleep(2.3)  # let the self-granted override lapse
r = do_checkin(device_id, secret)
check(
    "once the override lapses onto a genuine payment failure, status is grace or failed, never active",
    r.json()["subscription_status"] in ("grace", "failed"),
)
d = get_device_out(device_id)
check(
    "budget stays at 0 - a real ongoing failure must not refill the self-service budget",
    d["self_grace_uses_remaining"] == 0,
)

# --- 5. Clearing the real failure brings it back to active and restores
#        the budget, same as step 2 ---
client.post(f"/api/v1/admin/customers/{cust_id}/force-payment-succeeded", headers=H)
r = do_checkin(device_id, secret)
check("check-in after the real failure clears reads as active", r.json()["subscription_status"] == "active")
d = get_device_out(device_id)
check("budget restored to full (2) once genuinely back in good standing", d["self_grace_uses_remaining"] == 2)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All checks passed.")
