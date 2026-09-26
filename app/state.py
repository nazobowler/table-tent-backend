import os
from datetime import datetime, timedelta, timezone

# How long a device can go without a successful check-in, or a customer can
# sit in "payment failed" grace, before the display flips to Failed. Both
# configurable via env vars so they can be tuned without a code change.
OFFLINE_CAP_HOURS = float(os.getenv("OFFLINE_CAP_HOURS", "24"))
GRACE_HOURS = float(os.getenv("GRACE_HOURS", "24"))


def _aware(dt):
    """SQLite doesn't persist tzinfo even on DateTime(timezone=True) columns -
    values read back out are naive. Everything this app writes is UTC, so
    treat a naive value as UTC rather than letting it crash a comparison
    against a timezone-aware "now". Becomes a no-op (and moot) on Postgres,
    which does persist tzinfo."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class EffectiveState:
    def __init__(self, status, failure_code=None, grace_expires_at=None):
        self.status = status  # "active" | "grace" | "failed"
        self.failure_code = failure_code  # 402 | 423 | 504 | None
        self.grace_expires_at = grace_expires_at


def compute_effective_state(device, customer, now=None) -> EffectiveState:
    """Effective state is computed on read, never stored - avoids needing a
    cron job to sweep state. Priority order matches the design doc:
    manual suspend -> offline cap -> canceled subscription (immediate) ->
    payment-failure grace -> active."""
    now = now or datetime.now(timezone.utc)

    # 1. A manual override - from the admin dashboard/API, or from the
    # device's own local Suspend toggle (see device_locally_suspended on the
    # model) - always wins, independent of whatever Stripe or the check-in
    # clock says. Either source alone is enough to suspend; both need to be
    # clear for the device to show as active again. The backend can't reach
    # into a device to flip its local toggle, so a device_locally_suspended
    # device only clears once it reports locally_suspended=false on some
    # later check-in - the dashboard says as much rather than implying
    # Reactivate always works.
    if device.manually_suspended or device.device_locally_suspended:
        return EffectiveState("failed", failure_code=423)

    # 2. Offline cap - protects against a device being taken offline
    # indefinitely to dodge billing, while surviving ordinary WiFi/router
    # hiccups without interrupting the slideshow. Resets automatically
    # whenever last_checkin_at is updated (i.e. on every successful check-in),
    # so there's no separate clock field to maintain for this one.
    last_checkin = _aware(device.last_checkin_at)
    if last_checkin is None or (now - last_checkin) > timedelta(hours=OFFLINE_CAP_HOURS):
        return EffectiveState("failed", failure_code=504)

    # 3. A canceled subscription is an immediate failure - a deliberate
    # cancellation doesn't get the 24h courtesy window a declined card does.
    if customer.subscription_status == "canceled":
        return EffectiveState("failed", failure_code=402)

    # 4. Payment failed - within the grace window the slideshow keeps
    # playing (this state is visible on the dashboard, invisible to the
    # venue/customer); past it, same 402 as a cancellation.
    if customer.subscription_status == "failed":
        started = _aware(device.grace_started_at) or now
        expires_at = started + timedelta(hours=GRACE_HOURS)
        if now > expires_at:
            return EffectiveState("failed", failure_code=402)
        return EffectiveState("grace", grace_expires_at=expires_at)

    # 5. Nothing wrong.
    return EffectiveState("active")
