#!/usr/bin/env python3
"""A local stand-in for Razorpay's QR Codes API — for developing and demoing the
COD payment flow without Razorpay keys. NOT Razorpay: it implements only the
calls trips/payments.py makes, from Razorpay's public API documentation, so it
proves our plumbing (auth header, request bodies, paise, webhook signature) —
not that the real service accepts them. Try that with your Razorpay TEST keys.

    python scripts/dev_razorpay_stub.py            # listens on 127.0.0.1:8003

Point the backend at it (.env):

    PAYMENT_PROVIDER=razorpay
    RAZORPAY_KEY_ID=rzp_test_stub
    RAZORPAY_KEY_SECRET=stub_secret
    RAZORPAY_WEBHOOK_SECRET=stub_webhook_secret
    RAZORPAY_API_BASE=http://127.0.0.1:8003/v1

Then, once the driver's payment screen is showing a code, "pay" it:

    curl -X POST http://127.0.0.1:8003/simulate/<qr id>              # pays + sends the webhook
    curl -X POST "http://127.0.0.1:8003/simulate/<qr id>?webhook=0"  # pays, but sends NO webhook
                                                                      # (the driver's "check" finds it)
    curl -X POST "http://127.0.0.1:8003/simulate/<qr id>?amount=100" # pays 100 paise: too little

`GET http://127.0.0.1:8003/` lists the codes it has issued, with these links.
Environment: STUB_PORT, STUB_KEY_ID, STUB_KEY_SECRET, STUB_WEBHOOK_SECRET,
STUB_WEBHOOK_URL (default http://127.0.0.1:8000/api/v1/webhooks/razorpay).
"""

import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("STUB_PORT", "8003"))
KEY_ID = os.environ.get("STUB_KEY_ID", "rzp_test_stub")
KEY_SECRET = os.environ.get("STUB_KEY_SECRET", "stub_secret")
WEBHOOK_SECRET = os.environ.get("STUB_WEBHOOK_SECRET", "stub_webhook_secret")
WEBHOOK_URL = os.environ.get("STUB_WEBHOOK_URL", "http://127.0.0.1:8000/api/v1/webhooks/razorpay")

QR_CODES = {}  # id -> the code, as Razorpay would return it
PAYMENTS = {}  # id -> [payments]


def _uid(prefix):
    return f"{prefix}_{secrets.token_urlsafe(9).replace('-', 'x').replace('_', 'y')[:14]}"


def _png(text_lines):
    """A placeholder 'QR' image: it's obviously fake, and it says what it's for."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:  # 1x1 transparent PNG
        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        )
    image = Image.new("RGB", (360, 360), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 349, 349), outline=(16, 42, 67), width=6)
    for i, line in enumerate(text_lines):
        draw.text((180, 120 + i * 36), line, fill=(16, 42, 67), font=ImageFont.load_default(size=22 if i == 0 else 17), anchor="mm")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _post_webhook(event):
    body = json.dumps(event).encode()
    signature = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        WEBHOOK_URL, data=body, method="POST", headers={"Content-Type": "application/json", "X-Razorpay-Signature": signature}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()
    except OSError as error:
        return 0, str(error)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[razorpay-stub] {self.address_string()} {fmt % args}", flush=True)

    # -- helpers ---------------------------------------------------------------
    def _send(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self):
        header = self.headers.get("Authorization", "")
        expected = "Basic " + base64.b64encode(f"{KEY_ID}:{KEY_SECRET}".encode()).decode()
        if hmac.compare_digest(header, expected):
            return True
        self._send(401, {"error": {"code": "BAD_REQUEST_ERROR", "description": "Authentication failed"}})
        return False

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    # -- routes --------------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            rows = "".join(
                f"<li><b>{qr['id']}</b> — ₹{qr['payment_amount'] / 100:.2f} — {qr['status']} — "
                f"<code>curl -X POST http://127.0.0.1:{PORT}/simulate/{qr['id']}</code></li>"
                for qr in QR_CODES.values()
            )
            return self._send(200, f"<h3>Razorpay stub</h3><ul>{rows or '<li>no codes yet</li>'}</ul>".encode(), "text/html")
        if path.startswith("/qr/") and path.endswith(".png"):
            qr = QR_CODES.get(path[4:-4])
            if not qr:
                return self._send(404, {"error": "unknown code"})
            lines = ["STUB QR", f"Rs {qr['payment_amount'] / 100:.2f}", qr["status"].upper(), "pay via /simulate"]
            return self._send(200, _png(lines), "image/png")
        if not self._authorised():
            return
        parts = path.strip("/").split("/")  # v1 payments qr_codes <id> [payments]
        if parts[:3] == ["v1", "payments", "qr_codes"] and len(parts) >= 4:
            qr_id = parts[3]
            if qr_id not in QR_CODES:
                return self._send(400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "The id provided does not exist"}})
            if len(parts) == 5 and parts[4] == "payments":
                items = PAYMENTS[qr_id]
                return self._send(200, {"entity": "collection", "count": len(items), "items": items})
            return self._send(200, QR_CODES[qr_id])
        self._send(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")

        if parts[:1] == ["simulate"] and len(parts) == 2:
            return self._simulate(parts[1], parse_qs(parsed.query))

        if not self._authorised():
            return
        if parts == ["v1", "payments", "qr_codes"]:
            body = self._json_body()
            problems = []
            if body.get("type") not in ("upi_qr", "bharat_qr"):
                problems.append("type must be upi_qr or bharat_qr")
            if body.get("fixed_amount") and int(body.get("payment_amount") or 0) < 100:
                problems.append("payment_amount must be at least 100 paise")
            if body.get("close_by") and int(body["close_by"]) < time.time() + 100:
                problems.append("close_by must be at least 2 minutes from now")
            if problems:
                return self._send(400, {"error": {"code": "BAD_REQUEST_ERROR", "description": "; ".join(problems)}})
            qr_id = _uid("qr")
            QR_CODES[qr_id] = {
                "id": qr_id,
                "entity": "qr_code",
                "created_at": int(time.time()),
                "name": body.get("name"),
                "usage": body.get("usage", "multiple_use"),
                "type": body["type"],
                "image_url": f"http://127.0.0.1:{PORT}/qr/{qr_id}.png",
                "payment_amount": body.get("payment_amount"),
                "status": "active",
                "description": body.get("description"),
                "fixed_amount": bool(body.get("fixed_amount")),
                "payments_amount_received": 0,
                "payments_count_received": 0,
                "notes": body.get("notes", {}),
                "close_by": body.get("close_by"),
            }
            PAYMENTS[qr_id] = []
            print(f"[razorpay-stub] created {qr_id} for {body.get('payment_amount')} paise, notes={body.get('notes')}", flush=True)
            return self._send(200, QR_CODES[qr_id])
        self._send(404, {"error": "not found"})

    def _simulate(self, qr_id, query):
        qr = QR_CODES.get(qr_id)
        if qr is None:
            return self._send(404, {"error": f"no such code: {qr_id}"})
        paise = int(query.get("amount", [qr["payment_amount"]])[0])
        payment = {
            "id": _uid("pay"),
            "entity": "payment",
            "amount": paise,
            "currency": "INR",
            "status": "captured",
            "method": "upi",
            "captured": True,
            "notes": qr["notes"],
        }
        PAYMENTS[qr_id].append(payment)
        qr["payments_amount_received"] += paise
        qr["payments_count_received"] += 1
        if qr["usage"] == "single_use" and paise >= (qr["payment_amount"] or 0):
            qr["status"] = "closed"
        result = {"paid": payment["id"], "amount": paise, "webhook": None}
        if query.get("webhook", ["1"])[0] != "0":
            event = {
                "entity": "event",
                "event": "qr_code.credited",
                "contains": ["qr_code", "payment"],
                "payload": {"qr_code": {"entity": qr}, "payment": {"entity": payment}},
                "created_at": int(time.time()),
            }
            status, answer = _post_webhook(event)
            result["webhook"] = {"status": status, "answer": answer}
        print(f"[razorpay-stub] simulated payment {payment['id']} of {paise} paise on {qr_id}: {result['webhook']}", flush=True)
        self._send(200, result)


if __name__ == "__main__":
    print(f"Razorpay stub on http://127.0.0.1:{PORT}  (keys {KEY_ID} / {KEY_SECRET}; webhook → {WEBHOOK_URL})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
