"""File uploads (company side) and the Razorpay webhook."""

from drf_spectacular.utils import OpenApiParameter

from core.openapi.dsl import COMPANY, PUBLIC, doc, document, ex, ok, raw_ex
from core.openapi.serializers import RazorpayEventSerializer, WebhookResultSerializer
from core.serializers import UploadResultSerializer, UploadSerializer
from core.views import UploadView
from trips.views import RazorpayWebhookView

document(
    UploadView,
    post=doc(
        id="uploadFile",
        tag="Uploads",
        summary="Upload a file",
        description="""
Stores one file under your company and returns its **absolute URL**. Use it wherever another request wants a file URL: `invoice_url` and an item's `image_url` when
booking a trip, a vehicle type's `icon_image_url`, a vehicle's `photo_url`, a vehicle document's `file_url`.

Send as **`multipart/form-data`** with two parts: `file` and `purpose`. `purpose` decides where the file is kept and what is accepted:

| `purpose` | Use it for | Accepted |
|---|---|---|
| `trip_invoice` | the invoice for a trip (`invoice_url`) | image or PDF |
| `trip_item_image` | a picture of an item (`items[].image_url`) | image |
| `vehicle_type_icon` | a vehicle type's icon | image |
| `vehicle_photo` | a vehicle's photo | image |
| `vehicle_document` | RC, insurance, ... | image or PDF |
| `driver_document` | a driver's paperwork | image or PDF |
| `driver_photo` | a driver's picture | image |
| `delivery_proof` | proof-of-delivery photos | image |

Images are `jpg`, `jpeg`, `png` or `webp`. The **content is checked** - a renamed text file, a corrupt image or a fake PDF is refused - and the size limit is 10 MB.
""",
        auth=COMPANY,
        request={"multipart/form-data": UploadSerializer},
        responses={201: ok(UploadResultSerializer, ex("upload.invoice", "Stored"))},
        errors=["INVALID_UPLOAD"],
    ),
)

SIGNATURE = OpenApiParameter(
    "X-Razorpay-Signature",
    type=str,
    location=OpenApiParameter.HEADER,
    required=True,
    description="Hex HMAC-SHA256 of the **raw request body**, keyed with the webhook secret. Razorpay adds it; you only need it to test by hand.",
)

QR_CREDITED = {
    "entity": "event",
    "event": "qr_code.credited",
    "contains": ["qr_code", "payment"],
    "payload": {
        "qr_code": {"entity": {"id": "qr_Nx4a1Kp0aB9cXz", "entity": "qr_code", "status": "closed", "payments_amount_received": 8500}},
        "payment": {"entity": {"id": "pay_Nx4b2Lq1cC0dYa", "entity": "payment", "amount": 8500, "currency": "INR", "status": "captured", "method": "upi"}},
    },
    "created_at": 1789971822,
}

document(
    RazorpayWebhookView,
    post=doc(
        id="razorpayWebhook",
        tag="Razorpay webhook",
        summary="Razorpay reports a payment",
        description="""
**You do not call this - Razorpay does.** When a customer pays a trip's QR code, Razorpay sends a `qr_code.credited` event here; the server finds the trip that owns
that code and, if the payment covers the fare, marks it **paid** and texts the customer their delivery OTP. That is what lets the driver's payment screen move on by itself.

**Set it up** in the Razorpay Dashboard -> Settings -> Webhooks: URL `https://<your-domain>/api/v1/webhooks/razorpay`, event **QR Code -> `qr_code.credited`**, and a
secret of your choosing, which you also set on the server as `RAZORPAY_WEBHOOK_SECRET`.

**Security.** There is no bearer token - Razorpay can't log in. Every call is authenticated by `X-Razorpay-Signature`: the hex HMAC-SHA256 of the raw body keyed with the webhook secret.
Anything not signed with it is refused (`INVALID_SIGNATURE`), and if the server has no secret it refuses everything (`WEBHOOK_NOT_CONFIGURED`).
Redeliveries are harmless: an already-paid trip is never paid twice.

To test by hand, sign the exact bytes you send:

```python
import hmac, hashlib
signature = hmac.new(SECRET.encode(), body_bytes, hashlib.sha256).hexdigest()
```

Every understood event answers `200` with what was done (so Razorpay stops retrying), even when nothing was marked paid - see `status` below.
""",
        auth=PUBLIC,
        request=RazorpayEventSerializer,
        request_examples=[raw_ex("Razorpay: qr_code.credited", QR_CREDITED, request=True)],
        params=[SIGNATURE],
        responses={
            200: ok(
                WebhookResultSerializer,
                raw_ex("paid", {"status": "paid"}, "The trip is now paid"),
                raw_ex("underpaid", {"status": "underpaid"}, "Paid less than the fare"),
                raw_ex("already_paid", {"status": "already_paid"}, "A repeat delivery"),
            )
        },
        errors=["INVALID_SIGNATURE", "INVALID_PAYLOAD", "WEBHOOK_NOT_CONFIGURED"],
        validates=False,
    ),
)
