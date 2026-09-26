import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import List

import stripe
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from . import models, schemas
from .admin_dashboard import ADMIN_DASHBOARD_HTML
from .auth import authenticate_device, hash_secret, require_admin
from .database import Base, engine, get_db
from .state import compute_effective_state, _aware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("table-tent-backend")


def _ensure_device_columns():
    """create_all() below only creates missing TABLES, not missing COLUMNS
    on a table that already exists with real rows in it - fine for a brand
    new table like pending_claims, not fine for `devices`, which already has
    live data in production. Rather than asking for a manual ALTER TABLE on
    Railway's Postgres every time a column gets added to the model (no
    Alembic here yet - see "DB migrations" in the status doc), this checks
    for any column the current model expects but the live table doesn't
    have, and adds it. A no-op after the first boot that picks up a given
    column. Uses plain ALTER TABLE ... ADD COLUMN, which both SQLite and
    Postgres support with the same syntax for these simple column types.
    """
    inspector = inspect(engine)
    if "devices" not in inspector.get_table_names():
        return  # create_all() below will make it fresh with every column already
    existing = {c["name"] for c in inspector.get_columns("devices")}
    statements = []
    if "device_locally_suspended" not in existing:
        statements.append("ALTER TABLE devices ADD COLUMN device_locally_suspended BOOLEAN NOT NULL DEFAULT false")
    if "recent_log" not in existing:
        statements.append("ALTER TABLE devices ADD COLUMN recent_log TEXT")
    if not statements:
        return
    with engine.begin() as conn:
        for stmt in statements:
            logger.info("Startup migration: %s", stmt)
            conn.execute(text(stmt))


_ensure_device_columns()
Base.metadata.create_all(bind=engine)

STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_placeholder")

# How long an unclaimed pairing code stays valid. Short on purpose - it's
# only alive for the few minutes between a device booting unprovisioned and
# Kyle claiming it from his computer.
PAIRING_CODE_TTL_MINUTES = 15

app = FastAPI(title="Table Tent Subscription Backend", version="1.0.0")


def now_utc():
    return datetime.now(timezone.utc)


@app.get("/")
def root():
    return {"status": "ok", "service": "table-tent-backend"}


# =============================================================================
# Admin dashboard - a single self-contained HTML/JS page (no build step, no
# extra dependencies) served at /admin. It's a thin UI over the existing
# admin API below: it asks for the same X-Admin-Key you've been passing to
# curl, stores it in the browser's localStorage, and calls the same
# endpoints. No new backend logic, no new auth model - just a real screen
# instead of curl commands. Markup/JS lives in admin_dashboard.py, not here,
# so this file stays about the API.
# =============================================================================

@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard():
    return ADMIN_DASHBOARD_HTML


# =============================================================================
# Device-facing
# =============================================================================

@app.post("/api/v1/devices/{device_id}/checkin", response_model=schemas.CheckinResponse)
def checkin(
    device_id: str,
    body: schemas.CheckinRequest,
    device: models.Device = Depends(authenticate_device),
    db: Session = Depends(get_db),
):
    now = now_utc()

    device.last_checkin_at = now
    if body.firmware_version is not None:
        device.firmware_version = body.firmware_version
    if body.wifi_rssi_dbm is not None:
        device.wifi_rssi_dbm = body.wifi_rssi_dbm
    if body.uptime_seconds is not None:
        device.last_reboot_at = now - timedelta(seconds=body.uptime_seconds)
    if body.slide_sync_status is not None:
        device.slide_sync_status = body.slide_sync_status
        if body.slide_sync_status == "synced":
            device.slide_synced_at = now
    if body.locally_suspended is not None:
        device.device_locally_suspended = body.locally_suspended
    if body.recent_log is not None:
        # Defensive cap independent of the firmware's own SERIAL_LOG_MAX_CHARS
        # (4000) - keeps a modified or future client from growing this
        # column unbounded.
        device.recent_log = body.recent_log[-8000:]
    if body.name is not None and body.name.strip():
        # The device only ever sends this once the customer has renamed it
        # from Settings -> Device Name (see schemas.py) - a blank/omitted
        # name here means "no local override," not "clear the name," so
        # there's no way for a device that's never touched this feature to
        # accidentally blank out an admin-set name. Once a device does start
        # sending a name, it's the source of truth for itself going forward:
        # an admin rename via the dashboard (see /rename below) can get
        # overwritten by the device's own name on its next check-in, the
        # same two-sided-edit tradeoff already accepted for
        # device_locally_suspended.
        device.name = body.name.strip()[:100]

    db.add(device)
    db.commit()
    db.refresh(device)

    customer = db.query(models.Customer).filter(models.Customer.id == device.customer_id).first()
    if not customer:
        # Shouldn't happen (customer_id is a FK set at device creation), but
        # fail loudly rather than silently reporting a wrong status.
        raise HTTPException(status_code=500, detail="Device has no associated customer")

    eff = compute_effective_state(device, customer, now)
    logger.info("Check-in: device=%s status=%s code=%s", device.id, eff.status, eff.failure_code)

    return schemas.CheckinResponse(
        subscription_status=eff.status,
        failure_code=eff.failure_code,
        grace_expires_at=eff.grace_expires_at,
        server_time=now,
    )


# =============================================================================
# Device pairing - lets an unprovisioned device get its device_id/secret by
# displaying a short code and polling, instead of Kyle typing a 12-char id
# and a 32-char secret in on a touchscreen keyboard. The device-facing
# endpoints below are deliberately unauthenticated (there's nothing to
# authenticate yet - that's the whole point), but the code itself is short-
# lived and single-use, and claiming one still requires the admin key.
# =============================================================================

@app.post("/api/v1/pairing/request", response_model=schemas.PairingRequestOut)
def pairing_request(db: Session = Depends(get_db)):
    # Collision odds are astronomically low (6 chars from a 31-char
    # alphabet), but guard against it anyway rather than trust it blindly.
    for _ in range(5):
        code = models.generate_pairing_code()
        if not db.query(models.PendingClaim).filter(models.PendingClaim.code == code).first():
            break
    else:
        raise HTTPException(status_code=500, detail="Could not generate a unique pairing code")

    claim = models.PendingClaim(code=code)
    db.add(claim)
    db.commit()

    logger.info("Pairing: code %s requested", code)
    return schemas.PairingRequestOut(code=code, expires_in_seconds=PAIRING_CODE_TTL_MINUTES * 60)


@app.get("/api/v1/pairing/{code}/status", response_model=schemas.PairingStatusOut)
def pairing_status(code: str, db: Session = Depends(get_db)):
    claim = db.query(models.PendingClaim).filter(models.PendingClaim.code == code).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Unknown or expired pairing code")

    if not claim.claimed:
        return schemas.PairingStatusOut(claimed=False)

    # Returned every time the claim is polled, not just once - an earlier
    # version wiped the secret from the DB after the first read, on the
    # theory that only the device itself would ever check. In practice
    # anything hitting this URL once (a stray manual check, a browser tab
    # left open, etc.) could shut the device out by consuming it first.
    # Codes are short-lived (PAIRING_CODE_TTL_MINUTES) and claiming one
    # already requires the admin key, so repeat-readability here isn't a
    # meaningful new exposure.
    return schemas.PairingStatusOut(claimed=True, device_id=claim.device_id, secret=claim.secret)


# =============================================================================
# Stripe webhook
# =============================================================================

@app.post("/api/v1/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature"),
    db: Session = Depends(get_db),
):
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        logger.warning("Stripe webhook signature verification failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    data_object = event["data"]["object"]
    stripe_customer_id = data_object.get("customer")

    if not stripe_customer_id:
        return {"received": True, "note": "no customer id on event, ignored"}

    customer = (
        db.query(models.Customer)
        .filter(models.Customer.stripe_customer_id == stripe_customer_id)
        .first()
    )
    if not customer:
        logger.info("Stripe event for unknown customer %s, ignored", stripe_customer_id)
        return {"received": True, "note": "unknown customer, ignored"}

    now = now_utc()

    if event_type == "invoice.payment_failed":
        customer.subscription_status = "failed"
        db.add(customer)
        for dev in customer.devices:
            if dev.grace_started_at is None:
                dev.grace_started_at = now
                db.add(dev)

    elif event_type == "invoice.payment_succeeded":
        customer.subscription_status = "active"
        db.add(customer)
        for dev in customer.devices:
            dev.grace_started_at = None
            db.add(dev)

    elif event_type == "customer.subscription.updated":
        if data_object.get("status") == "active":
            customer.subscription_status = "active"
            db.add(customer)
            for dev in customer.devices:
                dev.grace_started_at = None
                db.add(dev)

    elif event_type == "customer.subscription.deleted":
        customer.subscription_status = "canceled"
        db.add(customer)

    db.commit()
    logger.info("Stripe webhook handled: type=%s customer=%s", event_type, stripe_customer_id)
    return {"received": True}


# =============================================================================
# Admin - bootstrapping + fleet management. Protected by a single shared
# X-Admin-Key header (see auth.py) - explicit placeholder-level auth.
# =============================================================================

@app.post("/api/v1/admin/customers", response_model=schemas.CustomerOut, dependencies=[Depends(require_admin)])
def create_customer(body: schemas.CustomerCreate, db: Session = Depends(get_db)):
    customer = models.Customer(
        name=body.name,
        stripe_customer_id=body.stripe_customer_id,
        stripe_subscription_id=body.stripe_subscription_id,
        subscription_status="active",
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@app.get("/api/v1/admin/customers", response_model=List[schemas.CustomerOut], dependencies=[Depends(require_admin)])
def list_customers(db: Session = Depends(get_db)):
    return db.query(models.Customer).all()


@app.post("/api/v1/admin/devices", response_model=schemas.DeviceCreateOut, dependencies=[Depends(require_admin)])
def create_device(body: schemas.DeviceCreate, db: Session = Depends(get_db)):
    customer = db.query(models.Customer).filter(models.Customer.id == body.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    secret = secrets.token_urlsafe(24)
    device = models.Device(
        customer_id=customer.id,
        name=body.name,
        secret_hash=hash_secret(secret),
    )
    db.add(device)
    db.commit()
    db.refresh(device)

    # The plaintext secret is only ever available in this one response -
    # only the hash is stored. Enter it into the device once during setup.
    return schemas.DeviceCreateOut(device_id=device.id, secret=secret)


@app.get(
    "/api/v1/admin/pairing/pending",
    response_model=List[str],
    dependencies=[Depends(require_admin)],
)
def list_pending_pairing_codes(db: Session = Depends(get_db)):
    """Unclaimed, unexpired codes currently being displayed by some device -
    handy if you've got more than one unit waiting to be set up at once."""
    cutoff = now_utc() - timedelta(minutes=PAIRING_CODE_TTL_MINUTES)
    claims = (
        db.query(models.PendingClaim)
        .filter(models.PendingClaim.claimed.is_(False))
        .filter(models.PendingClaim.created_at >= cutoff)
        .order_by(models.PendingClaim.created_at.desc())
        .all()
    )
    return [c.code for c in claims]


@app.post(
    "/api/v1/admin/pairing/{code}/claim",
    response_model=schemas.DeviceCreateOut,
    dependencies=[Depends(require_admin)],
)
def claim_pairing_code(code: str, body: schemas.PairingClaimIn, db: Session = Depends(get_db)):
    claim = db.query(models.PendingClaim).filter(models.PendingClaim.code == code).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Unknown pairing code")
    if claim.claimed:
        raise HTTPException(status_code=409, detail="Pairing code already claimed")

    cutoff = now_utc() - timedelta(minutes=PAIRING_CODE_TTL_MINUTES)
    if _aware(claim.created_at) is not None and _aware(claim.created_at) < cutoff:
        raise HTTPException(status_code=410, detail="Pairing code expired - reboot the device for a new one")

    customer = db.query(models.Customer).filter(models.Customer.id == body.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    secret = secrets.token_urlsafe(24)
    device = models.Device(
        customer_id=customer.id,
        name=body.name,
        secret_hash=hash_secret(secret),
    )
    db.add(device)
    # device.id is populated by a Python-side default (see models.py), which
    # only runs when this INSERT actually executes - flush here so device.id
    # is a real value below, instead of the None it would otherwise still be
    # at this point. Without this, claim.device_id was silently saved as
    # null every time (the DeviceCreateOut returned below was still correct,
    # since it reads device.id after the commit further down - only the
    # claim row itself was affected, which is what /pairing/{code}/status
    # hands back to the polling device).
    db.flush()

    claim.claimed = True
    claim.device_id = device.id
    claim.secret = secret
    db.add(claim)

    db.commit()
    db.refresh(device)

    logger.info("Pairing: code %s claimed -> device=%s customer=%s", code, device.id, customer.id)
    return schemas.DeviceCreateOut(device_id=device.id, secret=secret)


def _device_to_out(device: models.Device, db: Session) -> schemas.DeviceOut:
    customer = db.query(models.Customer).filter(models.Customer.id == device.customer_id).first()
    eff = compute_effective_state(device, customer, now_utc())
    return schemas.DeviceOut(
        device_id=device.id,
        name=device.name,
        customer_id=device.customer_id,
        customer_name=customer.name if customer else "(unknown)",
        effective_status=eff.status,
        failure_code=eff.failure_code,
        grace_expires_at=eff.grace_expires_at,
        last_checkin_at=device.last_checkin_at,
        manually_suspended=device.manually_suspended,
        device_locally_suspended=device.device_locally_suspended,
        firmware_version=device.firmware_version,
        wifi_rssi_dbm=device.wifi_rssi_dbm,
        last_reboot_at=device.last_reboot_at,
        slide_sync_status=device.slide_sync_status,
        slide_synced_at=device.slide_synced_at,
    )


@app.get("/api/v1/admin/devices", response_model=List[schemas.DeviceOut], dependencies=[Depends(require_admin)])
def list_devices(db: Session = Depends(get_db)):
    devices = db.query(models.Device).all()
    return [_device_to_out(d, db) for d in devices]


@app.delete(
    "/api/v1/admin/devices/{device_id}",
    response_model=schemas.DeviceDeleteOut,
    dependencies=[Depends(require_admin)],
)
def delete_device(device_id: str, db: Session = Depends(get_db)):
    """Permanently removes a Device row - for cleaning up the orphaned rows
    left behind by earlier pairing-bug attempts (see the status doc) and any
    test/throwaway devices. There's no undo: the row, its secret hash, and
    its check-in history (last_checkin_at, recent_log, etc.) are gone once
    this runs. This does NOT touch the physical hardware - if a real device
    still has this device_id/secret saved, it'll just start getting 401s on
    its next check-in (same as any other unknown device) rather than
    anything more dramatic. Doesn't cascade to anything else - Device has no
    dependent rows in this schema (PendingClaim references a device_id but
    doesn't have a real FK constraint on it, so a stale claim row pointing
    at a deleted device is harmless clutter, not a delete-blocking error)."""
    device = _get_device_or_404(device_id, db)
    name = device.name
    db.delete(device)
    db.commit()
    logger.info("Admin: deleted device=%s (name=%s)", device_id, name)
    return schemas.DeviceDeleteOut(device_id=device_id, name=name)


@app.get(
    "/api/v1/admin/devices/{device_id}/log",
    response_model=schemas.DeviceLogOut,
    dependencies=[Depends(require_admin)],
)
def device_log(device_id: str, db: Session = Depends(get_db)):
    """Tail of the device's own log buffer, as of its last check-in - not a
    live stream (see recent_log on the model / performCheckin() in the
    firmware). Kept as its own endpoint rather than part of DeviceOut so the
    fleet list doesn't carry a few KB of text per row on every refresh."""
    device = _get_device_or_404(device_id, db)
    return schemas.DeviceLogOut(device_id=device.id, log=device.recent_log, as_of=device.last_checkin_at)


def _get_device_or_404(device_id: str, db: Session) -> models.Device:
    device = db.query(models.Device).filter(models.Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@app.post(
    "/api/v1/admin/devices/{device_id}/reset-grace",
    response_model=schemas.DeviceOut,
    dependencies=[Depends(require_admin)],
)
def reset_grace(device_id: str, db: Session = Depends(get_db)):
    device = _get_device_or_404(device_id, db)
    device.grace_started_at = now_utc()
    db.add(device)
    db.commit()
    db.refresh(device)
    return _device_to_out(device, db)


@app.post(
    "/api/v1/admin/devices/{device_id}/rename",
    response_model=schemas.DeviceOut,
    dependencies=[Depends(require_admin)],
)
def rename_device(device_id: str, body: schemas.DeviceRenameIn, db: Session = Depends(get_db)):
    """Admin-side rename, from the dashboard. Note this can be overwritten by
    the device's own next check-in if the customer has ever renamed it from
    the device's own Settings menu (Settings -> Device Name) - see the
    comment on checkin()'s handling of body.name. Both sides can rename;
    whichever renamed most recently wins, same as the two suspend flags."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name can't be blank")
    device = _get_device_or_404(device_id, db)
    device.name = name[:100]
    db.add(device)
    db.commit()
    db.refresh(device)
    return _device_to_out(device, db)


@app.post(
    "/api/v1/admin/devices/{device_id}/force-suspend",
    response_model=schemas.DeviceOut,
    dependencies=[Depends(require_admin)],
)
def force_suspend(device_id: str, db: Session = Depends(get_db)):
    device = _get_device_or_404(device_id, db)
    device.manually_suspended = True
    db.add(device)
    db.commit()
    db.refresh(device)
    return _device_to_out(device, db)


@app.post(
    "/api/v1/admin/devices/{device_id}/force-reactivate",
    response_model=schemas.DeviceOut,
    dependencies=[Depends(require_admin)],
)
def force_reactivate(device_id: str, db: Session = Depends(get_db)):
    device = _get_device_or_404(device_id, db)
    device.manually_suspended = False
    db.add(device)
    db.commit()
    db.refresh(device)
    return _device_to_out(device, db)
