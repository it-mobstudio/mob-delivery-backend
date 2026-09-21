## The states of a trip

A trip moves through a fixed set of states. Its `status` field always says where it is.

```
                                          cancel (company or driver)
                          ┌──────────────────────────────────────────────┐
                          │                                              ▼
 POST /trips ──▶ requested ──▶ assigned ──▶ arrived_at_pickup ──▶ in_progress ──▶ completed
                     │  matching   ▲   driver:       driver:          driver:
                     ▼             │   arrive         start           complete
            no_driver_available ───┘
                     (POST /trips/{id}/assign to retry)

                     cancelled  ◀── from requested, no_driver_available, assigned, arrived_at_pickup
```

| Status | What it means | What moves it on | Can be cancelled? |
|---|---|---|---|
| `requested` | Just created; the server is looking for a driver. You will rarely see it - booking assigns in the same call. | the server | yes |
| `no_driver_available` | Nobody eligible was within reach. The trip exists and can be retried. | you: `POST /trips/{id}/assign` | yes |
| `assigned` | A driver has the trip and is heading to the pickup. | the driver: **arrive** | yes |
| `arrived_at_pickup` | The driver is at the pickup, collecting the goods. | the driver: **start** | yes |
| `in_progress` | The goods are on the way to the drop. | the driver: **complete** (after payment/verification) | **no** |
| `completed` | Delivered. The driver has been credited. | - (final) | no |
| `cancelled` | Cancelled by the company, the driver, or the system. See `cancelled_by` and `cancellation_reason`. | - (final) | no |

A trip can be cancelled **until the driver starts the delivery**. Once it is `in_progress` the only way out is `completed`.

## What happens at each step

**1. You book** (`POST /trips`). The route is calculated and the **fare is fixed** - it will not change later. The server immediately tries to assign the **nearest available driver**. A driver qualifies when they are:

- online (on duty),
- verified (Aadhaar, licence and police all `verified`, licence unexpired, account active),
- on a vehicle of the **vehicle type you booked**, with that vehicle active,
- not already on another trip, and
- within **8 km** of the pickup (using the position from their last GPS ping).

If one qualifies the trip is `assigned` in the same response; if none does it is `no_driver_available` (still a `201`).

**2. The driver reaches the pickup** (`arrive` -> `arrived_at_pickup`, `arrived_at_pickup_at` set).

**3. The driver picks up and sets off** (`start` -> `in_progress`, `started_at` set). Cancellation is no longer possible.

**4. At the drop**, depending on how the order was booked:

- If `verify_items` is on: the driver checks **every item** (delivered / not delivered, optionally with a photo). Nothing below can happen until they have all been answered.
- **Cash on delivery** (`payment_mode: cod`): the driver shows a **Razorpay QR** for the exact fare; the customer pays; the server marks the trip **paid** and texts the customer a **6-digit delivery OTP**. See *Payments (Razorpay)*.
- **Prepaid** (`payment_mode: prepaid`): already paid - there is nothing to collect.

**5. The driver completes** (`complete` -> `completed`, `completed_at` set). For cash on delivery they enter the customer's OTP - proof the goods were handed over. In the same step the **driver's wallet is credited**
with their share of the fare (80% by default).

## Following a trip

There are no webhooks to your systems yet, so **poll `GET /trips/{id}`** every 5-15 seconds and act on `status`:

```text
assigned            -> show "driver on the way" (driver.full_name, driver.phone_number, vehicle.registration_number)
arrived_at_pickup   -> "driver has arrived at the pickup"
in_progress         -> "out for delivery"
completed           -> done: read completed_at, payment_reference, and items[].status / proof_image_url
cancelled           -> read cancelled_by and cancellation_reason
no_driver_available -> offer to retry (POST /trips/{id}/assign) or cancel
```

The timestamps `assigned_at`, `arrived_at_pickup_at`, `started_at` and `completed_at` (and `cancelled_at`) tell you when each stage happened; they are `null` until it does.

## Payment state runs alongside

`payment_status` is independent of `status`:

| `payment_mode` | Starts as | Becomes `paid` when |
|---|---|---|
| `prepaid` | `paid` | (already) - the money was settled outside this system |
| `cod` | `pending` | the customer's **Razorpay** payment is confirmed (`cod_collected_at` and `payment_reference` are then set) |

A cash-on-delivery trip **cannot be completed while `payment_status` is `pending`**.

## Cancelling

| Who | How | Until |
|---|---|---|
| The company | `POST /trips/{id}/cancel` with a `reason` | `arrived_at_pickup` |
| The driver | `POST /driver/trips/{id}/cancel` with a `reason` | `arrived_at_pickup` |

A cancelled trip is final. If the **driver** cancelled, the trip is *not* re-offered to another driver automatically - book a new trip.

A cash-on-delivery payment code is only issued once a trip is `in_progress`, which can no longer be cancelled, so a customer can't pay for a cancelled trip through the normal flow. If money ever does arrive for a
trip that is no longer in progress, the server does **not** mark it paid, records it in its log, and a person has to **refund it from the Razorpay dashboard**.

## Status quick reference for the driver app

| The driver sees... | when `status` is... |
|---|---|
| "Head to pickup" | `assigned` |
| "Collecting the goods" | `arrived_at_pickup` |
| "Head to drop" / "Collect payment" / "Enter OTP" | `in_progress` (which one depends on items, `payment_status` and the OTP) |
| "Delivery completed" | `completed` |
