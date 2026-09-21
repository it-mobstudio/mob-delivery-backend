"""The API documentation must stay true to the API.

These tests fail when an endpoint, a field, an error code or a guide drifts from
the code - so the docs cannot quietly rot. They read the *finished* schema (the
same one served at /api/schema/) and check it three ways:

* complete   - every endpoint / field / parameter is described, examples exist
* consistent - the error catalogue matches the code; guides only mention real endpoints
* accurate   - every request example is accepted by the real serializer, every
               response example fits its documented schema, and the errors an
               endpoint really returns are the errors it documents
"""

import ast
import json
import re
import tempfile
from pathlib import Path

import jsonschema
import yaml
from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from referencing import Registry, Resource
from rest_framework.test import APIClient

from core.management.commands.build_api_docs import build_schema
from core.openapi import errors as catalog
from core.openapi import guides, tags
from core.openapi.dsl import PUBLIC, REGISTRY
from core.testing import LOCMEM_CACHES, DriverTestMixin

HTTP = ("get", "put", "post", "patch", "delete")
_schema = None


def schema():
    """Built once per test run - generating it walks every endpoint."""
    global _schema
    if _schema is None:
        _schema = build_schema()
    return _schema


def operations():
    for path, item in schema()["paths"].items():
        for method in HTTP:
            if method in item:
                yield path, method, item[method]


def normalise(path):
    """`/driver/trips/{id}/arrive` and `/driver/trips/$TRIP/arrive` and
    `/driver/trips/<id>/arrive` are the same endpoint."""
    out = []
    for segment in path.strip("/").split("/"):
        if re.fullmatch(r"\{.*\}|\$\{?\w+\}?|<.*>|[0-9a-f]{8}-[0-9a-f-]{27}", segment):
            out.append("{}")
        else:
            out.append(segment)
    return "/" + "/".join(out)


class CompletenessTests(SimpleTestCase):
    def test_the_schema_builds_and_validates_with_no_warnings(self):
        # --fail-on-warn: an endpoint added without documentation makes the
        # generator warn ("unable to guess serializer", unresolved auth, ...).
        with tempfile.TemporaryDirectory() as tmp:
            call_command("spectacular", "--validate", "--fail-on-warn", "--file", str(Path(tmp) / "schema.yaml"))

    def test_every_operation_is_documented(self):
        seen_ids = set()
        problems = []
        groups = {t for _, members in tags.TAG_GROUPS for t in members}
        for path, method, op in operations():
            where = f"{method.upper()} {path}"
            op_id = op.get("operationId", "")
            if not re.fullmatch(r"[a-z][A-Za-z0-9]+", op_id):
                problems.append(f"{where}: operationId {op_id!r} isn't a plain camelCase name")
            if op_id in seen_ids:
                problems.append(f"{where}: duplicate operationId {op_id}")
            seen_ids.add(op_id)
            if not op.get("summary"):
                problems.append(f"{where}: no summary")
            if len(op.get("description", "")) < 60:
                problems.append(f"{where}: description is missing or too thin")
            if not op.get("tags") or any(t not in groups for t in op["tags"]):
                problems.append(f"{where}: tag {op.get('tags')} isn't in the menu (core/openapi/tags.py)")
            if "security" not in op:
                problems.append(f"{where}: says nothing about who may call it")
            if len(op.get("x-codeSamples", [])) != 3:
                problems.append(f"{where}: missing code samples")
            success = [code for code in op["responses"] if code.startswith("2")]
            if not success:
                problems.append(f"{where}: no success response")
            for code in success:
                response = op["responses"][code]
                if not response.get("description"):
                    problems.append(f"{where}: {code} response has no description")
                media = response.get("content", {}).get("application/json")
                if media is not None and not (media.get("examples") or media.get("example")):
                    problems.append(f"{where}: {code} response has no example")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_operations_list_the_errors_a_caller_should_expect(self):
        problems = []
        for path, method, op in operations():
            where = f"{method.upper()} {path}"
            codes = set(op["responses"])
            if op["security"] != [{}]:
                if not {"401", "403"} <= codes:
                    problems.append(f"{where}: authenticated but doesn't list 401 and 403")
            if "requestBody" in op and "400" not in codes:
                problems.append(f"{where}: has a body but doesn't list 400")
            if "{" in path and "404" not in codes:
                problems.append(f"{where}: addresses a resource but doesn't list 404")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_every_parameter_and_field_is_described(self):
        problems = []
        for path, method, op in operations():
            for parameter in op.get("parameters", []):
                if not parameter.get("description"):
                    problems.append(f"{method.upper()} {path}: parameter {parameter['name']!r} has no description")
        for name, component in schema()["components"]["schemas"].items():
            for field, prop in (component.get("properties") or {}).items():
                described = prop.get("description") or any(x.get("description") for x in prop.get("allOf", []))
                if not described and set(prop) != {"$ref"}:
                    problems.append(f"schema {name}.{field} has no description (core/openapi/fields.py)")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_required_fields_are_marked_on_the_headline_requests(self):
        components = schema()["components"]["schemas"]
        self.assertEqual(sorted(components["TripCreateRequest"]["required"]), ["drop", "payment_mode", "pickup", "vehicle_type_id"])
        self.assertEqual(sorted(components["PointRequest"]["required"]), ["address", "lat", "lng"])
        self.assertEqual(sorted(components["TripItemInputRequest"]["required"]), ["name"])
        self.assertEqual(sorted(components["DriverOtpVerifyRequest"]["required"]), ["otp", "phone_number"])
        self.assertNotIn("required", components["PatchedDriverProfileUpdateRequest"], "PATCH /driver/me: every field is optional")

    def test_every_operation_appears_in_the_menu_and_the_menu_has_no_ghosts(self):
        used = {t for _, _, op in operations() for t in op["tags"]}
        documented = {name for name, _ in tags.ENDPOINT_TAGS}
        self.assertEqual(used, documented, "sections in tags.ENDPOINT_TAGS and tags used by endpoints must match")
        listed = [t for _, members in tags.TAG_GROUPS for t in members]
        self.assertEqual(sorted(listed), sorted([name for name, _ in guides.GUIDE_FILES] + list(documented)))

    def test_public_endpoints_are_exactly_the_ones_that_need_no_token(self):
        public = sorted(f"{m.upper()} {p}" for p, m, op in operations() if op["security"] == PUBLIC)
        self.assertEqual(
            public,
            [
                "POST /auth/client-token",
                "POST /auth/login",
                "POST /auth/refresh",
                "POST /driver/auth/logout",
                "POST /driver/auth/otp/request",
                "POST /driver/auth/otp/verify",
                "POST /driver/auth/refresh",
                "POST /webhooks/razorpay",
            ],
            "a new public endpoint must be a deliberate decision",
        )


class GuideTests(SimpleTestCase):
    def test_the_guides_are_written_and_fully_rendered(self):
        for name, text in guides.load():
            self.assertGreater(len(text), 1500, f"guide {name!r} looks unfinished")
            self.assertNotIn("(draft)", text, name)
            self.assertNotIn("{{", text, f"{name}: an unreplaced placeholder")
            self.assertNotIn("TODO", text, name)

    def test_guides_only_mention_endpoints_that_exist(self):
        real = {(method.upper(), normalise(path)) for path, method, _ in operations()}
        mentioned = []
        for name, text in guides.load():
            for method, path in re.findall(r"`(GET|POST|PUT|PATCH|DELETE) (/[^\s`?]+)", text):
                mentioned.append((name, method, path))
            for method, path in re.findall(r'-X (GET|POST|PUT|PATCH|DELETE) "\$BASE_URL(/[^"?]+)', text):
                mentioned.append((name, method, path))
            for path in re.findall(r'curl "\$BASE_URL(/[^"?]+)"', text):
                mentioned.append((name, "GET", path))
        self.assertGreater(len(mentioned), 40, "expected the guides to reference plenty of endpoints")
        unknown = sorted({f"{name}: {m} {p}" for name, m, p in mentioned if (m, normalise(p)) not in real})
        self.assertEqual(unknown, [], "guides mention endpoints that don't exist")

    def test_the_error_table_in_the_guide_is_the_catalogue(self):
        text = dict(guides.load())["Errors"]
        for info in catalog.ERRORS:
            self.assertIn(f"`{info.code}`", text)


class ErrorCatalogueTests(SimpleTestCase):
    """The catalogue and the code must list the same errors."""

    FRAMEWORK = {
        "INVALID", "PARSE_ERROR", "UNSUPPORTED_MEDIA_TYPE", "NOT_FOUND", "METHOD_NOT_ALLOWED",
        "NOT_AUTHENTICATED", "TOKEN_NOT_VALID", "AUTHENTICATION_FAILED", "PERMISSION_DENIED",
    }

    def codes_in_code(self):
        found = {}
        for file in Path(settings.BASE_DIR).rglob("*.py"):
            parts = file.parts
            if ".venv" in parts or "migrations" in parts or file.name.startswith("test") or "tests" in parts or "openapi" in parts:
                continue
            for node in ast.walk(ast.parse(file.read_text())):
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "DomainError" and node.args:
                    if isinstance(node.args[0], ast.Constant):
                        status = next(
                            (k.value.value for k in node.keywords if k.arg == "status_code" and isinstance(k.value, ast.Constant)), 409
                        )
                        found.setdefault(node.args[0].value, set()).add(status)
        return found

    def test_every_error_the_code_raises_is_documented_with_the_right_status(self):
        for code, statuses in self.codes_in_code().items():
            self.assertTrue(code in catalog.BY_CODE, f"{code} is raised in the code but missing from core/openapi/errors.py")
            self.assertTrue(
                catalog.BY_CODE[code].status in statuses,
                f"{code}: documented as {catalog.BY_CODE[code].status}, the code uses {sorted(statuses)}",
            )

    def test_every_documented_error_still_exists(self):
        raised = set(self.codes_in_code())
        stale = [e.code for e in catalog.ERRORS if e.code not in raised and e.code not in self.FRAMEWORK]
        self.assertEqual(stale, [], "documented but no longer raised anywhere: remove from core/openapi/errors.py")

    def test_the_catalogue_is_well_formed(self):
        self.assertEqual(len(catalog.BY_CODE), len(catalog.ERRORS), "duplicate codes")
        for info in catalog.ERRORS:
            self.assertRegex(info.code, r"^[A-Z][A-Z_]+$")
            self.assertTrue(info.when and info.fix and info.message, info.code)
            self.assertGreaterEqual(info.status, 400)


def validator_for(document):
    """A JSON-Schema validator for the components of the OpenAPI document
    (OpenAPI 3.0 schemas are a JSON-Schema dialect; `nullable` is the odd one out)."""

    def convert(node):
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                if key.startswith("x-") or key in ("readOnly", "writeOnly", "example", "examples", "externalDocs", "discriminator"):
                    continue
                if key == "$ref" and value.startswith("#/"):
                    out[key] = "urn:openapi" + value
                else:
                    out[key] = convert(value)
            if "oneOf" in out:
                # spectacular writes a blank-able email as oneOf[blank, email]; a blank string
                # satisfies both (format isn't checked), which oneOf rejects and anyOf allows.
                out["anyOf"] = out.pop("oneOf")
            if out.pop("nullable", False):
                out = {"anyOf": [out, {"type": "null"}]}
            return out
        if isinstance(node, list):
            return [convert(item) for item in node]
        return node

    resource = Resource.from_contents({"$schema": "https://json-schema.org/draft/2020-12/schema", "components": convert(document["components"])})
    registry = Registry().with_resource("urn:openapi", resource)

    def validate(instance, schema_node):
        jsonschema.Draft202012Validator(convert(schema_node), registry=registry).validate(instance)

    return validate


class AccuracyTests(SimpleTestCase):
    def test_every_response_example_fits_its_documented_schema(self):
        validate = validator_for(schema())
        failures = []
        for path, method, op in operations():
            for status, response in op["responses"].items():
                media = response.get("content", {}).get("application/json")
                if not media or "schema" not in media:
                    continue
                for name, example in (media.get("examples") or {}).items():
                    if "value" not in example:
                        continue
                    try:
                        validate(example["value"], media["schema"])
                    except jsonschema.ValidationError as error:
                        failures.append(f"{method.upper()} {path} {status} example {name!r}: {error.message[:160]} at {list(error.absolute_path)}")
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_every_request_example_fits_its_documented_schema(self):
        validate = validator_for(schema())
        failures = []
        checked = 0
        for path, method, op in operations():
            media = op.get("requestBody", {}).get("content", {}).get("application/json")
            if not media:
                continue
            for name, example in (media.get("examples") or {}).items():
                checked += 1
                try:
                    validate(example["value"], media["schema"])
                except jsonschema.ValidationError as error:
                    failures.append(f"{method.upper()} {path} example {name!r}: {error.message[:160]} at {list(error.absolute_path)}")
        self.assertGreater(checked, 40)
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_every_request_example_is_accepted_by_the_real_serializer(self):
        # The JSON-Schema check above knows types and required fields; this runs
        # the serializer's own rules too (age limits, "must be in the future", ...).
        from rest_framework import serializers

        failures = []
        checked = 0
        patches = {op["operationId"] for _, method, op in operations() if method == "patch"}
        for op_id, entry in REGISTRY.items():
            request = entry["request"]
            if not request or "application/json" not in request or not entry["request_examples"]:
                continue
            serializer_class = request["application/json"]
            for example in entry["request_examples"]:
                checked += 1
                serializer = serializer_class(data=example.value, partial=op_id in patches, context={})
                if serializer.is_valid():
                    continue
                # Ids in examples are made up: a "no such vehicle type" is expected and fine.
                real = {
                    field: errors
                    for field, errors in serializer.errors.items()
                    if not all(getattr(e, "code", "") in ("does_not_exist", "incorrect_type") for e in errors)
                }
                if real:
                    failures.append(f"{op_id} example {example.name!r}: {json.loads(json.dumps(real, default=str))}")
        self.assertGreater(checked, 40)
        self.assertEqual(failures, [], "\n" + "\n".join(failures))
        self.assertTrue(issubclass(serializers.Serializer, object))

    def test_the_committed_exports_are_current(self):
        fresh = json.loads(json.dumps(build_schema(), default=str))
        for name, load in (("openapi.json", json.loads), ("openapi.yaml", yaml.safe_load)):
            on_disk = load((Path(settings.BASE_DIR) / "docs" / "api" / name).read_text(encoding="utf-8"))
            self.assertEqual(
                on_disk,
                fresh,
                f"docs/api/{name} is out of date - run: python manage.py build_api_docs",
            )


@LOCMEM_CACHES
class DocumentedErrorsAreRealTests(DriverTestMixin, TestCase):
    """Provoke real errors and check the endpoint documents that very answer."""

    def documented(self, operation_id):
        for _, _, op in operations():
            if op["operationId"] == operation_id:
                found = set()
                for status, response in op["responses"].items():
                    for example in (response.get("content", {}).get("application/json", {}).get("examples") or {}).values():
                        value = example.get("value")
                        if isinstance(value, dict) and value.get("success") is False:
                            found.add((int(status), value["error"]["code"]))
                return found
        self.fail(f"no such operation {operation_id}")

    def assert_documented(self, response, operation_id, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertIn((status, code), self.documented(operation_id), f"{operation_id} doesn't document {status} {code}")

    def test_company_side(self):
        admin = self.admin_client()
        self.assert_documented(admin.post("/api/v1/trips", {}, format="json"), "createTrip", 400, "INVALID")
        self.assert_documented(admin.get("/api/v1/trips/00000000-0000-0000-0000-000000000000"), "getTrip", 404, "NOT_FOUND")
        self.assert_documented(APIClient().get("/api/v1/trips"), "listTrips", 401, "NOT_AUTHENTICATED")
        self.assert_documented(self.driver_client(self.make_driver()).get("/api/v1/trips"), "listTrips", 403, "PERMISSION_DENIED")

        driver = self.make_driver()
        in_progress = self.make_trip(driver, status="in_progress")
        response = admin.post(f"/api/v1/trips/{in_progress.id}/cancel", {"reason": "x"}, format="json")
        self.assert_documented(response, "cancelTrip", 409, "TRIP_NOT_CANCELLABLE")
        self.assert_documented(admin.post(f"/api/v1/trips/{in_progress.id}/assign"), "assignTrip", 409, "TRIP_NOT_RETRYABLE")

    def test_driver_side(self):
        driver = self.make_driver()
        mine = self.driver_client(driver)
        trip = self.make_trip(driver, status="assigned")
        self.assert_documented(mine.post(f"/api/v1/driver/trips/{trip.id}/start"), "driverStart", 409, "INVALID_TRIP_STATUS_TRANSITION")
        self.assert_documented(mine.post(f"/api/v1/driver/trips/{trip.id}/complete", {}, format="json"), "driverComplete", 409, "PAYMENT_NOT_COLLECTED")
        self.assert_documented(mine.get(f"/api/v1/driver/trips/{trip.id}/payment/qr"), "driverGetPaymentQr", 409, "TRIP_NOT_IN_PROGRESS")
        self.assert_documented(mine.get("/api/v1/driver/trips/00000000-0000-0000-0000-000000000000"), "driverGetTrip", 404, "NOT_FOUND")
        stranger = self.driver_client(self.make_driver())
        self.assert_documented(stranger.post(f"/api/v1/driver/trips/{trip.id}/arrive"), "driverArrive", 404, "NOT_FOUND")
        self.assert_documented(APIClient().post("/api/v1/driver/auth/otp/verify", {"phone_number": "+919000000001", "otp": "12345"}, format="json"), "driverVerifyOtp", 400, "INVALID")


@LOCMEM_CACHES
class ServingTests(TestCase):
    def test_the_docs_are_public(self):
        stale = APIClient()
        stale.credentials(HTTP_AUTHORIZATION="Bearer an.expired.token")  # must not matter
        for url, marker in (
            ("/api/docs/", b"swagger"),
            ("/api/redoc/", b"Redoc"),
            ("/api/schema/", b"openapi: 3."),
            ("/api/schema/?format=json", b'"openapi"'),
        ):
            response = stale.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertIn(marker.lower(), response.content.lower() if hasattr(response, "content") else b"", url)

    def test_the_served_schema_has_the_guides_and_the_menu(self):
        body = APIClient().get("/api/schema/?format=json").json()
        self.assertEqual([g["name"] for g in body["x-tagGroups"]], [name for name, _ in tags.TAG_GROUPS])
        self.assertEqual(len([t for t in body["tags"] if t.get("x-traitTag")]), len(guides.GUIDE_FILES))
        self.assertEqual(body["servers"][0]["url"], "/api/v1")
        self.assertEqual(set(body["components"]["securitySchemes"]), {"ApiClientToken", "AdminToken", "DriverToken"})
