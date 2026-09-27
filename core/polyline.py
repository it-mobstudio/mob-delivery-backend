"""Decoding the encoded polylines Valhalla returns (Google's format, at any
precision — Valhalla uses 6)."""


def decode(encoded, precision=6):
    """[(lat, lng), ...] from an encoded polyline string."""
    factor = 10 ** precision
    points, index, lat, lng = [], 0, 0, 0
    length = len(encoded)
    while index < length:
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lng += deltas[1]
        points.append((lat / factor, lng / factor))
    return points


def encode(points, precision=6):
    """An encoded polyline from [(lat, lng), ...] — the inverse of decode."""
    factor = 10 ** precision
    out, prev_lat, prev_lng = [], 0, 0
    for lat, lng in points:
        lat_i, lng_i = round(lat * factor), round(lng * factor)
        for delta in (lat_i - prev_lat, lng_i - prev_lng):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev_lat, prev_lng = lat_i, lng_i
    return "".join(out)
