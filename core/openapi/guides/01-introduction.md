## What this API does

The MOB Delivery API lets a company **book deliveries and follow them to the door**, and powers the **driver app** that carries them out.

You describe a delivery - a pickup, a drop, what you are sending, how it is paid - and the platform prices it, finds the nearest available driver, and
guides that driver through pickup, delivery, **cash collection by Razorpay QR** and a customer OTP hand-over. You can read the state of every delivery at any time,
including who delivered which item and a photo of it.

## Who is this documentation for?

| You are... | You use | Start with |
|---|---|---|
| **A developer integrating a company's systems** (order management, e-commerce, ERP) | The **Company API** with an *API client* token | *Authentication*, then *Booking a delivery* |
| **Building the company's admin panel** | The **Company API** with an *admin* token - plus driver management and KYC review | *Authentication*, then *Driver onboarding & wallet* |
| **Building or maintaining the driver mobile app** | The **Driver app API** with a *driver* token | *Driver app walkthrough* |

## How the docs are organised

The documentation is split into five groups (the left-hand menu in the reference view, the sections in Swagger UI):

- **Start here** - this page, how to sign in, the conventions every endpoint follows, and the complete list of error codes.
- **Guides** - the ideas that span several endpoints: the trip lifecycle, end-to-end walkthroughs, payments, items and invoices, onboarding and the wallet.
- **Company API** - every endpoint for companies, grouped by topic.
- **Driver app API** - every endpoint the driver app calls.
- **Webhooks** - what Razorpay calls on our side.

Every endpoint page tells you **who may call it, which fields are required and which are optional, what comes back, and every error it can return** - with real example
payloads and copy-paste **cURL, Python and JavaScript** samples. Fields marked *required* must be sent; everything else can be left out.

## The base URL

All endpoints live under one versioned prefix:

```
{your-server}/api/v1
```

For example `POST {your-server}/api/v1/trips`. The paths in this documentation are shown **relative to that prefix** (`/trips`), and the server picker
in the interactive console fills the prefix in for you. In the code samples, `BASE_URL` means `{your-server}/api/v1`.

```bash
export BASE_URL="https://your-server.example.com/api/v1"
```

## Try it in five calls

This is the shortest path from nothing to a booked delivery. Each step is explained in *Booking a delivery*.

```bash
# 1. Sign in (server-to-server) - you get a token valid for 60 minutes
curl -X POST "$BASE_URL/auth/client-token" -H "Content-Type: application/json" \
  -d '{"client_id": "<your client id>", "client_secret": "<your client secret>"}'
export ACCESS_TOKEN="<the access value from the response>"

# 2. Price the delivery for every vehicle type you offer
curl -X POST "$BASE_URL/trips/estimate" -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"pickup": {"address": "MG Road Metro, Bengaluru", "lat": "12.975000", "lng": "77.605000"},
       "drop":   {"address": "12 Indiranagar 100ft Road, Bengaluru", "lat": "12.978300", "lng": "77.640800"}}'

# 3. Book it with the vehicle type the customer picked
curl -X POST "$BASE_URL/trips" -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"vehicle_type_id": "<id from step 2>", "payment_mode": "prepaid",
       "pickup": {"address": "MG Road Metro, Bengaluru", "lat": "12.975000", "lng": "77.605000"},
       "drop":   {"address": "12 Indiranagar 100ft Road, Bengaluru", "lat": "12.978300", "lng": "77.640800"}}'

# 4. Follow it (look at "status")
curl "$BASE_URL/trips/<id from step 3>" -H "Authorization: Bearer $ACCESS_TOKEN"

# 5. Cancel it, if you must (until the driver has started the delivery)
curl -X POST "$BASE_URL/trips/<id>/cancel" -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"reason": "Customer cancelled the order"}'
```

## Two ways to explore

- **Swagger UI** (`/api/docs/`) - an interactive console. Click **Authorize**, paste a token, and press **Try it out** on any endpoint to make real calls.
- **This reference** (`/api/redoc/`) - the same content laid out for reading, with the menu and the request/response examples side by side.
- **The OpenAPI file** (`/api/schema/`) - the machine-readable description, to generate a client library or import into Postman/Insomnia.

## Good to know before you start

- **Everything is scoped to one company.** A token belongs to a company and only ever sees that company's data. Asking for another company's trip is a plain `404`.
- **There are no push notifications or webhooks to your systems yet.** To follow a delivery, poll `GET /trips/{id}` (every 5-15 seconds is plenty).
  The only webhook in this API is the one Razorpay calls on us (see *Payments (Razorpay)*).
- **Booking has no idempotency key.** If a booking request times out, list your trips before retrying so you don't book the delivery twice.
