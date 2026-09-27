"""Regression test for the admin dashboard's "Force payment failed" /
"Force payment succeeded" testing buttons (POST /api/v1/admin/customers/{id}/
force-payment-failed and .../force-payment-succeeded) - a stand-in for a real
Stripe invoice.payment_failed / invoice.payment_succeeded event, added so the
grace-period/Back-soon behavior can be exercised on a real device without a
real Stripe sandbox or a card that genuinely declines. Kyle asked for this
after an earlier doc entry described it as already built when it wasn't.

Shares its actual effect (_mark_payment_failed/_mark_payment_succeeded in
app/main.py) with the real Stripe webhook, so this test is also indirectly
proving the webhook's own side effects are correct. Fresh temp SQLite DB,
TestClient, same pattern as the other test_*.py scripts here.
"""
import os
import sys
import tempfile
import time

db_fd, db_path = tempfile.mkstemp(suffix=".db")
os.close(db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
# Small so the grace-expiry case (step 7 below) doesn't need a real sleep of
# hours - GRACE_HOURS is read once at import time same as SELF_GRACE_USES in
# test_self_grace.py, so this must be set before importing app.main.
os.environ["GRACE_HOURS"] = "0.0005"  # ~1.8 seconds

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


def get_customer_out(customer_id):
    r = client.get("/api/v1/admin/customers", headers=H)
    return next(c for c in r.json() if c["id"] == customer_id)


def do_checkin(device_id, secret):
    return client.post(
        f"/api/v1/devices/{device_id}/checkin",
        json={"firmware_version": "1.1.8"},
        headers={"Authorization": f"Bearer {secret}"},
    )


r = client.post("/api/v1/admin/customers", json={"name": "Force Payment Co"}, headers=H)
cust_id = r.json()["id"]
check("new customer starts active", r.json()["subscription_status"] == "active")

r = client.post("/api/v1/admin/devices", json={"customer_id": cust_id, "name": "Device A"}, headers=H)
device_a, secret_a = r.json()["device_id"], r.json()["secret"]

# --- 1. Baseline check-in establishes last_checkin_at so the device doesn't
#        fall into the offline cap (504) and mask what we're actually testing ---
r = do_checkin(device_a, secret_a)
check("baseline check-in is active", r.json()["subscription_status"] == "active" and r.json()["failure_code"] is None)

# --- 2. force-payment-failed flips the customer and starts the device's
#        grace clock, same as a real invoice.payment_failed webhook would ---
r = client.post(f"/api/v1/admin/customers/{cust_id}/force-payment-failed", headers=H)
check("force-payment-failed succeeds", r.status_code == 200)
check("force-payment-failed sets customer status to failed", r.json()["subscription_status"] == "failed")

r = do_checkin(device_a, secret_a)
check("check-in right after force-failed reads as grace (within window)", r.json()["subscription_status"] == "grace")
check("grace response carries grace_expires_at", r.json()["grace_expires_at"] is not None)

d = get_customer_out(cust_id)
check("admin listing reflects failed status", d["subscription_status"] == "failed")

# --- 3. A second force-payment-failed call while already failed must NOT
#        reset the grace clock - mirrors the real webhook's behavior
#        (Stripe can and does redeliver invoice.payment_failed) and is
#        exactly what _mark_payment_failed's "if grace_started_at is None"
#        guard is for. Sleep past the window, then confirm a second call
#        doesn't grant a fresh one. ---
time.sleep(2.2)  # past GRACE_HOURS (~1.8s)
r = client.post(f"/api/v1/admin/customers/{cust_id}/force-payment-failed", headers=H)
check("second force-payment-failed still succeeds", r.status_code == 200)

r = do_checkin(device_a, secret_a)
check(
    "second force-failed does NOT reset an already-expired grace clock",
    r.json()["subscription_status"] == "failed" and r.json()["failure_code"] == 402,
)

# --- 4. force-payment-succeeded clears it back to active and clears the
#        grace clock (checked indirectly: forcing failed again afterwards
#        gives a fresh full window, not an instantly-expired one) ---
r = client.post(f"/api/v1/admin/customers/{cust_id}/force-payment-succeeded", headers=H)
check("force-payment-succeeded succeeds", r.status_code == 200)
check("force-payment-succeeded sets customer status to active", r.json()["subscription_status"] == "active")

r = do_checkin(device_a, secret_a)
check("check-in after force-succeeded is active again", r.json()["subscription_status"] == "active")

# --- 5. force-payment-succeeded is a safe no-op when already active ---
r = client.post(f"/api/v1/admin/customers/{cust_id}/force-payment-succeeded", headers=H)
check("force-payment-succeeded on an already-active customer still 200s", r.status_code == 200)

# --- 6. Multiple devices under one customer all get the same grace clock
#        started together, since payment status is per-customer, not
#        per-device ---
r = client.post("/api/v1/admin/devices", json={"customer_id": cust_id, "name": "Device B"}, headers=H)
device_b, secret_b = r.json()["device_id"], r.json()["secret"]
do_checkin(device_b, secret_b)  # baseline, so it isn't 504 (offline) instead

client.post(f"/api/v1/admin/customers/{cust_id}/force-payment-failed", headers=H)
r_a = do_checkin(device_a, secret_a)
r_b = do_checkin(device_b, secret_b)
check("all devices under the customer see grace together (device A)", r_a.json()["subscription_status"] == "grace")
check("all devices under the customer see grace together (device B)", r_b.json()["subscription_status"] == "grace")

client.post(f"/api/v1/admin/customers/{cust_id}/force-payment-succeeded", headers=H)

# --- 7. Unknown customer 404s on both endpoints ---
r = client.post("/api/v1/admin/customers/999999/force-payment-failed", headers=H)
check("force-payment-failed on unknown customer 404s", r.status_code == 404)
r = client.post("/api/v1/admin/customers/999999/force-payment-succeeded", headers=H)
check("force-payment-succeeded on unknown customer 404s", r.status_code == 404)

# --- 8. Missing/wrong admin key is rejected on both ---
r = client.post(f"/api/v1/admin/customers/{cust_id}/force-payment-failed")
check("force-payment-failed with no admin key is rejected", r.status_code == 401)
r = client.post(f"/api/v1/admin/customers/{cust_id}/force-payment-succeeded", headers={"X-Admin-Key": "wrong"})
check("force-payment-succeeded with wrong admin key is rejected", r.status_code == 401)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All checks passed.")
