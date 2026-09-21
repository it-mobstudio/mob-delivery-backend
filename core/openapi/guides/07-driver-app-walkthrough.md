## A driver's day, in API calls

This is the exact sequence the driver app follows. Set these once (the driver's token comes from step 1):

```bash
export BASE_URL="https://your-server.example.com/api/v1"
export DRIVER_TOKEN="<accessToken from step 1>"
```

## 1. Sign in with a phone number

```bash
curl -X POST "$BASE_URL/driver/auth/otp/request" -H "Content-Type: application/json" -d '{"phone_number": "+919000000001"}'
```
```json
{ "message": "OTP sent to +919000000001." }
```

(A non-production server also returns `"otp": "482913"` so you can test without an SMS gateway.) Then verify the code the driver received:

```bash
curl -X POST "$BASE_URL/driver/auth/otp/verify" -H "Content-Type: application/json" -d '{"phone_number": "+919000000001", "otp": "482913"}'
```
```json
{
  "accessToken": "eyJhbGciOi...", "tokenType": "Bearer", "expiresInSeconds": 3600,
  "refreshToken": "eyJhbGciOi...", "refreshExpiresInSeconds": 2592000,
  "driverName": "Ravi Kumar",
  "driver": { "id": "...", "onboarding_status": "approved", "is_online": false, "...": "..." }
}
```

Store both tokens. **A phone number nobody has registered becomes a brand-new empty driver** (when the server allows self sign-up) - `driver.onboarding_status` will be `profile_incomplete`. Renew before expiry with
`POST /driver/auth/refresh` (send `{"refreshToken": "..."}`, get a **new pair** back - save both).

## 2. Decide the first screen from `onboarding_status`

`GET /driver/me` returns the same profile at any time. Key the app's first screen on `onboarding_status`:

| `onboarding_status` | Show |
|---|---|
| `profile_incomplete`, `documents_required` | The onboarding flow (step 3) |
| `under_review` | "Your documents are with the company" - wait, poll `GET /driver/me` |
| `action_required` | What was rejected (`kyc.aadhar.rejection_note`, ...) and let them re-submit |
| `approved` | The dashboard (step 4) |

## 3. Onboarding (new drivers)

Each step saves on its own, so a driver can leave and come back.

```bash
# About me - every field optional; send what you have
curl -X PATCH "$BASE_URL/driver/me" -H "Authorization: Bearer $DRIVER_TOKEN" -H "Content-Type: application/json" \
  -d '{"full_name": "Ravi Kumar", "date_of_birth": "1994-03-12", "city": "Bengaluru", "pincode": "560001",
       "emergency_contact_name": "Sunita Kumar", "emergency_contact_phone": "+919555500002"}'

# A selfie (multipart)
curl -X POST "$BASE_URL/driver/me/photo" -H "Authorization: Bearer $DRIVER_TOKEN" -F "photo=@selfie.jpg"

# Aadhaar: the number and both sides (multipart)
curl -X POST "$BASE_URL/driver/me/kyc/aadhar" -H "Authorization: Bearer $DRIVER_TOKEN" \
  -F "number=234567890123" -F "front=@aadhaar_front.jpg" -F "back=@aadhaar_back.jpg"

# Driving licence: number, expiry, front (back optional)
curl -X POST "$BASE_URL/driver/me/kyc/dl" -H "Authorization: Bearer $DRIVER_TOKEN" \
  -F "number=KA0120200012345" -F "expiry_date=2031-06-30" -F "front=@licence_front.jpg"

# Police verification certificate (optional to upload)
curl -X POST "$BASE_URL/driver/me/kyc/police" -H "Authorization: Bearer $DRIVER_TOKEN" -F "document=@police.pdf"

# Where to send payouts (UPI or bank)
curl -X PATCH "$BASE_URL/driver/me" -H "Authorization: Bearer $DRIVER_TOKEN" -H "Content-Type: application/json" -d '{"payout_upi_id": "ravi@okhdfc"}'
```

Each upload answers with the refreshed profile. When the driver has completed the profile and provided Aadhaar and licence, `onboarding_status` becomes `under_review`. The **company verifies** the documents
(`PATCH /drivers/{id}/kyc/aadhar`, `/police` and `/dl`, with an admin token) - after which the driver is `approved` and can take trips. If the company rejects a document, `onboarding_status` becomes `action_required` with a `rejection_note`, and the driver re-submits.
More in *Driver onboarding & wallet*.

## 4. Go on duty

```bash
# Which vehicles may I take? (only ones my verified licence covers, that nobody else is using)
curl "$BASE_URL/driver/vehicles" -H "Authorization: Bearer $DRIVER_TOKEN"

# Go online on one, with my current GPS fix so I can be matched straight away
curl -X POST "$BASE_URL/driver/duty/start" -H "Authorization: Bearer $DRIVER_TOKEN" -H "Content-Type: application/json" \
  -d '{"vehicle_id": "34dbaab9-e77f-466a-93f3-3637131968ea", "lat": "12.971600", "lng": "77.594600"}'
```

From now on, send the position **every 15-30 seconds** while on duty:

```bash
curl -X POST "$BASE_URL/driver/location" -H "Authorization: Bearer $DRIVER_TOKEN" -H "Content-Type: application/json" -d '{"lat": "12.971900", "lng": "77.595100"}'
```

Trips are matched only to drivers whose last position is **within 8 km of the pickup**.

## 5. Wait for a trip

There are no push notifications yet, so **poll** every few seconds while idle:

```bash
curl "$BASE_URL/driver/trips/active" -H "Authorization: Bearer $DRIVER_TOKEN"
```
```json
{ "trip": null }
```

When a trip is assigned the body carries the whole trip (`{"trip": {"id": "...", "status": "assigned", ...}}`): addresses, contacts, route, fare, `driver_earning`, invoice and item checklist. Use its `id` below.

## 6. Run the delivery

```bash
TRIP=5920b157-27b4-43c2-a3a9-8d9fd7f0e3db

# The road to the pickup, from where I am now (draw the polyline at 6 decimal places)
curl "$BASE_URL/driver/trips/$TRIP/navigation?lat=12.9719&lng=77.5951" -H "Authorization: Bearer $DRIVER_TOKEN"

# I'm at the pickup
curl -X POST "$BASE_URL/driver/trips/$TRIP/arrive" -H "Authorization: Bearer $DRIVER_TOKEN"

# I have the goods - go (the trip can no longer be cancelled)
curl -X POST "$BASE_URL/driver/trips/$TRIP/start" -H "Authorization: Bearer $DRIVER_TOKEN"
```

Each answers with the updated trip. To give up before starting: `POST /driver/trips/$TRIP/cancel` with `{"reason": "..."}`.

## 7. At the drop

**If the order has `verify_items: true`**, first answer for every item (multipart; a camera photo is optional):

```bash
curl -X POST "$BASE_URL/driver/trips/$TRIP/items/$ITEM/verify" -H "Authorization: Bearer $DRIVER_TOKEN" \
  -F "status=delivered" -F "photo=@proof.jpg"
# not delivered needs a note
curl -X POST "$BASE_URL/driver/trips/$TRIP/items/$OTHER_ITEM/verify" -H "Authorization: Bearer $DRIVER_TOKEN" \
  -F "status=not_delivered" -F "note=Damaged in transit"
```

**If the order is cash on delivery**, collect the money through Razorpay:

```bash
# Show this QR (image_url) to the customer - it is for exactly this trip's fare
curl "$BASE_URL/driver/trips/$TRIP/payment/qr" -H "Authorization: Bearer $DRIVER_TOKEN"
```
```json
{ "provider": "razorpay", "reference": "qr_Nx4a1Kp0aB9cXz", "image_url": "https://rzp.io/i/Nx4a1Kp",
  "amount": 85.0, "currency": "INR", "expires_at": "2026-09-21T10:15:00Z", "qr_payload": null }
```

The customer scans and pays. Razorpay tells the server, which marks the trip **paid** and texts the customer a 6-digit delivery OTP - so the app just **waits** (re-read `GET /driver/trips/$TRIP` and watch `payment_status`).
"Check payment" asks the server to look right now:

```bash
curl -X POST "$BASE_URL/driver/trips/$TRIP/payment/collect" -H "Authorization: Bearer $DRIVER_TOKEN"
# 409 PAYMENT_NOT_RECEIVED  -> not paid yet, wait and check again
# 200 {"message": "Payment collected. An OTP was sent to +919888800002 ..."}
```

If the customer says the SMS didn't arrive: `POST /driver/trips/$TRIP/delivery-otp/resend` (once per 30 seconds).

## 8. Hand over

```bash
# Cash on delivery: the customer tells you the OTP they were texted
curl -X POST "$BASE_URL/driver/trips/$TRIP/complete" -H "Authorization: Bearer $DRIVER_TOKEN" -H "Content-Type: application/json" -d '{"otp": "387406"}'
# Prepaid: no body
curl -X POST "$BASE_URL/driver/trips/$TRIP/complete" -H "Authorization: Bearer $DRIVER_TOKEN"
```

The trip becomes `completed` and the driver's wallet is credited. `driver_earning` on the trip says how much.

## 9. Earnings

```bash
curl "$BASE_URL/driver/wallet?utc_offset_minutes=330" -H "Authorization: Bearer $DRIVER_TOKEN"          # balance, today/week/month, last 7 days
curl "$BASE_URL/driver/wallet/transactions" -H "Authorization: Bearer $DRIVER_TOKEN"                    # the statement
curl "$BASE_URL/driver/stats?utc_offset_minutes=330" -H "Authorization: Bearer $DRIVER_TOKEN"           # today / all-time counts
curl "$BASE_URL/driver/trips?status=completed,cancelled" -H "Authorization: Bearer $DRIVER_TOKEN"       # history
```

## 10. End of shift

```bash
curl -X POST "$BASE_URL/driver/duty/end" -H "Authorization: Bearer $DRIVER_TOKEN"      # refused while a trip is active
curl -X POST "$BASE_URL/driver/auth/logout" -H "Content-Type: application/json" -d '{"refreshToken": "..."}'
```

## Errors the app should handle

| Code | Show / do |
|---|---|
| `TOKEN_NOT_VALID` (401) | Refresh the session; if that fails, back to sign-in. |
| `INVALID_OTP` | "That code is wrong or expired" - offer to resend. |
| `DRIVER_NOT_ELIGIBLE`, `VEHICLE_CATEGORY_NOT_ALLOWED` | Explain what is missing from the driver's verification. |
| `ITEMS_NOT_VERIFIED` | Take the driver to the item checklist. |
| `PAYMENT_NOT_RECEIVED` | Not an error: "Not paid yet" - keep waiting. |
| `INVALID_DELIVERY_OTP` | Let them re-enter, or resend. |
| `INVALID_TRIP_STATUS_TRANSITION`, `TRIP_NOT_CANCELLABLE` | The trip moved on - re-read it and update the screen. |
| `PAYMENT_PROVIDER_UNAVAILABLE` (503) | "Payment service is busy - try again in a moment". |

The complete list with causes is in *Errors*.
