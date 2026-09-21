from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from core.choices import DriverAccountStatus, PaymentMode, TripStatus, VehicleCategory, VehicleStatus
from core.testing import LOCMEM_CACHES, DriverTestMixin
from drivers.tokens import DriverTokenService


@LOCMEM_CACHES
class DriverAuthTests(DriverTestMixin, TestCase):
    def login(self, driver):
        client = APIClient()
        otp = client.post("/api/v1/driver/auth/otp/request", {"phone_number": driver.phone_number}).json()["otp"]
        return client.post("/api/v1/driver/auth/otp/verify", {"phone_number": driver.phone_number, "otp": otp})

    def test_verify_returns_token_pair_and_profile(self):
        driver = self.make_driver()
        response = self.login(driver)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tokenType"], "Bearer")
        self.assertEqual(body["driverName"], driver.full_name)
        self.assertGreater(body["refreshExpiresInSeconds"], body["expiresInSeconds"])
        self.assertEqual(body["driver"]["id"], str(driver.id))

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['accessToken']}")
        self.assertEqual(client.get("/api/v1/driver/me").status_code, 200)

    def test_refresh_token_cannot_be_used_as_an_access_token(self):
        body = self.login(self.make_driver()).json()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['refreshToken']}")
        self.assertEqual(client.get("/api/v1/driver/me").status_code, 401)

    def test_otp_endpoints_ignore_a_stale_bearer_token(self):
        # A driver whose session lapsed still has the old access token on
        # the device — it mustn't turn their next login into a 401.
        driver = self.make_driver()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer this.is.expired")
        response = client.post("/api/v1/driver/auth/otp/request", {"phone_number": driver.phone_number})
        self.assertEqual(response.status_code, 200)

    def test_refresh_issues_a_working_new_pair(self):
        driver = self.make_driver()
        old = self.login(driver).json()

        response = APIClient().post("/api/v1/driver/auth/refresh", {"refreshToken": old["refreshToken"]}, format="json")

        self.assertEqual(response.status_code, 200)
        new = response.json()
        self.assertNotEqual(new["accessToken"], old["accessToken"])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {new['accessToken']}")
        self.assertEqual(client.get("/api/v1/driver/me").status_code, 200)

    def test_refresh_rejects_garbage_and_access_tokens(self):
        driver = self.make_driver()
        access, _ = DriverTokenService.issue(driver)
        for bad in ("not-a-token", access):
            response = APIClient().post("/api/v1/driver/auth/refresh", {"refreshToken": bad}, format="json")
            self.assertEqual(response.status_code, 401, bad)
            self.assertEqual(response.json()["error"]["code"], "INVALID_REFRESH_TOKEN")

    def test_refresh_rejects_another_principals_refresh_token(self):
        # A simplejwt refresh token with no `type: driver` claim — what an
        # AdminUser login produces.
        response = APIClient().post("/api/v1/driver/auth/refresh", {"refreshToken": str(RefreshToken())}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_refresh_stops_working_once_the_driver_is_disabled(self):
        driver = self.make_driver()
        refresh = self.login(driver).json()["refreshToken"]
        driver.account_status = DriverAccountStatus.DISABLED
        driver.save(update_fields=["account_status"])

        response = APIClient().post("/api/v1/driver/auth/refresh", {"refreshToken": refresh}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_logout_revokes_the_refresh_token(self):
        refresh = self.login(self.make_driver()).json()["refreshToken"]

        self.assertEqual(APIClient().post("/api/v1/driver/auth/logout", {"refreshToken": refresh}, format="json").status_code, 200)

        response = APIClient().post("/api/v1/driver/auth/refresh", {"refreshToken": refresh}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_logout_is_idempotent_and_tolerates_junk(self):
        response = APIClient().post("/api/v1/driver/auth/logout", {"refreshToken": "junk"}, format="json")
        self.assertEqual(response.status_code, 200)

    def test_expired_refresh_token_is_rejected(self):
        driver = self.make_driver()
        refresh = RefreshToken()
        refresh.set_exp(lifetime=-timedelta(seconds=5))
        refresh["type"] = "driver"
        refresh["sub"] = str(driver.id)
        response = APIClient().post("/api/v1/driver/auth/refresh", {"refreshToken": str(refresh)}, format="json")
        self.assertEqual(response.status_code, 401)


@LOCMEM_CACHES
class DriverProfileAndDutyTests(DriverTestMixin, TestCase):
    def test_me_includes_vehicle_and_eligibility(self):
        driver = self.make_driver()
        vehicle = self.make_vehicle()
        client = self.driver_client(driver)
        client.post("/api/v1/driver/duty/start", {"vehicle_id": str(vehicle.id)}, format="json")

        me = client.get("/api/v1/driver/me").json()

        self.assertTrue(me["is_online"])
        self.assertTrue(me["is_eligible_for_assignment"])
        self.assertEqual(me["current_vehicle"]["registration_number"], vehicle.registration_number)
        self.assertEqual(me["current_vehicle"]["vehicle_type"]["category"], "two_wheeler")
        self.assertEqual(me["dl_allowed_categories"], ["two_wheeler"])

    def test_me_exposes_kyc_rejection_notes_for_a_blocked_driver(self):
        from core.choices import VerificationStatus
        from drivers.services import DriverKycService

        driver = self.make_driver(verified=False)
        DriverKycService.verify_aadhar(driver, VerificationStatus.REJECTED, admin_id=None, note="Blurry scan")

        me = self.driver_client(driver).get("/api/v1/driver/me").json()

        self.assertFalse(me["is_eligible_for_assignment"])
        self.assertEqual(me["aadhar_status"], "rejected")
        self.assertEqual(me["aadhar_rejection_note"], "Blurry scan")

    def test_vehicle_list_only_shows_vehicles_the_driver_can_take(self):
        driver = self.make_driver()  # licensed for two-wheelers only
        mine = self.make_vehicle()
        self.make_vehicle(status=VehicleStatus.MAINTENANCE)  # not active
        truck_type = self.make_vehicle_type("Truck", VehicleCategory.FOUR_WHEELER)
        self.make_vehicle(vehicle_type=truck_type)  # wrong licence category

        other_driver = self.make_driver()
        taken = self.make_vehicle()
        self.driver_client(other_driver).post("/api/v1/driver/duty/start", {"vehicle_id": str(taken.id)}, format="json")

        rows = self.driver_client(driver).get("/api/v1/driver/vehicles").json()["vehicles"]

        self.assertEqual([row["id"] for row in rows], [str(mine.id)])
        self.assertFalse(rows[0]["is_current"])

    def test_a_vehicle_frees_up_when_its_driver_goes_offline(self):
        vehicle = self.make_vehicle()
        first, second = self.make_driver(), self.make_driver()
        first_client = self.driver_client(first)
        first_client.post("/api/v1/driver/duty/start", {"vehicle_id": str(vehicle.id)}, format="json")

        blocked = self.driver_client(second).post(
            "/api/v1/driver/duty/start", {"vehicle_id": str(vehicle.id)}, format="json"
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"]["code"], "VEHICLE_IN_USE")

        first_client.post("/api/v1/driver/duty/end")
        ok = self.driver_client(second).post("/api/v1/driver/duty/start", {"vehicle_id": str(vehicle.id)}, format="json")
        self.assertEqual(ok.status_code, 200)

    def test_duty_start_records_the_first_location_fix(self):
        driver = self.make_driver()
        vehicle = self.make_vehicle()

        response = self.driver_client(driver).post(
            "/api/v1/driver/duty/start",
            {"vehicle_id": str(vehicle.id), "lat": "12.971600", "lng": "77.594600"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        driver.refresh_from_db()
        self.assertEqual(driver.last_known_lat, Decimal("12.971600"))
        self.assertIsNotNone(driver.last_location_at)

    def test_duty_start_rejects_half_a_location(self):
        driver = self.make_driver()
        vehicle = self.make_vehicle()
        response = self.driver_client(driver).post(
            "/api/v1/driver/duty/start", {"vehicle_id": str(vehicle.id), "lat": "12.97"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_duty_start_rejects_a_category_the_licence_does_not_cover(self):
        driver = self.make_driver()
        truck = self.make_vehicle(vehicle_type=self.make_vehicle_type("Truck", VehicleCategory.FOUR_WHEELER))

        response = self.driver_client(driver).post("/api/v1/driver/duty/start", {"vehicle_id": str(truck.id)}, format="json")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "VEHICLE_CATEGORY_NOT_ALLOWED")

    def test_duty_start_blocked_until_kyc_is_verified(self):
        driver = self.make_driver(verified=False)
        vehicle = self.make_vehicle()
        response = self.driver_client(driver).post("/api/v1/driver/duty/start", {"vehicle_id": str(vehicle.id)}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "DRIVER_NOT_ELIGIBLE")

    def test_switching_vehicles_releases_the_old_one_but_not_mid_trip(self):
        driver = self.make_driver()
        first, second = self.make_vehicle(), self.make_vehicle()
        client = self.driver_client(driver)
        client.post("/api/v1/driver/duty/start", {"vehicle_id": str(first.id)}, format="json")

        self.assertEqual(client.post("/api/v1/driver/duty/start", {"vehicle_id": str(second.id)}, format="json").status_code, 200)
        first.refresh_from_db()
        self.assertIsNone(first.current_driver_id)

        self.make_trip(driver, vehicle=second)
        blocked = client.post("/api/v1/driver/duty/start", {"vehicle_id": str(first.id)}, format="json")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"]["code"], "DRIVER_HAS_ACTIVE_TRIP")

    def test_cannot_go_offline_with_an_active_trip(self):
        driver = self.make_driver()
        vehicle = self.make_vehicle()
        client = self.driver_client(driver)
        client.post("/api/v1/driver/duty/start", {"vehicle_id": str(vehicle.id)}, format="json")
        self.make_trip(driver, vehicle=vehicle)

        response = client.post("/api/v1/driver/duty/end")

        self.assertEqual(response.status_code, 409)

    def test_location_ping_updates_position(self):
        driver = self.make_driver()
        response = self.driver_client(driver).post(
            "/api/v1/driver/location", {"lat": "12.9", "lng": "77.6"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        driver.refresh_from_db()
        self.assertEqual(driver.last_known_lng, Decimal("77.600000"))


@LOCMEM_CACHES
class DriverStatsTests(DriverTestMixin, TestCase):
    def test_today_and_all_time_totals(self):
        driver = self.make_driver()
        now = timezone.now()
        self.make_trip(
            driver, status=TripStatus.COMPLETED, completed_at=now, total_fare=Decimal("85.00"), payment_mode=PaymentMode.COD
        )
        self.make_trip(
            driver, status=TripStatus.COMPLETED, completed_at=now, total_fare=Decimal("100.00"), payment_mode=PaymentMode.PREPAID
        )
        self.make_trip(
            driver, status=TripStatus.COMPLETED, completed_at=now - timedelta(days=3), total_fare=Decimal("50.00")
        )
        self.make_trip(driver, status=TripStatus.CANCELLED, cancelled_at=now)
        self.make_trip(self.make_driver(), status=TripStatus.COMPLETED, completed_at=now)  # someone else's

        stats = self.driver_client(driver).get("/api/v1/driver/stats").json()

        self.assertEqual(stats["today"]["trips_completed"], 2)
        self.assertEqual(stats["today"]["total_fare"], "185.00")
        self.assertEqual(stats["today"]["cod_collected"], "85.00")
        self.assertEqual(stats["today"]["trips_cancelled"], 1)
        self.assertEqual(stats["all_time"]["trips_completed"], 3)
        self.assertEqual(stats["all_time"]["total_fare"], "235.00")
        self.assertEqual(stats["all_time"]["distance_meters"], 3 * 4200)

    def test_empty_stats_are_zeroes_not_nulls(self):
        stats = self.driver_client(self.make_driver()).get("/api/v1/driver/stats").json()
        self.assertEqual(stats["today"], {
            "trips_completed": 0, "trips_cancelled": 0, "total_fare": "0.00", "earnings": "0.00",
            "cod_collected": "0.00", "distance_meters": 0,
        })

    def test_utc_offset_moves_the_day_boundary(self):
        driver = self.make_driver()
        # Frozen "now": 20:00 UTC on Sep 20 == 01:30 IST on Sep 21.
        now = datetime(2026, 9, 20, 20, 0, tzinfo=dt_timezone.utc)
        # Completed 10:00 UTC Sep 20 == 15:30 IST Sep 20: the same day as
        # "now" in UTC, but the *previous* day for a driver in IST.
        self.make_trip(driver, status=TripStatus.COMPLETED, completed_at=now.replace(hour=10))
        client = self.driver_client(driver)

        with patch("drivers.views.timezone.now", return_value=now):
            utc_day = client.get("/api/v1/driver/stats", {"utc_offset_minutes": 0}).json()
            ist_day = client.get("/api/v1/driver/stats", {"utc_offset_minutes": 330}).json()

        self.assertEqual(utc_day["today"]["trips_completed"], 1)
        self.assertEqual(ist_day["today"]["trips_completed"], 0)
        self.assertEqual(ist_day["all_time"]["trips_completed"], 1)

    def test_rejects_an_absurd_offset(self):
        response = self.driver_client(self.make_driver()).get("/api/v1/driver/stats", {"utc_offset_minutes": 5000})
        self.assertEqual(response.status_code, 400)

    def test_requires_a_driver_token(self):
        self.assertEqual(APIClient().get("/api/v1/driver/stats").status_code, 401)
