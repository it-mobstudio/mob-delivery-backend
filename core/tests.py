from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient


URL = "/api/v1/driver/auth/otp/request"
FLUTTER_WEB = "http://localhost:5057"


def preflight(client, origin=FLUTTER_WEB, headers="content-type,authorization"):
    return client.options(
        URL,
        HTTP_ORIGIN=origin,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS=headers,
    )


@override_settings(DEBUG=True)
class DevCorsTests(SimpleTestCase):
    def test_preflight_from_the_flutter_web_dev_server_is_allowed(self):
        response = preflight(APIClient())

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Access-Control-Allow-Origin"], FLUTTER_WEB)
        self.assertIn("POST", response["Access-Control-Allow-Methods"])
        # Echoes what the browser asked for (Dio adds Authorization + JSON content-type).
        self.assertEqual(response["Access-Control-Allow-Headers"], "content-type,authorization")
        self.assertIn("Origin", response["Vary"])

    def test_any_local_port_and_loopback_spelling_works(self):
        for origin in ("http://localhost:58869", "http://127.0.0.1:5057", "http://localhost"):
            self.assertEqual(preflight(APIClient(), origin)["Access-Control-Allow-Origin"], origin, origin)

    def test_the_real_request_carries_the_header_too_even_when_it_is_an_error(self):
        # Without the header on the *actual* response the browser discards it
        # even after a successful preflight — and on an error response it hides
        # the message from the app, which is how "Enter a valid phone number"
        # would turn into a generic "no internet". (An invalid phone fails
        # validation before any DB lookup, so this stays a SimpleTestCase.)
        response = APIClient().post(URL, {"phone_number": "abc"}, format="json", HTTP_ORIGIN=FLUTTER_WEB)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response["Access-Control-Allow-Origin"], FLUTTER_WEB)
        self.assertEqual(response.json()["error"]["details"]["phone_number"], ["Enter a valid phone number."])

    def test_foreign_origins_are_not_allowed(self):
        for origin in ("https://evil.example", "http://localhost.evil.example", "http://192.168.1.20:8000", "null"):
            response = preflight(APIClient(), origin)
            self.assertNotIn("Access-Control-Allow-Origin", response, origin)

    def test_requests_without_an_origin_are_untouched(self):
        response = APIClient().post(URL, {"phone_number": "abc"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_credentials_are_never_allowed_cross_origin(self):
        self.assertNotIn("Access-Control-Allow-Credentials", preflight(APIClient()))


class DevCorsIsOffInProductionTests(SimpleTestCase):
    @override_settings(DEBUG=False)
    def test_with_debug_off_no_origin_is_ever_allowed(self):
        response = preflight(APIClient())
        self.assertNotIn("Access-Control-Allow-Origin", response)
        # ...and the preflight is not answered on the API's behalf.
        self.assertNotEqual(response.status_code, 204)
