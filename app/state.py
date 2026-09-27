import os
from datetime import datetime, timedelta, timezone

# How long a device can go without a successful check-in, or a customer can
# sit in "payment failed" grace, before the display flips to Failed. Both
# configurable via env vars so they can be tuned without a code change.
OFFLINE_CAP_HOURS = float(os.getenv("OFFLINE_CAP_HOURS", "24"))
GRACE_HOURS = float(os.getenv("GRACE_HOURS", "24"))

# How many times a device can grant itself a grace override from its own
# Settings menu (see Device.self_grace_uses_remaining in models.py and
# POST /api/v1/devices/{id}/request-grace in main.py) before it needs an
# operator to top the count back up from the dashboard. A budget, not a
# cooldown - each use costs one, whatever the underlying cause - so a venue
# can't just keep clicking it forever to dodge billing indefinitely, but
# still has real, repeatable breathing room for actual bad luck (a flaky
# router, a card that takes a day to get sorted) without having to reach
# an admin every single time.
SELF_GRACE_USES = int(os.getenv("SELF_GRACE_USES", "7"))


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
    cron job to sweep state. Priority order: manual suspend -> manual grace
    override -> offline cap -> canceled subscription (immediate) ->
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

    # 2. Manual grace override - an operator-granted "keep this playing for
    # now" window (see the comment on Device.manual_grace_until in
    # models.py). Checked ahead of every cause below it can override - offline
    # cap, canceled subscription, payment-failure grace - so a single button
    # covers "Back soon" for any of those reasons without the operator first
    # diagnosing which one it is. Deliberately does NOT override a manual
    # suspend (step 1) - that's a deliberate "turn this off" decision, not a
    # clock running out, so this override isn't meant to undo it.
    manual_until = _aware(device.manual_grace_until)
    if manual_until and now < manual_until:
        return EffectiveState("grace", grace_expires_at=manual_until)

    # 3. Offline cap - protects against a device being taken offline
    # indefinitely to dodge billing, while surviving ordinary WiFi/router
    # hiccups without interrupting the slideshow. Resets automatically
    # whenever last_checkin_at is updated (i.e. on every successful check-in),
    # so there's no separate clock field to maintain for this one.
    last_checkin = _aware(device.last_checkin_at)
    if last_checkin is None or (now - last_checkin) > timedelta(hours=OFFLINE_CAP_HOURS):
        return EffectiveState("failed", failure_code=504)

    # 4. A canceled subscription is an immediate failure - a deliberate
    # cancellation doesn't get the 24h courtesy window a declined card does.
    if customer.subscription_status == "canceled":
        return EffectiveState("failed", failure_code=402)

    # 5. Payment failed - within the grace window the slideshow keeps
    # playing (this state is visible on the dashboard, invisible to the
    # venue/customer); past it, same 402 as a cancellation.
    if customer.subscription_status == "failed":
        started = _aware(device.grace_started_at) or now
        expires_at = started + timedelta(hours=GRACE_HOURS)
        if now > expires_at:
            return EffectiveState("failed", failure_code=402)
        return EffectiveState("grace", grace_expires_at=expires_at)

    # 6. Nothing wrong.
    return EffectiveState("active")
