# MOB Delivery Backend

Multi-tenant Django/DRF backend for MOB's delivery fleet: vehicles, drivers,
trips/assignment, live GPS tracking with anomaly detection, damage reports,
SOS, webhooks, and push notifications.

## Local development setup

1. Copy `.env.example` to `.env` and fill in the values (see comments in
   that file for what each one is for).
2. Start Postgres and Redis:
   ```
   docker compose up -d db redis
   ```
3. Install dependencies and run migrations:
   ```
   pip install -r requirements.txt
   python manage.py migrate
   ```
4. Run the three application processes (see below) — a plain
   `python manage.py runserver` is **not** sufficient for this project.

## Why three processes

This is not a typical single-process Django deployment. Two features
require long-lived, always-on processes that a request/response web server
cannot provide on its own:

- **WebSockets** (`tracking.consumers.TrackingConsumer`, `sos.consumers`) —
  live vehicle-location broadcast and SOS alerts. These need an ASGI
  server, not WSGI.
- **Scheduled/background work** (Celery) — DL-expiry locking, stationary/
  wrong-direction anomaly detection sweeps, webhook dispatch, vehicle
  document expiry alerts, location-ping retention purging, idempotency
  record cleanup. See `CELERY_BEAT_SCHEDULE` in `mob_delivery/settings.py`
  for the full, current list of periodic tasks.

Because Celery Beat only *schedules* tasks and does not execute them
itself, and a scheduler process must never be run more than once, the
web server, the task executor, and the scheduler are three separate
processes that all need to be running simultaneously in any environment
(local dev included, though `runserver`-only development happens to work
for endpoints that don't touch WebSockets or Celery tasks).

## The three processes

Run each of these in its own terminal/container:

**1. ASGI web server** — serves the HTTP API and the WebSocket endpoints.
```
daphne -b 0.0.0.0 -p 8000 mob_delivery.asgi:application
```
(`daphne` is already in `INSTALLED_APPS`/`requirements.txt`. In local dev,
`python manage.py runserver` also works and auto-reloads, but it doesn't
reflect the production ASGI setup — prefer `daphne` when testing anything
WebSocket-related.)

**2. Celery worker** — executes queued/scheduled tasks (DL-expiry lock,
anomaly detection, webhook dispatch, push notifications, document expiry
alerts, retention purges, idempotency cleanup).
```
celery -A mob_delivery worker -l info
```
On Windows, the prefork pool isn't supported; use the solo pool instead:
```
celery -A mob_delivery worker -l info -P solo
```

**3. Celery Beat** — the scheduler. Ticks `CELERY_BEAT_SCHEDULE` and
enqueues each task at its configured time for the worker(s) above to pick
up. Exactly one Beat process must run per environment — running more than
one will enqueue every scheduled task multiple times.
```
celery -A mob_delivery beat -l info
```

## Redis — one instance, three roles

A single Redis server is used for three unrelated purposes, kept on
separate logical DB indices so they never collide:

| DB index | Role | Setting |
|---|---|---|
| 0 | Celery broker + result backend | `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` |
| 1 | Django cache (driver OTP storage, live vehicle-location snapshots) | `REDIS_URL` |
| 2 | Channels layer (WebSocket broadcast groups for live tracking + SOS) | `CHANNEL_LAYER_REDIS_URL` |

In production, these can point at the same Redis host (as in local dev)
or be split across separate Redis instances/clusters if isolation or
independent scaling is needed — the app only cares that each setting
resolves to a valid Redis URL.

## CORS

The Admin Panel is a separate frontend origin. `CORS_ALLOWED_ORIGINS` is
driven by the `ALLOWED_ADMIN_PANEL_ORIGIN` env var (comma-separated if
there's more than one production origin) — `CORS_ALLOW_ALL_ORIGINS` is
never set. When `DEBUG=True`, common localhost dev-server origins
(`localhost`/`127.0.0.1` on ports 3000 and 5173) are appended
automatically.

## Running tests

```
python manage.py test
```
