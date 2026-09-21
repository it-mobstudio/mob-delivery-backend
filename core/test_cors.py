"""Who a browser is allowed to talk to this API from (CORS)."""

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from core.cors import parse_origins

NETLIFY = "https://mob-driver.netlify.app"
PREFLIGHT = {
    "HTTP_ACCESS_CONTROL_REQUEST_METHOD": "POST",
    "HTTP_ACCESS_CONTROL_REQUEST_HEADERS": "authorization,content-type",
}
OTP = "/api/v1/driver/auth/otp/request"


class ParseOriginsTests(SimpleTestCase):
    def test_exact_origins_are_kept_and_tidied(self):
        self.assertEqual(
            parse_origins([" https://mob-driver.netlify.app/ ", "http://localhost:3000", "", "HTTPS://Admin.Example.com"]),
            ["https://mob-driver.netlify.app", "http://localhost:3000", "https://admin.example.com"],
        )
        self.assertEqual(parse_origins([]), [])

    def test_a_wildcard_or_anything_that_is_not_an_origin_is_refused_loudly(self):
        for bad in ("*", "https://*.netlify.app", "netlify.app", "ftp://host", "https://host/path", "https://host?x=1", "https://"):
            with self.assertRaises(ValueError, msg=bad):
                parse_origins([bad])


@override_settings(CORS_ALLOWED_ORIGINS=[NETLIFY], DEBUG=False)
class ListedOriginTests(TestCase):
    """A deployed web build: allowed by name, with DEBUG off."""

    def setUp(self):
        self.client = APIClient()

    def test_the_preflight_is_answered_and_lets_the_token_header_through(self):
        response = self.client.options(OTP, HTTP_ORIGIN=NETLIFY, **PREFLIGHT)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Access-Control-Allow-Origin"], NETLIFY, "the origin itself, never *")
        self.assertIn("POST", response["Access-Control-Allow-Methods"])
        self.assertEqual(response["Access-Control-Allow-Headers"], "authorization,content-type")
        self.assertEqual(response["Vary"], "Origin")
        self.assertNotIn("Access-Control-Allow-Credentials", response, "bearer tokens, not cookies")

    def test_real_answers_carry_the_header_errors_included(self):
        # A browser hides a response it isn't allowed to read - a 400 with the
        # reason is useless to the app unless it is readable too.
        ok = self.client.get("/api/schema/", HTTP_ORIGIN=NETLIFY)
        refused = self.client.post(OTP, {"phone_number": "not a phone"}, format="json", HTTP_ORIGIN=NETLIFY)

        self.assertEqual(ok["Access-Control-Allow-Origin"], NETLIFY)
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused["Access-Control-Allow-Origin"], NETLIFY)
        self.assertEqual(refused.json()["error"]["code"], "INVALID")

    def test_lookalikes_and_strangers_get_nothing(self):
        for origin in (
            "https://evil.example",
            "https://mob-driver.netlify.app.evil.com",
            "https://evil.mob-driver.netlify.app",
            "http://mob-driver.netlify.app",  # the scheme is part of the origin
            "https://mob-driver.netlify.app:8443",
            "null",
        ):
            preflight = self.client.options(OTP, HTTP_ORIGIN=origin, **PREFLIGHT)
            self.assertNotIn("Access-Control-Allow-Origin", preflight, origin)
            self.assertNotEqual(preflight.status_code, 204, f"{origin}: the preflight is not answered for them")
            actual = self.client.get("/api/schema/", HTTP_ORIGIN=origin)
            self.assertNotIn("Access-Control-Allow-Origin", actual, origin)
            self.assertIn("Origin", actual["Vary"], "still varies, so a cache can't cross-serve")

    def test_no_origin_means_no_cors_headers_at_all(self):
        response = self.client.get("/api/schema/")
        self.assertNotIn("Access-Control-Allow-Origin", response)
        self.assertNotIn("Origin", response.get("Vary", ""))

    def test_localhost_is_not_let_in_when_debug_is_off(self):
        response = self.client.options(OTP, HTTP_ORIGIN="http://localhost:5555", **PREFLIGHT)
        self.assertNotIn("Access-Control-Allow-Origin", response)


@override_settings(CORS_ALLOWED_ORIGINS=[], DEBUG=True)
class LocalDevTests(TestCase):
    def test_a_local_flutter_web_dev_server_works_on_any_port(self):
        client = APIClient()
        for origin in ("http://localhost:52713", "http://127.0.0.1:8080"):
            response = client.options(OTP, HTTP_ORIGIN=origin, **PREFLIGHT)
            self.assertEqual((response.status_code, response["Access-Control-Allow-Origin"]), (204, origin))

    def test_the_deployed_origin_is_not_special_unless_listed(self):
        self.assertNotIn("Access-Control-Allow-Origin", APIClient().options(OTP, HTTP_ORIGIN=NETLIFY, **PREFLIGHT))


@override_settings(CORS_ALLOWED_ORIGINS=[NETLIFY, "https://admin.example.com"], DEBUG=False)
class SeveralOriginsTests(TestCase):
    def test_each_listed_origin_is_echoed_back_as_itself(self):
        client = APIClient()
        for origin in (NETLIFY, "https://admin.example.com"):
            self.assertEqual(client.options(OTP, HTTP_ORIGIN=origin, **PREFLIGHT)["Access-Control-Allow-Origin"], origin)
