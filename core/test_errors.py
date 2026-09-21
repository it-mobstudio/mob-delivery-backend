"""The error envelope every client relies on: {"success": false, "error":
{"code", "message", "details"?}}. `code` is stable; `message` is for people."""

from django.test import TestCase
from rest_framework.test import APIClient

from core.testing import LOCMEM_CACHES, DriverTestMixin


@LOCMEM_CACHES
class ErrorEnvelopeTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.company_client = self.admin_client()

    def error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertIs(body["success"], False)
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"])
        return body["error"]

    def test_a_missing_credential(self):
        error = self.error(APIClient().get("/api/v1/trips"), 401, "NOT_AUTHENTICATED")
        self.assertNotIn("details", error)

    def test_an_invalid_token_says_so_without_leaking_library_internals(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer not.a.token")

        error = self.error(client.get("/api/v1/trips"), 401, "TOKEN_NOT_VALID")

        self.assertEqual(error["message"], "Given token not valid for any token type")
        self.assertNotIn("details", error)

    def test_a_stale_token_does_not_block_getting_a_new_one(self):
        # A client that keeps its last (now expired) token in its default headers
        # must still be able to call the endpoint that issues the next one.
        from accounts.models import ApiClient

        client = ApiClient(company=self.company, name="stale-token-test")
        client.set_secret("s3cret-value")
        client.save()
        stale = APIClient()
        stale.credentials(HTTP_AUTHORIZATION="Bearer expired.or.garbage")

        response = stale.post(
            "/api/v1/auth/client-token", {"client_id": str(client.client_id), "client_secret": "s3cret-value"}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("access", response.json())

    def test_the_wrong_kind_of_credential(self):
        error = self.error(self.driver_client(self.make_driver()).get("/api/v1/trips"), 403, "PERMISSION_DENIED")
        self.assertNotIn("details", error)

    def test_a_missing_resource_is_NOT_FOUND_not_a_bare_ERROR(self):
        # Covers both get_object_or_404 and DRF's own lookup.
        for url in (
            "/api/v1/trips/00000000-0000-0000-0000-000000000000",
            "/api/v1/vehicles/00000000-0000-0000-0000-000000000000",
        ):
            self.error(self.company_client.get(url), 404, "NOT_FOUND")
        driver = self.make_driver()
        self.error(self.driver_client(driver).get("/api/v1/driver/trips/00000000-0000-0000-0000-000000000000"), 404, "NOT_FOUND")

    def test_validation_errors_carry_field_details(self):
        error = self.error(self.company_client.post("/api/v1/trips", {"payment_mode": "cod"}, format="json"), 400, "INVALID")
        self.assertEqual(error["message"], "Request could not be processed.")
        self.assertEqual(set(error["details"]), {"vehicle_type_id", "pickup", "drop"})
        self.assertEqual(error["details"]["pickup"], ["This field is required."])

    def test_malformed_json(self):
        response = self.company_client.generic("POST", "/api/v1/trips", "{oops", content_type="application/json")
        error = self.error(response, 400, "PARSE_ERROR")
        self.assertNotIn("details", error)

    def test_a_method_the_endpoint_does_not_offer(self):
        self.error(self.company_client.put("/api/v1/trips", {}, format="json"), 405, "METHOD_NOT_ALLOWED")

    def test_business_rule_errors_keep_their_own_codes(self):
        error = self.error(
            self.company_client.delete(f"/api/v1/vehicle-types/{self.make_vehicle().vehicle_type.id}"), 409, "VEHICLE_TYPE_IN_USE"
        )
        self.assertNotIn("details", error)
