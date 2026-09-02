import shutil
import tempfile
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import AdminUser, Company

from .constants import UploadPurpose
from .services import PURPOSE_ALLOWED_EXTENSIONS, validate_upload

NEW_DRIVER_OPERATIONAL_PURPOSES = [
    UploadPurpose.TRIP_PHOTO,
    UploadPurpose.SHIFT_PHOTO,
    UploadPurpose.DAMAGE_PHOTO,
]


def _image_file(name="photo.jpg", fmt="JPEG", content_type="image/jpeg"):
    buf = BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buf, format=fmt)
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type=content_type)


class NewUploadPurposeValidationTests(TestCase):
    """Fix — trip_photo/shift_photo/damage_photo, the three new
    driver-submitted operational-photo purposes. Images only (same set as
    vehicle_photo), no PDF — unlike the *_document purposes."""

    def test_new_purposes_are_registered_choices(self):
        values = {choice for choice, _label in UploadPurpose.choices}
        for purpose in NEW_DRIVER_OPERATIONAL_PURPOSES:
            self.assertIn(purpose, values)

    def test_new_purposes_only_allow_image_extensions_no_pdf(self):
        for purpose in NEW_DRIVER_OPERATIONAL_PURPOSES:
            allowed = PURPOSE_ALLOWED_EXTENSIONS[purpose]
            self.assertEqual(allowed, {"jpg", "jpeg", "png", "webp"})
            self.assertNotIn("pdf", allowed)

    def test_valid_image_passes_validation_for_each_new_purpose(self):
        for purpose in NEW_DRIVER_OPERATIONAL_PURPOSES:
            file = _image_file()
            validate_upload(file, purpose)  # must not raise

    def test_pdf_is_rejected_for_each_new_purpose(self):
        for purpose in NEW_DRIVER_OPERATIONAL_PURPOSES:
            pdf_file = SimpleUploadedFile(
                "doc.pdf", b"%PDF-1.4 fake", content_type="application/pdf"
            )
            with self.assertRaises(ValidationError):
                validate_upload(pdf_file, purpose)


class NewUploadPurposeEndpointTests(TestCase):
    """POST /api/v1/uploads accepts the three new purposes end to end."""

    def setUp(self):
        self.company = Company.objects.create(name="Upload Purpose Endpoint Co")
        self.admin = AdminUser.objects.create_user(
            email="uploadpurposeadmin@test.invalid", company=self.company, password="pass12345"
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

    def test_each_new_purpose_is_accepted_and_stored(self):
        for purpose in NEW_DRIVER_OPERATIONAL_PURPOSES:
            media_root = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, media_root, ignore_errors=True)
            with override_settings(MEDIA_ROOT=media_root):
                r = self.client_api.post(
                    "/api/v1/uploads",
                    {"file": _image_file(), "purpose": purpose.value},
                    format="multipart",
                )
            self.assertEqual(r.status_code, 201, f"purpose={purpose} response={r.content}")
            self.assertIn("url", r.data)

    def test_unsupported_extension_is_rejected_for_a_new_purpose(self):
        r = self.client_api.post(
            "/api/v1/uploads",
            {
                "file": SimpleUploadedFile("proof.pdf", b"%PDF-1.4 fake", content_type="application/pdf"),
                "purpose": UploadPurpose.DAMAGE_PHOTO.value,
            },
            format="multipart",
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "INVALID_UPLOAD")
