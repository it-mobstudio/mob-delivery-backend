"""The API documentation.

How it fits together
--------------------
* docs/guides/*.md            the prose pages (introduction, auth, lifecycle, ...)
* core/openapi/errors.py      every error code - the one catalogue
* core/openapi/operations/    one module per area: what each endpoint does,
                              its fields' requirements, examples and errors
* core/openapi/examples.json  real request/response bodies captured from the API
* core/openapi/hooks.py       menu + code samples, applied when the schema is built
* core/test_openapi.py        fails if an endpoint, error code or example is
                              missing or has drifted from the code

Views stay free of documentation: register() decorates them at start-up.
"""

_registered = False


def register():
    global _registered
    if _registered:
        return
    _registered = True
    from . import operations  # noqa: F401  (importing applies the decorators)
