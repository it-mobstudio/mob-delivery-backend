## Requests and responses

- **JSON everywhere.** Send `Content-Type: application/json` and read JSON back. The exceptions are the **file-upload** endpoints (`POST /uploads`, the driver's document/photo uploads and item verification), which take
  `multipart/form-data`.
- **HTTPS only in production.** Tokens travel in a header; never send them over plain HTTP.
- **Field names are `snake_case`.** (One exception: the driver sign-in responses use `camelCase` - `accessToken`, `refreshToken` - because the driver app was built against that shape.)
- **Unknown fields in a request are ignored**, not rejected. Fields marked *required* must be present; everything else may be omitted.

## Status codes

| Code | Meaning |
|---|---|
| `200` | OK - the body is the result. |
| `201` | Created - the body is the new resource. |
| `204` | Done - there is no body (deletes). |
| `400` | The request was malformed or a field is invalid (`INVALID`, `PARSE_ERROR`, or a specific code). Nothing was changed. |
| `401` | No or unacceptable token. |
| `403` | Signed in, but not allowed. |
| `404` | Not found - or belongs to another company. |
| `409` | The request is well-formed but conflicts with the current state (wrong trip status, already paid, ...). |
| `422` | The request is understood but can't be priced/processed (e.g. `FARE_NOT_CONFIGURED`). |
| `429` | Too soon - wait and retry (delivery OTP resend). |
| `502` / `503` | An upstream service (routing engine, payment provider) is failing. Retry shortly. |

Errors always use the same envelope - see *Errors*.

## Identifiers

Every resource has a **UUID** id (`"5920b157-27b4-43c2-a3a9-8d9fd7f0e3db"`). Treat it as an opaque string. Your own order number goes in a trip's `reference_id`.

## Dates and times

- **Timestamps** are ISO 8601 in **UTC** with a trailing `Z` and microsecond precision: `"2026-09-21T09:41:05.554924Z"`. Parse them as instants; convert to local time for display.
- **Dates** (a licence expiry, a date of birth) are `YYYY-MM-DD`.
- Where a request needs the caller's local day boundary (the driver's "today"), it takes `utc_offset_minutes` - minutes **east** of UTC (India: `330`).

## Money

- The currency is **INR** (`"currency": "INR"`) everywhere.
- On **records** (a trip's fares, a wallet entry) amounts are **decimal strings** with two places: `"85.00"`. Parse them as decimals, not floats.
- In **calculated answers** (`POST /trips/estimate`, the payment QR's `amount`, a payout's balance summary) amounts are JSON **numbers**: `85.0`. Each field's page says which.
- Razorpay itself works in **paise** (1/100 rupee); the API converts for you. You only meet paise in the raw Razorpay webhook body.

## Coordinates

`lat` is -90 to 90 and `lng` is -180 to 180, in decimal degrees, with **up to 6 decimal places**. In JSON you may send them as numbers or as strings; they come back as strings
(`"12.975000"`). Route polylines are encoded at **6 decimal places** (`polyline_precision: 6`), not the 5 used by Google - pass that precision to your decoder or the route will be drawn in the wrong place.

## Phone numbers

International format with the country code: `+919876543210`. The API accepts 10-15 digits with an optional leading `+`, and stores what you send - so send the same form every time
(a driver signs in with the exact number the company registered).

## Pagination

Lists are paginated, **20 per page** by default, newest first.

| Query parameter | Meaning |
|---|---|
| `page` | Page number, starting at 1. |
| `page_size` | Items per page: default 20, **maximum 100**. |

```json
{
  "count": 137,
  "next": "https://your-server/api/v1/trips?page=2",
  "previous": null,
  "results": [ ... ]
}
```

`count` is the total across all pages. Follow `next` until it is `null`.

## Filtering

Where an endpoint supports filters they are plain query parameters listed on its page (`GET /trips?status=assigned&driver=<id>`). Several filters combine with AND.
Comma-separated values (`status=completed,cancelled`) are supported only where the endpoint says so.

## Files and media URLs

- Upload a file with `POST /uploads` (multipart) and pass the **URL it returns** to whatever field wants a file (`invoice_url`, `photo_url`, `file_url`, ...).
- URLs the API returns for stored files are **absolute** (`https://...`) so they open in a browser or the driver's phone as they are. Don't build file URLs yourself.
- Only `http(s)` links are accepted in URL fields.
- Uploads are checked by content, not just the file name, and limited to 10 MB.

## Multi-tenancy

Every token belongs to exactly one company. You can only read and change that company's data, and never need to send a company id. A resource of another company is indistinguishable from one that
doesn't exist: `404 NOT_FOUND`.

## Rate limits and retries

The API applies no global rate limit, but a few calls are deliberately throttled (the delivery OTP can be re-sent once per 30 seconds per trip). Be considerate: poll a trip every 5-15 seconds, not in a tight loop.
A request that fails with `502`/`503` should have had no lasting effect - it is safe to retry it. A request that **times out** may or may not have been processed: for creations (booking), look before you retry.
