"""Drivers registering their own vehicles, with pictures."""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Company
from core.choices import VehicleCategory, VehicleStatus, VehicleTypeStatus
from core.constants import DRIVER_MAX_VEHICLES, VEHICLE_MAX_PHOTOS
from core.testing import LOCMEM_CACHES, DriverTestMixin, image_file
from drivers.models import Driver, Vehicle, VehiclePhoto, VehicleType

MINE = "/api/v1/driver/my-vehicles"


@LOCMEM_CACHES
class DriverVehicleTests(DriverTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.driver = self.make_driver()
        self.mine = self.driver_client(self.driver)

    def add(self, client=None, *, photos=2, **fields):
        data = {"vehicle_type_id": str(self.vehicle_type.id), "registration_number": "KA05MN7788", **fields}
        if photos:
            data["photos"] = [image_file(f"p{i}.jpg") for i in range(photos)]
        return (client or self.mine).post(MINE, data, format="multipart")

    def created(self, **kwargs):
        response = self.add(**kwargs)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    # -- registering ------------------------------------------------------------------
    def test_a_driver_registers_a_vehicle_with_pictures(self):
        body = self.created(registration_number="ka05mn7788")

        self.assertEqual(body["registration_number"], "KA05MN7788", "upper-cased")
        self.assertEqual(body["vehicle_type"]["name"], "Bike")
        self.assertEqual(body["capacity_kg"], "20.00", "the vehicle type's default")
        self.assertEqual(body["status"], "active")
        self.assertFalse(body["is_current"])
        self.assertEqual(len(body["photos"]), 2)
        self.assertTrue(all(p["url"].startswith("http://testserver/media/") for p in body["photos"]), "absolute URLs the phone can load")
        self.assertEqual(body["photo_url"], body["photos"][0]["url"], "the first picture is the vehicle's main one")
        vehicle = Vehicle.objects.get(pk=body["id"])
        self.assertEqual((vehicle.owner_driver_id, vehicle.company_id), (self.driver.id, self.company.id))

    def test_pictures_are_optional_and_json_works_without_them(self):
        response = self.mine.post(
            MINE, {"vehicle_type_id": str(self.vehicle_type.id), "registration_number": "KA01AA0001", "capacity_kg": "35.5"}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()["photos"], response.json()["photo_url"], response.json()["capacity_kg"]), ([], None, "35.50"))

    def test_the_input_is_validated_and_nothing_is_left_behind(self):
        other_company_type = VehicleType.objects.create(
            company=Company.objects.create(name="Other Co"), name="Bike", category=VehicleCategory.TWO_WHEELER, default_capacity_kg=Decimal("20")
        )
        inactive = self.make_vehicle_type("Old", VehicleCategory.TWO_WHEELER, status=VehicleTypeStatus.INACTIVE)
        for problem in (
            {"registration_number": "KA 05 MN"},  # spaces
            {"registration_number": ""},
            {"capacity_kg": "0"},
            {"vehicle_type_id": str(other_company_type.id)},
            {"vehicle_type_id": str(inactive.id)},
            {"vehicle_type_id": "00000000-0000-0000-0000-000000000000"},
        ):
            response = self.add(photos=0, **problem)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "INVALID"), problem)
        self.assertEqual(self.add(photos=VEHICLE_MAX_PHOTOS + 1).status_code, 400, "too many pictures")

        not_an_image = self.mine.post(
            MINE,
            {"vehicle_type_id": str(self.vehicle_type.id), "registration_number": "KA09ZZ0001", "photos": [image_file("ok.jpg"), _text_file()]},
            format="multipart",
        )
        self.assertEqual((not_an_image.status_code, not_an_image.json()["error"]["code"]), (400, "INVALID_UPLOAD"))
        self.assertEqual(Vehicle.objects.filter(owner_driver=self.driver).count(), 0, "one bad picture creates nothing")
        self.assertEqual(VehiclePhoto.objects.count(), 0)

    def test_a_registration_is_unique_within_the_company(self):
        self.created()
        duplicate = self.add(client=self.driver_client(self.make_driver()), photos=0)
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("registration_number", duplicate.json()["error"]["details"])

    def test_there_is_a_limit_on_how_many_vehicles_one_driver_can_register(self):
        for i in range(DRIVER_MAX_VEHICLES):
            Vehicle.objects.create(
                company=self.company, vehicle_type=self.vehicle_type, registration_number=f"KA01XX{i:04d}", capacity_kg=20, owner_driver=self.driver
            )
        response = self.add(photos=0)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "VEHICLE_LIMIT_REACHED"))
        # ...but another driver still can.
        self.assertEqual(self.add(client=self.driver_client(self.make_driver()), photos=0).status_code, 201)

    def test_a_driver_sees_the_companys_active_vehicle_types_to_choose_from(self):
        self.make_vehicle_type("Retired", VehicleCategory.FOUR_WHEELER, status=VehicleTypeStatus.INACTIVE)
        VehicleType.objects.create(
            company=Company.objects.create(name="Other Co"), name="Secret", category=VehicleCategory.TWO_WHEELER, default_capacity_kg=Decimal("5")
        )
        response = self.mine.get("/api/v1/driver/vehicle-types")
        self.assertEqual(response.status_code, 200)
        types = response.json()["vehicle_types"]
        self.assertEqual([t["name"] for t in types], ["Bike"])
        self.assertEqual(set(types[0]), {"id", "name", "category", "default_capacity_kg", "icon_image_url"})

    # -- mine, and only mine ----------------------------------------------------------------------
    def test_a_driver_lists_their_own_vehicles_and_nobody_elses(self):
        first = self.created()
        second = self.created(registration_number="KA05MN7789", photos=1)
        listed = self.mine.get(MINE).json()["vehicles"]
        self.assertEqual({v["id"] for v in listed}, {first["id"], second["id"]})

        stranger = self.driver_client(self.make_driver())
        self.assertEqual(stranger.get(MINE).json()["vehicles"], [])
        for method, url in (("get", f"{MINE}/{first['id']}"), ("patch", f"{MINE}/{first['id']}"), ("delete", f"{MINE}/{first['id']}"),
                            ("post", f"{MINE}/{first['id']}/photos")):
            response = getattr(stranger, method)(url)
            self.assertEqual(response.status_code, 404, (method, url))
        self.assertTrue(Vehicle.objects.filter(pk=first["id"]).exists())

    def test_only_drivers_can_use_these_endpoints(self):
        for client in (self.admin_client(), self.api_client_client()):
            self.assertEqual(client.get(MINE).status_code, 403)
            self.assertEqual(client.get("/api/v1/driver/vehicle-types").status_code, 403)
        self.assertEqual(APIClient().get(MINE).status_code, 401)

    def test_a_driver_can_correct_a_vehicle(self):
        vehicle = self.created(photos=0)
        response = self.mine.patch(f"{MINE}/{vehicle['id']}", {"registration_number": "ka05mn9999", "capacity_kg": "42"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["registration_number"], response.json()["capacity_kg"]), ("KA05MN9999", "42.00"))

        other = self.created(registration_number="KA05MN0001", photos=0)
        clash = self.mine.patch(f"{MINE}/{other['id']}", {"registration_number": "KA05MN9999"}, format="json")
        self.assertEqual(clash.status_code, 400)

    # -- pictures -----------------------------------------------------------------------------------------
    def test_pictures_can_be_added_and_removed_and_the_main_one_follows(self):
        vehicle = self.created(photos=2)
        url = f"{MINE}/{vehicle['id']}/photos"

        added = self.mine.post(url, {"photo": image_file("back.jpg")}, format="multipart")
        self.assertEqual(added.status_code, 201, added.content)
        photos = added.json()["photos"]
        self.assertEqual(len(photos), 3)

        first_gone = self.mine.delete(f"{url}/{photos[0]['id']}")
        self.assertEqual(first_gone.status_code, 200)
        self.assertEqual(first_gone.json()["photo_url"], photos[1]["url"], "the next picture becomes the main one")

        for photo in first_gone.json()["photos"]:
            last = self.mine.delete(f"{url}/{photo['id']}")
        self.assertEqual((last.json()["photos"], last.json()["photo_url"]), ([], None))

    def test_a_vehicle_can_hold_only_so_many_pictures_and_only_images(self):
        vehicle = self.created(photos=VEHICLE_MAX_PHOTOS)
        url = f"{MINE}/{vehicle['id']}/photos"
        full = self.mine.post(url, {"photo": image_file("x.jpg")}, format="multipart")
        self.assertEqual((full.status_code, full.json()["error"]["code"]), (409, "PHOTO_LIMIT_REACHED"))

        empty = self.created(registration_number="KA05MN0002", photos=0)
        bad = self.mine.post(f"{MINE}/{empty['id']}/photos", {"photo": _text_file()}, format="multipart")
        self.assertEqual((bad.status_code, bad.json()["error"]["code"]), (400, "INVALID_UPLOAD"))
        self.assertEqual(self.mine.post(f"{MINE}/{empty['id']}/photos", {}, format="multipart").status_code, 400)

    def test_a_picture_of_another_vehicle_cannot_be_removed_through_this_one(self):
        first = self.created(photos=1)
        second = self.created(registration_number="KA05MN0003", photos=1)
        foreign = second["photos"][0]["id"]
        self.assertEqual(self.mine.delete(f"{MINE}/{first['id']}/photos/{foreign}").status_code, 404)
        self.assertTrue(VehiclePhoto.objects.filter(pk=foreign).exists())

    # -- taking one on duty --------------------------------------------------------------------------------------
    def test_own_vehicles_are_offered_to_their_owner_only_next_to_the_companys_fleet(self):
        fleet = self.make_vehicle(registration_number="KA01FLEET01")
        mine = self.created()
        rival = self.driver_client(self.make_driver())

        picker = {v["id"]: v for v in self.mine.get("/api/v1/driver/vehicles").json()["vehicles"]}
        self.assertEqual(set(picker), {str(fleet.id), mine["id"]})
        self.assertTrue(picker[mine["id"]]["is_own"])
        self.assertFalse(picker[str(fleet.id)]["is_own"])
        self.assertTrue(picker[mine["id"]]["photo_url"].startswith("http://testserver/media/"))

        rivals = {v["id"] for v in rival.get("/api/v1/driver/vehicles").json()["vehicles"]}
        self.assertEqual(rivals, {str(fleet.id)}, "another driver's own vehicle is theirs alone")

    def test_a_driver_goes_on_duty_with_their_own_vehicle_and_nobody_else_can(self):
        mine = self.created()
        started = self.mine.post("/api/v1/driver/duty/start", {"vehicle_id": mine["id"], "lat": "12.97", "lng": "77.59"}, format="json")
        self.assertEqual(started.status_code, 200, started.content)
        current = started.json()["current_vehicle"]
        self.assertEqual(current["id"], mine["id"])
        self.assertTrue(current["photo_url"].startswith("http://testserver/media/"), "the go-on-duty answer carries a loadable picture too")
        ended = self.mine.post("/api/v1/driver/duty/end")
        self.assertTrue(ended.json()["current_vehicle"]["photo_url"].startswith("http://testserver/media/"))
        self.mine.post("/api/v1/driver/duty/start", {"vehicle_id": mine["id"], "lat": "12.97", "lng": "77.59"}, format="json")

        me = self.mine.get("/api/v1/driver/me").json()["current_vehicle"]
        self.assertTrue(me["photo_url"].startswith("http://testserver/media/"), "the profile carries a loadable picture too")
        self.assertTrue(self.mine.get(MINE).json()["vehicles"][0]["is_current"])

        rival = self.driver_client(self.make_driver())
        refused = rival.post("/api/v1/driver/duty/start", {"vehicle_id": mine["id"], "lat": "12.97", "lng": "77.59"}, format="json")
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["error"]["code"], "VEHICLE_IN_USE")

    # -- removing ---------------------------------------------------------------------------------------------------------
    def test_removing_a_vehicle_retires_it_everywhere(self):
        vehicle = self.created()
        self.assertEqual(self.mine.delete(f"{MINE}/{vehicle['id']}").status_code, 204)
        self.assertEqual(self.mine.get(MINE).json()["vehicles"], [])
        self.assertEqual(self.mine.get(f"{MINE}/{vehicle['id']}").status_code, 404)
        self.assertEqual(self.admin_client().get(f"/api/v1/vehicles/{vehicle['id']}").status_code, 404)
        self.assertEqual(self.add(photos=0).status_code, 201, "the registration is free to use again")

    def test_a_vehicle_in_use_cannot_be_removed(self):
        vehicle = self.created()
        self.mine.post("/api/v1/driver/duty/start", {"vehicle_id": vehicle["id"], "lat": "12.97", "lng": "77.59"}, format="json")
        on_duty = self.mine.delete(f"{MINE}/{vehicle['id']}")
        self.assertEqual((on_duty.status_code, on_duty.json()["error"]["code"]), (409, "VEHICLE_ON_DUTY"))

        self.mine.post("/api/v1/driver/duty/end")
        self.make_trip(self.driver, vehicle=Vehicle.objects.get(pk=vehicle["id"]), status="in_progress")
        busy = self.mine.delete(f"{MINE}/{vehicle['id']}")
        self.assertEqual((busy.status_code, busy.json()["error"]["code"]), (409, "VEHICLE_HAS_ACTIVE_TRIP"))
        self.assertTrue(Vehicle.objects.filter(pk=vehicle["id"]).exists())

    # -- the company's view ---------------------------------------------------------------------------------------------------
    def test_the_company_sees_who_registered_a_vehicle_and_its_pictures(self):
        vehicle = self.created()
        admin = self.admin_client()

        detail = admin.get(f"/api/v1/vehicles/{vehicle['id']}").json()
        self.assertEqual(detail["owner_driver_id"], str(self.driver.id))
        self.assertEqual([p["url"] for p in detail["photos"]], [p["url"] for p in vehicle["photos"]])
        self.assertEqual(detail["photo_url"], vehicle["photo_url"])

        listed = {v["id"]: v for v in admin.get("/api/v1/vehicles").json()["results"]}
        self.assertEqual(listed[vehicle["id"]]["owner_driver_id"], str(self.driver.id))

        fleet = self.make_vehicle(registration_number="KA01FLEET02")
        self.assertIsNone(admin.get(f"/api/v1/vehicles/{fleet.id}").json()["owner_driver_id"])

        self.assertEqual(
            admin.patch(f"/api/v1/vehicles/{vehicle['id']}", {"owner_driver_id": str(self.make_driver().id)}, format="json").json()["owner_driver_id"],
            str(self.driver.id),
            "ownership isn't something the fleet API rewrites",
        )
        self.assertEqual(admin.post(f"/api/v1/vehicles/{vehicle['id']}/disable").json()["status"], VehicleStatus.DISABLED)


def _text_file():
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile("notes.jpg", b"definitely not a picture", content_type="image/jpeg")
