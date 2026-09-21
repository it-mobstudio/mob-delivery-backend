## What you will build

An end-to-end integration for a company's backend: authenticate, price a delivery, book it (with an invoice and an item list the driver must verify, paid on delivery), follow it, and read the result.
Every step shows the real request and a trimmed real response. Set these once:

```bash
export BASE_URL="https://your-server.example.com/api/v1"
```

## 1. Get a token

```bash
curl -X POST "$BASE_URL/auth/client-token" -H "Content-Type: application/json" \
  -d '{"client_id": "0b1c3a52-6f0e-4d3e-9a55-1f2b7c9d4e10", "client_secret": "<secret>"}'
```
```json
{ "access": "eyJhbGciOiJIUzI1NiIs...", "expires_in": 3600 }
```
```bash
export ACCESS_TOKEN="eyJhbGciOiJIUzI1NiIs..."
```

Cache it for `expires_in` seconds. On a `401 TOKEN_NOT_VALID` fetch a new one and retry. (*Authentication* has the details.)

## 2. See what you can offer (optional)

```bash
curl "$BASE_URL/vehicle-types" -H "Authorization: Bearer $ACCESS_TOKEN"
```

The vehicle types are yours (Bike, Tempo, ...). Each has a **fare card**. A type with `min_fare` of `0` isn't priced yet and can't be booked.

## 3. Price the delivery

```bash
curl -X POST "$BASE_URL/trips/estimate" -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" -d '{
  "pickup": {"address": "MG Road Metro Station, Bengaluru", "lat": "12.975000", "lng": "77.605000"},
  "drop":   {"address": "12 Indiranagar 100ft Road, Bengaluru", "lat": "12.978300", "lng": "77.640800"}
}'
```
```json
{ "estimates": [
  { "vehicle_type_id": "725d6bae-1bdc-4a7a-ad03-8ef8ae0c6f13", "vehicle_type_name": "Bike", "category": "two_wheeler",
    "distance_meters": 4200, "duration_seconds": 780, "base_fare": 30.0, "distance_fare": 42.0, "time_fare": 13.0,
    "surge_multiplier": 1.0, "total_fare": 85.0, "currency": "INR", "polyline_precision": 6, "route_polyline": "ox|vWogs_sC..." },
  { "vehicle_type_id": "48def33b-8d32-4f13-9a73-fda7482a5d3a", "vehicle_type_name": "Tempo", "total_fare": 129.9, "...": "..." }
] }
```

Show these as choices. Nothing is stored. The `vehicle_type_id` the customer picks goes into the booking.

## 4. Upload the invoice (optional)

If you have the invoice as a file, upload it to get a URL (or skip this and pass a link you host yourself):

```bash
curl -X POST "$BASE_URL/uploads" -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "purpose=trip_invoice" -F "file=@/path/to/INV-1001.pdf"
```
```json
{ "url": "https://your-server.example.com/media/3b79.../trip-invoices/525c9e10-....pdf" }
```

## 5. Book it

A **cash-on-delivery** order with an invoice and two items the driver must verify:

```bash
curl -X POST "$BASE_URL/trips" -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" -d '{
  "vehicle_type_id": "725d6bae-1bdc-4a7a-ad03-8ef8ae0c6f13",
  "payment_mode": "cod",
  "reference_id": "ORD-20260921-0042",
  "pickup": {"address": "MG Road Metro Station, Bengaluru", "lat": "12.975000", "lng": "77.605000",
             "contact_name": "Acme Hardware", "contact_phone": "+919888800001"},
  "drop":   {"address": "12 Indiranagar 100ft Road, Bengaluru", "lat": "12.978300", "lng": "77.640800",
             "contact_name": "Asha Menon", "contact_phone": "+919888800002"},
  "invoice_url": "https://your-server.example.com/media/3b79.../trip-invoices/525c9e10-....pdf",
  "invoice_number": "INV-1001",
  "verify_items": true,
  "items": [
    {"name": "Cement bag 50 kg", "quantity": 4, "unit": "bags", "sku": "CEM-50", "unit_price": "380.00", "notes": "Keep dry"},
    {"name": "TMT bar 12 mm",    "quantity": 20, "unit": "pcs",  "sku": "TMT-12", "unit_price": "95.00"}
  ]
}'
```

**Required:** `vehicle_type_id`, `payment_mode`, `pickup` and `drop` (each with `address`, `lat`, `lng`). **Also required here:** the drop's `contact_phone`, because a cash-on-delivery trip texts the delivery OTP to it.
Everything else - `reference_id`, `invoice_*`, `verify_items`, `items` - is optional (`verify_items` needs at least one item).

```json
{
  "id": "5920b157-27b4-43c2-a3a9-8d9fd7f0e3db",
  "status": "assigned",
  "reference_id": "ORD-20260921-0042",
  "driver": { "id": "bd316e30-...", "full_name": "Ravi Kumar", "phone_number": "+919876543210" },
  "vehicle": { "id": "34dbaab9-...", "registration_number": "KA01AB1234" },
  "total_fare": "85.00", "currency": "INR",
  "payment_mode": "cod", "payment_status": "pending",
  "verify_items": true,
  "items": [ { "id": "d3035b1c-...", "name": "Cement bag 50 kg", "quantity": 4, "status": "pending", "proof_image_url": null }, "..." ],
  "assigned_at": "2026-09-21T09:41:05.554924Z"
}
```

**Check `status`.** `assigned` means a driver has it. `no_driver_available` means nobody qualified right now - see step 7. Save the `id`.

## 6. Follow it

```bash
curl "$BASE_URL/trips/5920b157-27b4-43c2-a3a9-8d9fd7f0e3db" -H "Authorization: Bearer $ACCESS_TOKEN"
```

Poll every 5-15 seconds and switch on `status` (see *Trip lifecycle*): `assigned` -> `arrived_at_pickup` -> `in_progress` -> `completed`. For a customer-facing tracker show `driver.full_name`,
`driver.phone_number` and `vehicle.registration_number` once `assigned`.

To list many at once: `GET /trips?status=in_progress&page_size=50`.

## 7. If no driver was found

`status: "no_driver_available"`. Wait a moment (a driver may come online) and retry:

```bash
curl -X POST "$BASE_URL/trips/5920b157-27b4-43c2-a3a9-8d9fd7f0e3db/assign" -H "Authorization: Bearer $ACCESS_TOKEN"
```

The answer is the trip again - `assigned` or still `no_driver_available`. Not finding anyone is not an error. Give up with a cancel when you've waited long enough.

## 8. Cancel

```bash
curl -X POST "$BASE_URL/trips/5920b157-27b4-43c2-a3a9-8d9fd7f0e3db/cancel" -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" -d '{"reason": "Customer cancelled the order"}'
```

Allowed until the driver starts the delivery; after that you get `409 TRIP_NOT_CANCELLABLE`.

## 9. Read the outcome

When `status` is `completed`, `GET /trips/{id}` gives you everything to close the order in your system:

- `completed_at`, `payment_status: "paid"`, `cod_collected_at` and **`payment_reference`** (Razorpay's `pay_...` id, to reconcile against your Razorpay dashboard).
- For each `items[]`: `status` (`delivered` / `not_delivered`), `verified_at`, `proof_image_url` (the driver's photo) and `driver_note` - your delivery history.

## Handling failures

| You get | Meaning | Do |
|---|---|---|
| `400 INVALID` | A field is missing or wrong; `error.details` names each. | Fix the fields, resend. |
| `404 VEHICLE_TYPE_NOT_FOUND` | The vehicle type isn't an active one of yours. | List `GET /vehicle-types`. |
| `422 FARE_NOT_CONFIGURED` | The vehicle type has no fare card. | Set its `min_fare` above 0. |
| `503 ROUTING_UNAVAILABLE` | The route couldn't be computed. | Check coordinates; retry shortly. |
| `401 TOKEN_NOT_VALID` | Token expired. | Get a new one, retry once. |
| *timeout* on booking | It may have been booked. | `GET /trips` and look for your `reference_id` among the newest trips before retrying. |

The full list is in *Errors*.
