import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from drivers.models import Driver

from .models import AdminRole, AdminUser, ApiClient, Company
from .throttling import ApiClientRateThrottle


def _body(response):
    # response.data is the PRE-render dict (snake_case, set before the
    # camelCase renderer runs) — only response.content reflects what the
    # client actually receives on the wire.
    return json.loads(response.content)


class AdminRefreshTokenTests(TestCase):
    """Fix 4 — JWT refresh tokens."""

    def setUp(self):
        self.company = Company.objects.create(name="Auth Test Co")
        self.admin = AdminUser.objects.create_user(
            email="refreshadmin@test.invalid", company=self.company, password="pass12345", role=AdminRole.ADMIN
        )
        self.client_api = APIClient()

    def test_login_returns_access_and_refresh_tokens_in_expected_shape(self):
        r = self.client_api.post(
            "/api/v1/auth/login", {"email": "refreshadmin@test.invalid", "password": "pass12345"}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        body = _body(r)
        for key in ("accessToken", "refreshToken", "tokenType", "expiresInSeconds"):
            self.assertIn(key, body)
        self.assertEqual(body["tokenType"], "Bearer")

    def test_refresh_token_yields_a_working_new_access_token(self):
        login = self.client_api.post(
            "/api/v1/auth/login", {"email": "refreshadmin@test.invalid", "password": "pass12345"}, format="json"
        )
        refresh_token = _body(login)["refreshToken"]

        r = self.client_api.post("/api/v1/auth/refresh", {"refreshToken": refresh_token}, format="json")
        self.assertEqual(r.status_code, 200)
        access_token = _body(r)["accessToken"]

        authed_client = APIClient()
        authed_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        me = authed_client.get("/api/v1/drivers")  # any AdminUser-authenticated endpoint
        self.assertEqual(me.status_code, 200)

    def test_garbage_refresh_token_is_rejected(self):
        r = self.client_api.post("/api/v1/auth/refresh", {"refreshToken": "not-a-real-token"}, format="json")
        self.assertEqual(r.status_code, 401)

    def test_expired_refresh_token_is_rejected(self):
        refresh = RefreshToken()
        refresh["type"] = "admin"
        refresh["company_id"] = str(self.company.id)
        refresh.set_exp(lifetime=timedelta(seconds=-1))

        r = self.client_api.post("/api/v1/auth/refresh", {"refreshToken": str(refresh)}, format="json")
        self.assertEqual(r.status_code, 401)


class ApiClientRateThrottleTests(TestCase):
    """Fix 6 — API rate limiting, per ApiClient."""

    def setUp(self):
        self.company = Company.objects.create(name="Throttle Test Co")
        self.admin = AdminUser.objects.create_user(
            email="throttleadmin@test.invalid", company=self.company, password="pass12345"
        )
        self.api_client_principal = ApiClient(company=self.company, name="Rate Limited Client")
        self.api_client_principal.set_secret("dummy-secret")
        self.api_client_principal.save()
        self.other_api_client = ApiClient(company=self.company, name="Other Client")
        self.other_api_client.set_secret("dummy-secret-2")
        self.other_api_client.save()
        cache.clear()

    def test_admin_and_driver_requests_are_never_scoped_or_throttled(self):
        throttle = ApiClientRateThrottle()
        request = SimpleNamespace(user=self.admin)
        self.assertIsNone(throttle.get_cache_key(request, view=None))

    def _throttle_with_rate(self, rate):
        throttle = ApiClientRateThrottle()
        throttle.rate = rate
        throttle.num_requests, throttle.duration = throttle.parse_rate(rate)
        return throttle

    def test_api_client_is_throttled_after_the_configured_limit(self):
        throttle = self._throttle_with_rate("2/min")
        request = SimpleNamespace(user=self.api_client_principal)

        self.assertTrue(throttle.allow_request(request, view=None))
        self.assertTrue(throttle.allow_request(request, view=None))
        self.assertFalse(
            throttle.allow_request(request, view=None), "3rd request within the window should be throttled"
        )

    def test_different_api_clients_have_independent_limits(self):
        throttle = self._throttle_with_rate("1/min")

        request_a = SimpleNamespace(user=self.api_client_principal)
        request_b = SimpleNamespace(user=self.other_api_client)

        self.assertTrue(throttle.allow_request(request_a, view=None))
        self.assertFalse(throttle.allow_request(request_a, view=None))
        # A different client's own limit is untouched by client A's usage.
        self.assertTrue(throttle.allow_request(request_b, view=None))

    def test_http_level_429_with_retry_after_header(self):
        # DRF resolves DEFAULT_THROTTLE_CLASSES/RATES into class attributes
        # at import time — @override_settings doesn't reach code paths that
        # already read those attributes, so patch the rate table directly
        # instead (the same attribute get_rate() actually consults).
        with patch.object(ApiClientRateThrottle, "THROTTLE_RATES", {"api_client": "2/min"}):
            client = APIClient()
            client.force_authenticate(user=self.api_client_principal)

            r1 = client.get("/api/v1/vehicles")
            r2 = client.get("/api/v1/vehicles")
            r3 = client.get("/api/v1/vehicles")

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r3.status_code, 429)
        self.assertIn("Retry-After", r3)
        body = json.loads(r3.content)
        self.assertEqual(body["error"]["code"], "THROTTLED")


class SubUserManagementTests(TestCase):
    """Fix 8 — sub-user management (multiple AdminUsers per company)."""

    def setUp(self):
        self.company = Company.objects.create(name="Sub-User Test Co")
        self.other_company = Company.objects.create(name="Other Sub-User Co")
        self.owner = AdminUser.objects.create_user(
            email="owner@test.invalid", company=self.company, password="pass12345", role=AdminRole.OWNER
        )
        self.other_company_admin = AdminUser.objects.create_user(
            email="otheradmin@test.invalid", company=self.other_company, password="pass12345"
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.owner)

    def test_create_sub_user_scoped_to_creators_company(self):
        r = self.client_api.post(
            "/api/v1/admin/users",
            {"email": "teammate@test.invalid", "password": "teammate123", "firstName": "Team", "lastName": "Mate"},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        body = _body(r)
        self.assertEqual(body["email"], "teammate@test.invalid")

        created = AdminUser.objects.get(email="teammate@test.invalid")
        self.assertEqual(created.company_id, self.company.id)

    def test_company_id_cannot_be_spoofed_from_request_body(self):
        r = self.client_api.post(
            "/api/v1/admin/users",
            {
                "email": "spoofed@test.invalid", "password": "teammate123",
                "companyId": str(self.other_company.id),  # must be ignored entirely
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        created = AdminUser.objects.get(email="spoofed@test.invalid")
        self.assertEqual(created.company_id, self.company.id, "company must always come from the creator, never the body")

    def test_duplicate_email_is_a_clean_400_not_a_raw_db_error(self):
        r = self.client_api.post(
            "/api/v1/admin/users", {"email": "owner@test.invalid", "password": "teammate123"}, format="json"
        )
        self.assertEqual(r.status_code, 400)

    def test_list_only_shows_own_companys_admins(self):
        r = self.client_api.get("/api/v1/admin/users")
        self.assertEqual(r.status_code, 200)
        emails = {row["email"] for row in _body(r)["results"]}
        self.assertIn("owner@test.invalid", emails)
        self.assertNotIn("otheradmin@test.invalid", emails)

    def test_cannot_create_admin_for_a_different_company_via_cross_tenant_disable_either(self):
        r = self.client_api.post(f"/api/v1/admin/users/{self.other_company_admin.id}/disable")
        self.assertEqual(r.status_code, 404, "an admin in a different company must not even be visible")

    def test_owner_can_disable_a_teammate(self):
        teammate = AdminUser.objects.create_user(
            email="disableme@test.invalid", company=self.company, password="pass12345"
        )
        r = self.client_api.post(f"/api/v1/admin/users/{teammate.id}/disable")
        self.assertEqual(r.status_code, 200)

        teammate.refresh_from_db()
        self.assertFalse(teammate.is_active)
        self.assertTrue(teammate.is_deleted)

    def test_cannot_disable_own_account(self):
        r = self.client_api.post(f"/api/v1/admin/users/{self.owner.id}/disable")
        self.assertEqual(r.status_code, 409)
        body = _body(r)
        self.assertEqual(body["error"]["code"], "CANNOT_DISABLE_SELF")

    def test_disabled_admins_existing_token_stops_working(self):
        teammate = AdminUser.objects.create_user(
            email="soontobedisabled@test.invalid", company=self.company, password="pass12345"
        )
        login = self.client_api.post(
            "/api/v1/auth/login", {"email": "soontobedisabled@test.invalid", "password": "pass12345"}, format="json"
        )
        access_token = _body(login)["accessToken"]

        self.client_api.post(f"/api/v1/admin/users/{teammate.id}/disable")

        disabled_client = APIClient()
        disabled_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        r = disabled_client.get("/api/v1/drivers")
        self.assertEqual(r.status_code, 401)


class FoundationAuthTests(TestCase):
    """Production-readiness Part 3, Foundation/Auth section.

    Company onboarding in this codebase is a `bootstrap_company` management
    command (see accounts/management/commands/bootstrap_company.py), not an
    HTTP endpoint gated by a platform master key — there is no
    PLATFORM_MASTER_KEY to test here. The access-control boundary is server/
    CLI access itself, so the equivalent safety property to verify is that
    the command refuses to silently clobber an existing company or admin.
    """

    def test_bootstrap_command_rejects_a_duplicate_company_name(self):
        Company.objects.create(name="Acme Logistics")
        with self.assertRaises(CommandError):
            call_command(
                "bootstrap_company",
                "--company-name=Acme Logistics",
                "--admin-email=owner2@acme.test.invalid",
                "--admin-password=pass12345",
            )

    def test_bootstrap_command_rejects_a_duplicate_admin_email(self):
        company = Company.objects.create(name="Existing Co")
        AdminUser.objects.create_user(email="owner@acme.test.invalid", company=company, password="pass12345")
        with self.assertRaises(CommandError):
            call_command(
                "bootstrap_company",
                "--company-name=Brand New Co",
                "--admin-email=owner@acme.test.invalid",
                "--admin-password=pass12345",
            )

    def test_bootstrap_command_creates_company_and_owner_admin(self):
        call_command(
            "bootstrap_company",
            "--company-name=Fresh Co",
            "--admin-email=freshowner@test.invalid",
            "--admin-password=pass12345",
        )
        company = Company.objects.get(name="Fresh Co")
        admin = AdminUser.objects.get(email="freshowner@test.invalid")
        self.assertEqual(admin.company_id, company.id)
        self.assertEqual(admin.role, AdminRole.OWNER)

    def test_admin_login_rejects_a_disabled_account(self):
        company = Company.objects.create(name="Disabled Login Test Co")
        admin = AdminUser.objects.create_user(
            email="disabledlogin@test.invalid", company=company, password="pass12345"
        )
        admin.is_active = False
        admin.save(update_fields=["is_active"])

        r = APIClient().post(
            "/api/v1/auth/login", {"email": "disabledlogin@test.invalid", "password": "pass12345"}, format="json"
        )
        self.assertEqual(r.status_code, 401)

    def test_api_client_jwt_is_rejected_on_an_admin_only_endpoint(self):
        company = Company.objects.create(name="Actor Type Test Co")
        api_client = ApiClient(company=company, name="Not An Admin")
        api_client.set_secret("dummy-secret")
        api_client.save()

        client = APIClient()
        client.force_authenticate(user=api_client)
        r = client.get("/api/v1/admin/users")
        self.assertEqual(r.status_code, 403)

    def test_admin_jwt_is_rejected_on_a_driver_only_endpoint(self):
        company = Company.objects.create(name="Actor Type Test Co 2")
        admin = AdminUser.objects.create_user(
            email="notadriver@test.invalid", company=company, password="pass12345"
        )
        # Sanity check that a real Driver row existing elsewhere doesn't
        # accidentally make this pass for the wrong reason.
        Driver.objects.create(
            company=company, full_name="Someone Else", phone_number="+919000000001",
            emergency_contact_name="EC", emergency_contact_phone="+919000000002",
        )

        client = APIClient()
        client.force_authenticate(user=admin)
        r = client.get("/api/v1/driver/me")
        self.assertEqual(r.status_code, 403)
