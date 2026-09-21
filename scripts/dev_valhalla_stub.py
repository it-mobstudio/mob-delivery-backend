#!/usr/bin/env python3
"""A stand-in for the Valhalla routing engine, for local development only.

The backend asks Valhalla (VALHALLA_URL, default http://localhost:8002) for a
route whenever a trip is booked. The real one (`docker compose up -d valhalla`)
downloads and builds a map extract — minutes of setup, and the bundled extract
only covers Bengaluru. This answers the same `POST /route` request instantly,
anywhere in the world, with a gently curving polyline between the two points, a
distance of straight-line × 1.3, and a time at 25 km/h.

It is NOT a router: the line ignores roads. Use it to see the driver app's map,
route line and trip flow; use real Valhalla for anything that depends on actual
roads or ETAs.

    python scripts/dev_valhalla_stub.py            # listens on 127.0.0.1:8002
    python scripts/dev_valhalla_stub.py 9000       # or another port
"""

import json
import math
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


def encode_polyline(points, precision=6):
    """Google/Valhalla encoded polyline (Valhalla uses 6 decimal places)."""
    factor = 10**precision
    out, prev_lat, prev_lng = [], 0, 0
    for lat, lng in points:
        lat_i, lng_i = round(lat * factor), round(lng * factor)
        for delta in (lat_i - prev_lat, lng_i - prev_lng):
            value = ~(delta << 1) if delta < 0 else (delta << 1)
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev_lat, prev_lng = lat_i, lng_i
    return "".join(out)


def haversine_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def curved_path(a, b, segments=24):
    """A → B with a sideways bulge in the middle, so it reads as a route, not a ruler."""
    d_lat, d_lng = b[0] - a[0], b[1] - a[1]
    length = math.hypot(d_lat, d_lng) or 1e-9
    normal = (-d_lng / length, d_lat / length)  # unit vector perpendicular to A→B
    bulge = length * 0.12
    points = []
    for i in range(segments + 1):
        t = i / segments
        offset = math.sin(math.pi * t) * bulge
        points.append((a[0] + d_lat * t + normal[0] * offset, a[1] + d_lng * t + normal[1] * offset))
    return points


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path.rstrip("/") != "/route":
            return self._send(404, {"error": "only POST /route is stubbed"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        origin, dest = body["locations"][0], body["locations"][1]
        a, b = (origin["lat"], origin["lon"]), (dest["lat"], dest["lon"])
        km = haversine_km(a, b) * 1.3
        self._send(200, {"trip": {"summary": {"length": km, "time": km / 25 * 3600}, "legs": [{"shape": encode_polyline(curved_path(a, b))}]}})

    def do_GET(self):  # /status, so a health check (or curl) has something to hit
        self._send(200, {"stub": True})

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8002
    print(f"Valhalla stand-in on http://127.0.0.1:{port} — straight-ish lines, not real roads. Ctrl-C to stop.")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
