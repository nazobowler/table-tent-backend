import hashlib
import hmac
import os

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from . import models
from .database import get_db

# Single shared key for the admin endpoints - an explicit placeholder, not
# real auth. Fine while this is only Kyle hitting it with curl/Postman;
# swap for real admin login before anyone else touches this.
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "changeme-admin-key")


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def require_admin(x_admin_key: str = Header(None)):
    if not x_admin_key or not hmac.compare_digest(x_admin_key, ADMIN_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Key header")


def authenticate_device(
    device_id: str,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> models.Device:
    """Per-device bearer auth. FastAPI fills `device_id` in from the path
    parameter of whichever route depends on this (it matches dependency
    params to path params by name), so every device-facing route just needs
    `device_id` in its own path and `Depends(authenticate_device)`."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    secret = authorization[len("Bearer "):]
    device = db.query(models.Device).filter(models.Device.id == device_id).first()

    # Same error, same status code, for "no such device" and "wrong secret" -
    # a distinct 404 would let this endpoint be used to enumerate valid
    # device IDs by checking which ones don't 404.
    if not device or not hmac.compare_digest(hash_secret(secret), device.secret_hash):
        raise HTTPException(status_code=401, detail="Invalid device credentials")

    return device
