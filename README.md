# Table Tent Subscription Backend

Implements the subscription enforcement + device check-in system from
`subscription-device-status-design.md`: devices check in hourly, the server
tracks a payment-failure grace clock and a can't-reach-server offline clock
(both 24h, configurable), and an admin can list devices and manually
reset/suspend/reactivate them. Stripe drives subscription status via webhook.

Stack: Python + FastAPI + SQLite (via SQLAlchemy) + the Stripe SDK (for
webhook signature verification only - no live billing calls yet).

No auth system or admin dashboard UI exists yet - the `/api/v1/admin/*`
endpoints are protected by a single static API key from an environment
variable, meant purely as a placeholder until there's a real dashboard.

## Run it locally

```bash
cd table-tent-backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env - at minimum, set ADMIN_API_KEY to something random:
python3 -c "import secrets; print(secrets.token_hex(32))"

uvicorn app.main:app --reload
```

Starts on `http://127.0.0.1:8000` and creates `table_tent.db` (a SQLite
file) in this folder on first run. Interactive API docs are auto-generated
at `http://127.0.0.1:8000/docs` - useful for poking at endpoints by hand
without curl.

Leave `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` as the placeholders in
`.env.example` for now - the app runs fine without real ones; they only
matter once `/api/v1/webhooks/stripe` needs to verify real events, which
means having an actual Stripe account/product set up first.

## Walking through the flow

**1. Create a customer (venue):**

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/customers \
  -H "X-Admin-Key: <your ADMIN_API_KEY>" -H "Content-Type: application/json" \
  -d '{"name": "Holy Family Club"}'
```

Returns a numeric `id` - you'll need it for the next step.

**2. Provision a device for that customer:**

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/devices \
  -H "X-Admin-Key: <your ADMIN_API_KEY>" -H "Content-Type: application/json" \
  -d '{"customer_id": <id from step 1>, "name": "Front bar tent"}'
```

Returns `device_id` and a plaintext `secret` - **the secret is only ever
shown in this one response.** That pair is what gets entered into the
physical device during setup (this is the part the device-side firmware
and provisioning flow don't do yet - see "What's still missing" below).

**3. The device checks in (this is what the ESP32 firmware will call
hourly, once that part is built):**

```bash
curl -X POST http://127.0.0.1:8000/api/v1/devices/<device_id>/checkin \
  -H "Authorization: Bearer <secret>" -H "Content-Type: application/json" \
  -d '{"firmware_version": "1.0.0", "wifi_rssi_dbm": -55, "uptime_seconds": 3600}'
```

Returns `{"subscription_status": "active"|"grace"|"failed", "failure_code": ..., "grace_expires_at": ..., "server_time": ...}`.
Everything in the request body is optional except the auth header - a bare
`{}` is a valid check-in.

**4. See the fleet:**

```bash
curl http://127.0.0.1:8000/api/v1/admin/devices -H "X-Admin-Key: <your ADMIN_API_KEY>"
```

**5. Manual actions** (support/operator use, independent of Stripe):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/devices/<device_id>/reset-grace   -H "X-Admin-Key: <key>"
curl -X POST http://127.0.0.1:8000/api/v1/admin/devices/<device_id>/force-suspend -H "X-Admin-Key: <key>"
curl -X POST http://127.0.0.1:8000/api/v1/admin/devices/<device_id>/force-reactivate -H "X-Admin-Key: <key>"
```

## Deploying to Railway

Railway builds straight from the included `Dockerfile`, so there's nothing
extra to configure for the app itself - just the environment variables.

1. **Push this folder to a GitHub repo.** Railway deploys from a repo (its
   CLI can also deploy a local folder directly if you'd rather skip GitHub -
   see the note at the end of this section).

   ```bash
   git init
   git add .
   git commit -m "Table tent subscription backend"
   # create a new repo on github.com, then:
   git remote add origin <your new repo's URL>
   git push -u origin main
   ```

2. **Create the Railway project.** At [railway.app](https://railway.app),
   sign in (GitHub sign-in is simplest), click **New Project > Deploy from
   GitHub repo**, and pick this repo. Railway detects the `Dockerfile`
   automatically and starts a build - it'll fail on the first attempt
   because the environment variables below aren't set yet, which is normal.

3. **Set environment variables.** In the project, open the service, go to
   **Variables**, and add:

   | Variable | Value |
   |---|---|
   | `ADMIN_API_KEY` | A random string - generate one with `python3 -c "import secrets; print(secrets.token_hex(32))"` |
   | `STRIPE_SECRET_KEY` | `sk_test_placeholder` for now |
   | `STRIPE_WEBHOOK_SECRET` | `whsec_placeholder` for now |
   | `OFFLINE_CAP_HOURS` | `24` |
   | `GRACE_HOURS` | `24` |

   Leave `DATABASE_URL` unset - it defaults to a local SQLite file inside
   the container. That's fine to start, with one caveat: Railway's
   filesystem for a service isn't guaranteed to persist across redeploys
   unless you attach a **Volume** (Settings > Volumes, mount it at e.g.
   `/app/data`, and set `DATABASE_URL=sqlite:////app/data/table_tent.db` to
   point the app at it). Without a volume, redeploying wipes the database -
   fine for early testing, not fine once real devices depend on it. Adding
   a Postgres plugin instead (Railway offers one built in) is the more
   durable long-term option and needs zero code changes here, just a
   `DATABASE_URL` pointed at it.

4. **Redeploy** (Railway usually does this automatically once variables are
   saved; otherwise there's a manual "Redeploy" button). Once it's up,
   Railway assigns a public URL under **Settings > Networking > Generate
   Domain** - that `https://...up.railway.app` URL is what the device
   firmware and the Stripe webhook config will point at.

5. **Sanity check the live deployment:**

   ```bash
   curl https://<your-app>.up.railway.app/
   # {"status":"ok","service":"table-tent-backend"}
   ```

   Then repeat the "Walking through the flow" steps above against that URL
   instead of `127.0.0.1:8000`.

**Skipping GitHub:** if you'd rather not push this to a repo yet, Railway's
CLI (`npm i -g @railway/cli`, then `railway login` and `railway up` from
this folder) deploys the local folder directly - same Dockerfile, same
Variables step, no GitHub involved.

## What's still missing

Carried over from `subscription-backend-status.md` - this backend is only
half the picture:

- **Device-side (ESP32) check-in code.** The firmware doesn't call this
  backend at all yet - no hourly check-in, no local grace/offline clock, no
  device-ID concept. The web page's Suspend/Reactivate toggle is still
  purely manual/local.
- **Device provisioning flow** to get `device_id` + `secret` onto a
  physical unit, instead of the device having no identity at all. A natural
  place for this is right alongside the WiFi setup screens already in the
  sketch (same captive portal, same on-device keyboard).
- **Admin dashboard UI** - `/api/v1/admin/*` is API-only right now.
- **Real admin authentication** - the single shared `ADMIN_API_KEY` is a
  stand-in, not real auth.
- **DB migrations** (e.g. Alembic) - a schema change right now means
  dropping the SQLite file (or the Postgres tables) and starting over.
- **Real Stripe keys** - wire these in once you're actually ready to charge
  customers; everything here runs fine on placeholders until then.
