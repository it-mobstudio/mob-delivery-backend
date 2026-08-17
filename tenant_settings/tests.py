from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import AdminUser, Company
from drivers.models import Driver

from . import services
from .models import TenantSetting


class TenantSettingsTestBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Settings Test Co")
        self.admin = AdminUser.objects.create_user(
            email="settingsadmin@test.invalid", company=self.company, password="pass12345"
        )


class SeedAndResolveTests(TenantSettingsTestBase):
    def test_seed_creates_all_default_keys(self):
        services.seed_default_settings(self.company)
        keys = set(TenantSetting.objects.filter(company=self.company).values_list("key", flat=True))
        self.assertEqual(keys, set(services.DEFAULT_SETTINGS.keys()))

    def test_seed_is_idempotent(self):
        services.seed_default_settings(self.company)
        services.set_tenant_setting(self.company.id, "assignment_window_minutes", "45")
        services.seed_default_settings(self.company)  # must not clobber the customized value
        self.assertEqual(
            services.get_tenant_setting(self.company.id, "assignment_window_minutes", 30), 45
        )

    def test_get_returns_default_when_row_missing(self):
        self.assertEqual(services.get_tenant_setting(self.company.id, "nonexistent_key", 42), 42)

    def test_get_casts_to_default_type(self):
        services.set_tenant_setting(self.company.id, "geofence_meters", "150")
        value = services.get_tenant_setting(self.company.id, "geofence_meters", 100)
        self.assertEqual(value, 150)
        self.assertIsInstance(value, int)

    def test_set_upserts(self):
        services.set_tenant_setting(self.company.id, "geofence_meters", "100")
        services.set_tenant_setting(self.company.id, "geofence_meters", "125")
        self.assertEqual(TenantSetting.objects.filter(company=self.company, key="geofence_meters").count(), 1)
        self.assertEqual(services.get_tenant_setting(self.company.id, "geofence_meters", 100), 125)

    def test_settings_are_company_scoped(self):
        other_company = Company.objects.create(name="Other Settings Co")
        services.set_tenant_setting(self.company.id, "geofence_meters", "200")
        self.assertEqual(services.get_tenant_setting(other_company.id, "geofence_meters", 100), 100)


class TenantSettingHttpTests(TenantSettingsTestBase):
    def setUp(self):
        super().setUp()
        services.seed_default_settings(self.company)
        self.driver = Driver.objects.create(
            company=self.company, full_name="Settings Driver", phone_number="+919222000001",
            emergency_contact_name="EC", emergency_contact_phone="+919222000002",
        )

    def test_admin_can_list_settings(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        r = client.get("/api/v1/settings")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], len(services.DEFAULT_SETTINGS))

    def test_admin_can_patch_a_setting(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        r = client.patch("/api/v1/settings/assignment_window_minutes", {"value": "60"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["value"], "60")
        self.assertEqual(services.get_tenant_setting(self.company.id, "assignment_window_minutes", 30), 60)

    def test_driver_cannot_view_or_patch_settings(self):
        client = APIClient()
        client.force_authenticate(user=self.driver)
        self.assertEqual(client.get("/api/v1/settings").status_code, 403)
        self.assertEqual(
            client.patch("/api/v1/settings/geofence_meters", {"value": "1"}, format="json").status_code, 403
        )
