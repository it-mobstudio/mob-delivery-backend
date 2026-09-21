"""A small vocabulary for documenting endpoints without cluttering the views.

    from core.openapi.dsl import *

    document(TripViewSet,
        create=doc(
            id="createTrip", tag="Trips", summary="Book a delivery",
            description="...markdown...",
            auth=COMPANY, request=TripCreateSerializer, request_examples=[ex(...)],
            responses={201: ok(TripSerializer, ex("trip.create", "Created and assigned"))},
            errors=["ROUTING_UNAVAILABLE"],
        ))

`doc()` returns a drf-spectacular `extend_schema`; `document()` attaches a set
of them to a view (one per handler / viewset action) with `extend_schema_view`.
Everything the reader sees about errors comes from core.openapi.errors, so a
new error code is written once and shows up everywhere it is listed.
"""

import json
import re
from pathlib import Path

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)

from . import errors as catalog
from .serializers import ErrorEnvelopeSerializer

# -- who may call an endpoint -----------------------------------------------------
PUBLIC = [{}]
COMPANY = [{"ApiClientToken": []}, {"AdminToken": []}]
ADMIN = [{"AdminToken": []}]
DRIVER = [{"DriverToken": []}]

WHO = {
    "PUBLIC": "**No token needed.**",
    "COMPANY": "**Company** - an API client token *or* an admin token.",
    "ADMIN": "**Admin** - an admin token only (not an API client).",
    "DRIVER": "**Driver** - a driver token only.",
}


def _who(auth):
    if auth == PUBLIC:
        return WHO["PUBLIC"]
    if auth == COMPANY:
        return WHO["COMPANY"]
    if auth == ADMIN:
        return WHO["ADMIN"]
    if auth == DRIVER:
        return WHO["DRIVER"]
    raise ValueError(f"unknown auth {auth!r}")


# -- real example payloads (captured from the running API) -----------------------------
_EXAMPLES = json.loads((Path(__file__).parent / "examples.json").read_text())


def example_body(key):
    return _EXAMPLES[key]["body"]


def _slug(text):
    """`Cash on delivery, with items` -> `cash_on_delivery_with_items` (example keys must be plain)."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def ex(key, name, summary=None, description="", *, request=False, statuses=None, item=None):
    """An example whose payload is the real, captured `key` from examples.json.
    Pass `request=True` for a request body; otherwise it's a response.

    For a **list** endpoint pass `item=0` (or another index): the captured page's
    `results[item]`. drf-spectacular wraps a list endpoint's example in the page
    envelope itself, so giving it a whole page would show a page inside a page."""
    value = example_body(key)
    if item is not None:
        value = value["results"][item]
    kwargs = dict(
        name=_slug(name),
        value=value,
        summary=summary or name,
        description=description,
    )
    if request:
        kwargs["request_only"] = True
    else:
        kwargs["response_only"] = True
        if statuses:
            kwargs["status_codes"] = [str(s) for s in statuses]
    return OpenApiExample(**kwargs)


def raw_ex(name, value, summary=None, description="", *, request=False):
    """An example written inline (for payloads that were never captured)."""
    kwargs = dict(name=_slug(name), value=value, summary=summary or name, description=description)
    kwargs["request_only" if request else "response_only"] = True
    return OpenApiExample(**kwargs)


def derived_ex(key, name, changes, summary=None, description="", *, statuses=None):
    """A captured response with a few fields changed - "the same trip, but
    cancelled" - so the docs show every state without inventing whole payloads."""
    import copy

    value = copy.deepcopy(example_body(key))
    value.update(changes)
    kwargs = dict(name=_slug(name), value=value, summary=summary or name, description=description, response_only=True)
    if statuses:
        kwargs["status_codes"] = [str(s) for s in statuses]
    return OpenApiExample(**kwargs)


def ok(serializer=None, *examples, description=""):
    """A successful response. `serializer=None` means an empty body."""
    return OpenApiResponse(serializer, description=description, examples=list(examples))


# -- error responses, generated from the catalogue -----------------------------------------
def _error_responses(codes):
    by_status = {}
    for code in codes:
        info = catalog.BY_CODE[code]
        by_status.setdefault(info.status, []).append(info)
    responses = {}
    for status, infos in sorted(by_status.items()):
        if len(infos) == 1:
            lines = [f"`{infos[0].code}` - {infos[0].when}"]
        else:
            lines = ["One of:"] + [f"- `{i.code}` - {i.when}" for i in infos]
        examples = [
            OpenApiExample(
                name=i.code,
                summary=i.code,
                value=catalog.envelope(
                    i.code,
                    details={"field": ["This field is required."]} if i.code == "INVALID" else None,
                ),
                response_only=True,
                status_codes=[str(status)],
            )
            for i in infos
        ]
        responses[status] = OpenApiResponse(
            ErrorEnvelopeSerializer, description="\n".join(lines), examples=examples
        )
    return responses


# operationId -> what was documented as its request, so core/test_openapi.py can
# check every request example against the real serializer.
REGISTRY = {}

DEFAULT_SUCCESS = {200: "Success.", 201: "Created.", 202: "Accepted.", 204: "Done - no content is returned."}


def doc(
    *,
    id,
    tag,
    summary,
    description,
    auth,
    request=None,
    request_examples=(),
    responses=None,
    errors=(),
    params=(),
    by_id=None,
    validates=None,
    deprecated=False,
    notes=None,
):
    """Everything a reader needs to know about one operation.

    auth        PUBLIC / COMPANY / ADMIN / DRIVER - who may call it
    request     serializer (or {"multipart/form-data": serializer}); None = no body
    responses   {status: ok(...)} for the successes
    errors      catalogue codes this operation can return; the framework-level
                ones (401/403 for authenticated calls, 400 `INVALID` when there is
                a body, 404 when the path has an id) are added automatically
    by_id / validates   override the automatic 404 / 400 (default: guessed)
    """
    codes = list(errors)
    if auth != PUBLIC:
        codes += ["NOT_AUTHENTICATED", "TOKEN_NOT_VALID", "PERMISSION_DENIED"]
    if request is not None and validates is not False:
        codes.append("INVALID")
    if by_id and "NOT_FOUND" not in codes:
        codes.append("NOT_FOUND")
    codes = list(dict.fromkeys(codes))

    if request is not None and not isinstance(request, dict):
        # Say "JSON" - not also form-encoded and multipart, which the parsers
        # would accept but nobody should send here.
        request = {"application/json": request}
    REGISTRY[id] = {"request": request, "request_examples": list(request_examples)}

    merged = {}
    merged.update(_error_responses(codes))
    for status, response in (responses or {}).items():
        if isinstance(response, OpenApiResponse) and not response.description:
            response = OpenApiResponse(
                response.response, description=DEFAULT_SUCCESS.get(status, "Success."), examples=response.examples
            )
        merged[status] = response

    text = f"{description.strip()}\n\n**Who may call this:** {_who(auth)}"
    if notes:
        text += f"\n\n{notes.strip()}"
    return extend_schema(
        operation_id=id,
        tags=[tag],
        summary=summary,
        description=text,
        auth=auth,
        request=request,
        examples=list(request_examples),
        responses=merged,
        parameters=list(params),
        deprecated=deprecated,
    )


def document(view, **handlers):
    """Attach `doc(...)`s to `view`: keys are HTTP handler names (`get`, `post`,
    ...) for an APIView, or actions (`list`, `create`, `retrieve`, `update`,
    `partial_update`, `destroy`, plus custom @actions) for a viewset."""
    extend_schema_view(**handlers)(view)


# -- parameters ----------------------------------------------------------------------------
def path_param(name, description, *, type=OpenApiTypes.UUID):
    return OpenApiParameter(name, type=type, location=OpenApiParameter.PATH, description=description)


def query_param(name, description, *, type=str, required=False, enum=None, default=None, examples=None):
    return OpenApiParameter(
        name,
        type=type,
        location=OpenApiParameter.QUERY,
        required=required,
        description=description,
        enum=enum,
        default=default,
        examples=examples,
    )
