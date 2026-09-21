"""Reading the CORS allow-list from the environment (kept free of Django imports
so settings can use it and tests can call it directly)."""

from urllib.parse import urlsplit


def parse_origins(values):
    """`https://app.example.com, https://other.example.com/` -> exact origins.

    An origin is scheme + host (+ port) and nothing else: browsers never send a
    trailing slash or a path, so those are trimmed for you. A wildcard is refused
    outright - this list is the whole point of CORS, and "everyone" isn't an
    origin."""
    origins = []
    for raw in values:
        value = raw.strip().rstrip("/")
        if not value:
            continue
        if "*" in value:
            raise ValueError(
                f"CORS_ALLOWED_ORIGINS takes exact origins like https://app.example.com, not {raw!r}."
            )
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.path or parts.query or parts.fragment:
            raise ValueError(
                f"CORS_ALLOWED_ORIGINS entry {raw!r} isn't an origin: use scheme://host[:port] (e.g. https://app.example.com)."
            )
        origins.append(f"{parts.scheme}://{parts.netloc}".lower())
    return origins
