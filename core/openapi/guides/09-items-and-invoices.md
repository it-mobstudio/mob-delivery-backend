## What you can attach to a trip

When you book a trip you may describe the goods and attach the invoice. Both are optional, and both travel with the trip to the driver's app.

| You send | The driver gets |
|---|---|
| `invoice_url` (+ `invoice_number`) | An **invoice card** with a **Download** button, and buttons to **share it on WhatsApp** or via the phone's share sheet - so the driver can hand the customer their invoice. |
| `items[]` | A list of what is being delivered (name, quantity, unit, SKU, notes, picture). |
| `verify_items: true` | A **checklist**: at the drop the driver must answer for **every item** - and can take a photo of each. |

## The invoice

- Pass **`invoice_url`** - a link to a PDF or image, `http(s)` only. Either **host it yourself**, or **upload it** with `POST /uploads` (`purpose: trip_invoice`, image or PDF) and pass the URL that comes back.
- `invoice_number` is the number printed on it (shown on the card).
- The URL is returned on the trip as an **absolute URL** so the driver's phone can open it as is.

## Items

Each entry in `items` (at most 100 per trip):

| Field | Required | Notes |
|---|:-:|---|
| `name` | **yes** | What it is, up to 200 characters. |
| `quantity` | no | Whole number 1 - 1,000,000. Default `1`. |
| `unit` | no | Free text: `pcs`, `kg`, `box`, `bags`. |
| `sku` | no | Your product code. |
| `notes` | no | A handling note: "Keep dry", "Fragile". |
| `image_url` | no | A picture of the item, `http(s)`. Upload one with `purpose: trip_item_image`. |
| `unit_price` | no | Decimal, for the driver's information only - it does not change the fare. |

Items keep the order you sent them (`position`).

## Verification: proof of what was delivered

Set **`verify_items: true`** (with at least one item) to make the driver *confirm the delivery of each line*. That confirmation is stored as your **delivery history**.

**At the drop** (once the trip is `in_progress`), for each item the driver chooses:

- **Delivered** - optionally with a **photo** taken on the spot with the phone's camera (the app only accepts fresh camera shots as proof, not gallery pictures), or
- **Not delivered** - with a **note saying what happened** (required), optionally with a photo.

A wrong tap can be undone (the item goes back to `pending`, and its note and photo are dropped) until the trip is completed. Answering again replaces the earlier answer.

**Until every item has an answer, the trip cannot be paid or completed.** Asking for the payment QR, confirming the payment, or completing all fail with `409 ITEMS_NOT_VERIFIED`. "Not delivered" is an answer - it unblocks the trip - so
a damaged or refused item doesn't strand the delivery.

### What you can read afterwards

`GET /trips/{id}` returns each item with the driver's answer:

```json
{
  "id": "d3035b1c-78ce-44a0-b408-1d1c46c7916f",
  "position": 0,
  "name": "Cement bag 50 kg",
  "quantity": 4,
  "status": "delivered",
  "verified_at": "2026-09-21T10:04:12.310000Z",
  "proof_image_url": "https://your-server/media/.../delivery-proofs/9c1e....jpg",
  "driver_note": ""
}
```

| Field | Meaning |
|---|---|
| `status` | `pending` (not yet answered), `delivered` or `not_delivered`. |
| `verified_at` | When the driver answered. `null` while pending. |
| `proof_image_url` | The driver's photo, or `null`. |
| `driver_note` | What the driver wrote (always present for `not_delivered`). |

## The endpoints

| Purpose | Endpoint |
|---|---|
| Upload an invoice / item picture | `POST /uploads` |
| Book with `invoice_url`, `items`, `verify_items` | `POST /trips` |
| Read the delivery history | `GET /trips/{id}` |
| The driver answers an item | `POST /driver/trips/{id}/items/{item_id}/verify` (multipart) |
| The driver takes an answer back | `DELETE /driver/trips/{id}/items/{item_id}/verify` |

## Rules at a glance

- `verify_items` needs at least one item, or booking fails with `INVALID`.
- Verification happens **only while the trip is `in_progress`** (`TRIP_NOT_IN_PROGRESS` otherwise), and only on orders booked with `verify_items` (`VERIFICATION_NOT_REQUESTED` otherwise).
- Photos must be images (`jpg`, `jpeg`, `png`, `webp`), up to 10 MB, and are checked by content.
- The driver only ever sees their own trips' items; another driver's trip is a `404`.
