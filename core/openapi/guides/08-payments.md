## Two ways a delivery is paid

| `payment_mode` | What it means | What the driver does at the drop |
|---|---|---|
| `prepaid` | You settled the money outside this system. The trip is marked **paid** the moment it is booked. | Nothing to collect - hand over the goods. |
| `cod` | **Cash on delivery.** The customer pays the driver at the drop. | Shows a **Razorpay QR code** for the exact fare; the customer scans it with any UPI app. |

This guide is about the second one: how the QR is made, how the platform finds out it was paid, and how to set it up.

## How a cash-on-delivery payment works

```
 Driver app                     This API                          Razorpay                 Customer
     │  GET .../payment/qr          │                                │                         │
     ├─────────────────────────────▶│  create single-use QR (₹85)    │                         │
     │                              ├───────────────────────────────▶│                         │
     │◀─────────────────────────────┤◀───────────────────────────────┤                         │
     │  image_url, expires_at       │                                │                         │
     │  ── shows the QR ─────────────────────────────────────────────────────────────────────▶ │
     │                              │                                │◀── scans & pays (UPI) ──┤
     │                              │◀── webhook qr_code.credited ───┤                         │
     │                              │  verify signature, check amount │                        │
     │                              │  trip.payment_status = paid     │                         │
     │                              │  SMS delivery OTP ──────────────────────────────────────▶│
     │  poll GET /driver/trips/{id} │                                │                         │
     │◀─ payment_status: paid ──────┤                                │                         │
     │  customer reads OTP ◀──────────────────────────────────────────────────────────────────┤
     │  POST .../complete {otp}     │                                                          │
     ├─────────────────────────────▶│  trip completed, driver credited                         │
```

Key points:

- **The QR is created by Razorpay, per trip, for exactly the trip's fare.** It is *single-use*: once paid it closes, so a screenshot cannot be paid twice, and it is fixed-amount, so a customer cannot pay the wrong sum.
- **Money confirmation comes from Razorpay, never from the driver's word.** The trip is marked paid only when the server has confirmed - through Razorpay's signed webhook, or by asking Razorpay when the driver taps *Check payment* - that a captured payment **covering the full fare** exists for this trip's code.
- **The customer's delivery OTP is texted at that moment.** The driver cannot complete a cash-on-delivery trip without it, so completion cannot happen before payment does.
- **The driver app doesn't have to poll a payments endpoint**: it re-reads the trip and watches `payment_status` (the reference app checks every 3 seconds while the QR is on screen).

## The endpoints involved

| Step | Endpoint | Who |
|---|---|---|
| Show the code | `GET /driver/trips/{id}/payment/qr` | driver app |
| "Check payment" | `POST /driver/trips/{id}/payment/collect` | driver app |
| Resend the OTP | `POST /driver/trips/{id}/delivery-otp/resend` | driver app |
| Finish | `POST /driver/trips/{id}/complete` (with `otp`) | driver app |
| Razorpay tells us | `POST /webhooks/razorpay` | **Razorpay** |
| Read the result | `GET /trips/{id}` -> `payment_status`, `cod_collected_at`, `payment_reference` | company |

## The QR code

`GET /driver/trips/{id}/payment/qr` returns:

```json
{
  "provider": "razorpay",
  "reference": "qr_Nx4a1Kp0aB9cXz",
  "image_url": "https://rzp.io/i/Nx4a1Kp",
  "qr_payload": null,
  "amount": 85.0,
  "currency": "INR",
  "expires_at": "2026-09-21T10:15:00Z"
}
```

- Show **`image_url`** as an image, large and on a white background. It is a ready-made QR image hosted by Razorpay; don't try to draw it yourself.
- **Asking again returns the same code** while it is valid - reopening the screen never creates a second one. After `expires_at` (30 minutes) a fresh code is issued. Show a countdown and a "Get a new code" button.
- If the customer has in fact already paid (the webhook was late), the call answers **`409 ALREADY_PAID`** - go straight to the OTP.
- The code is only issued for a **cash-on-delivery** trip that is **in progress**, and, when the order asks for item verification, only after **every item is answered**.

## "Check payment"

`POST /driver/trips/{id}/payment/collect` asks the server to look at Razorpay *now*:

| Answer | Meaning |
|---|---|
| `200` + message | Paid. The trip is marked paid and the customer has been texted their OTP. |
| `409 PAYMENT_NOT_RECEIVED` | **Not a fault.** No full payment for this code yet. Ask the customer to pay; check again in a moment. |
| `409 ALREADY_PAID` | The webhook already confirmed it. Carry on to the OTP. |
| `503 PAYMENT_PROVIDER_UNAVAILABLE` | Razorpay couldn't be reached. Try again shortly. Nothing was charged. |

## Setting it up (for the person running the server)

1. **Razorpay account.** Use your **Test mode** keys while developing. This uses Razorpay's *QR Codes* product, which is an **on-demand feature**: until Razorpay support activates it on your account (Test and Live are separate), asking for a code fails with `503 PAYMENT_PROVIDER_NOT_CONFIGURED` and a message saying so - even though the keys themselves are valid. Any other refusal comes back as `PAYMENT_PROVIDER_ERROR` with Razorpay's reason.
2. **Server settings** (environment variables):

   | Variable | Value |
   |---|---|
   | `PAYMENT_PROVIDER` | `razorpay` (the default). `upi_static` is a development-only stand-in - see below. |
   | `RAZORPAY_KEY_ID` | Your Razorpay key id (`rzp_test_...` / `rzp_live_...`). |
   | `RAZORPAY_KEY_SECRET` | Its secret. Keep it server-side. |
   | `RAZORPAY_WEBHOOK_SECRET` | The secret you set on the webhook in step 3. |
   | `RAZORPAY_QR_VALID_MINUTES` | How long a code stays payable. Default `30` (Razorpay's minimum is 2). |
   | `RAZORPAY_TIMEOUT_SECONDS` | How long to wait for Razorpay. Default `10`. |

3. **Webhook.** In the Razorpay Dashboard -> Settings -> Webhooks -> *Add new webhook*: URL `https://<your-domain>/api/v1/webhooks/razorpay`, secret = the value of `RAZORPAY_WEBHOOK_SECRET`, and tick the event **QR Code -> `qr_code.credited`**.
4. **Try it in Test mode**: book a cash-on-delivery trip, drive it to `in_progress`, open the QR and pay it from the Razorpay test tools.

Until the keys are set, asking for a code fails with `503 PAYMENT_PROVIDER_NOT_CONFIGURED`, and until the webhook secret is set the webhook answers `503 WEBHOOK_NOT_CONFIGURED`. In that state a driver can still confirm by tapping *Check payment*
- but **without the webhook the payment is only noticed when they tap it**.

### The webhook

`POST /webhooks/razorpay` is called by Razorpay, not by you. Each request is authenticated by the **`X-Razorpay-Signature`** header - the hex HMAC-SHA256 of the raw body keyed with your webhook secret. Anything else is
refused with `400 INVALID_SIGNATURE`. It answers `200` with `{"status": "..."}` for every event it understood, so Razorpay never retries needlessly:

| `status` | Meaning |
|---|---|
| `paid` | The trip is now paid; the OTP was sent. |
| `already_paid` | A repeat delivery of an event already handled. Nothing changed. |
| `underpaid` | The payment was less than the fare. The trip stays unpaid. |
| `trip_not_in_progress` | Money arrived for a trip no longer in progress. Nothing was marked paid - **refund it manually**. |
| `unknown_qr` | No trip owns this code. |
| `ignored` | Some other Razorpay event. |

Redelivery is safe: the same payment can never pay a trip twice, and a trip's OTP is only sent once.

## Edge cases

| Situation | What happens |
|---|---|
| The customer pays **less** than the fare | Not accepted (the code is fixed-amount, so this is rare). The trip stays unpaid; the driver sees `PAYMENT_NOT_RECEIVED`. |
| The code **expires** unpaid | Asking again issues a new one. The old one can no longer be paid. |
| The **webhook is delayed or lost** | The driver taps *Check payment*; the server asks Razorpay directly and finds the payment. |
| **Razorpay is down** | `503 PAYMENT_PROVIDER_UNAVAILABLE` on the QR / check calls. Retry shortly; nothing was charged. |
| The customer's **SMS OTP doesn't arrive** | The driver taps *Resend OTP* (once per 30 s per trip). |
| The customer pays **twice** | The code is single-use and closes after the first payment. |

## Reconciling

Every paid cash-on-delivery trip carries Razorpay's payment id in **`payment_reference`** (`pay_...`) and the time in `cod_collected_at` (both on `GET /trips/{id}`). Match `payment_reference` against
the payments in your Razorpay dashboard; the code's own id (`qr_...`) is in the QR response's `reference`.

## Developing without Razorpay

Two options, neither of which is for production:

- **`scripts/dev_razorpay_stub.py`** - a local stand-in for Razorpay's QR Codes API. Point `RAZORPAY_API_BASE` at it and use fake keys; it can "pay" a code (with or without sending the webhook) so you can exercise the whole flow, including underpayment. It proves *our* plumbing - not that real Razorpay accepts it, so also try Test mode.
- **`PAYMENT_PROVIDER=upi_static`** - the response carries a plain UPI link (`qr_payload`, no `image_url`, no `expires_at`) drawn as a QR against `COMPANY_UPI_VPA`. **Nothing can confirm that a payment happened**, so the driver's "payment received" tap is taken on trust. Never use it with real money.
