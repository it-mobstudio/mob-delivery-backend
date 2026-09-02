import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from PIL import Image

from .constants import UploadPurpose

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
DOCUMENT_EXTENSIONS = IMAGE_EXTENSIONS | {"pdf"}

# Where each purpose's files land in the (Azure Blob or local) storage tree:
# {company_id}/{segment}/{uuid}.{ext}
PURPOSE_PATH_SEGMENT = {
    UploadPurpose.VEHICLE_TYPE_ICON: "vehicle-types",
    UploadPurpose.VEHICLE_PHOTO: "vehicles",
    UploadPurpose.VEHICLE_DOCUMENT: "vehicle-documents",
    UploadPurpose.DRIVER_DOCUMENT: "driver-documents",
    UploadPurpose.TRIP_PHOTO: "trip-photos",
    UploadPurpose.SHIFT_PHOTO: "shift-photos",
    UploadPurpose.DAMAGE_PHOTO: "damage-photos",
}

PURPOSE_ALLOWED_EXTENSIONS = {
    UploadPurpose.VEHICLE_TYPE_ICON: IMAGE_EXTENSIONS,
    UploadPurpose.VEHICLE_PHOTO: IMAGE_EXTENSIONS,
    UploadPurpose.VEHICLE_DOCUMENT: DOCUMENT_EXTENSIONS,
    UploadPurpose.DRIVER_DOCUMENT: DOCUMENT_EXTENSIONS,
    # Driver-submitted operational photos — images only, same set as
    # VEHICLE_PHOTO, no PDF (unlike the *_DOCUMENT purposes above).
    UploadPurpose.TRIP_PHOTO: IMAGE_EXTENSIONS,
    UploadPurpose.SHIFT_PHOTO: IMAGE_EXTENSIONS,
    UploadPurpose.DAMAGE_PHOTO: IMAGE_EXTENSIONS,
}


def _extension(filename):
    return Path(filename).suffix.lstrip(".").lower()


def validate_upload(file, purpose):
    max_size = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if file.size > max_size:
        raise ValidationError(f"File exceeds the {settings.MAX_UPLOAD_SIZE_MB}MB limit.")

    ext = _extension(file.name)
    allowed = PURPOSE_ALLOWED_EXTENSIONS.get(purpose, IMAGE_EXTENSIONS)
    if ext not in allowed:
        raise ValidationError(
            f"Unsupported file type '.{ext}' for purpose '{purpose}'. Allowed: {sorted(allowed)}."
        )

    if ext != "pdf":
        try:
            Image.open(file).verify()
        except Exception:
            # Pillow raises different exception types for different kinds of
            # corrupt/invalid image data (UnidentifiedImageError, SyntaxError,
            # OSError, ...) — all of them mean the same thing to our caller.
            raise ValidationError("File is not a valid image.")
        finally:
            file.seek(0)


def store_upload(file, purpose, company_id):
    """Build once, use for all three image needs (VehicleType icons, Vehicle
    photos, VehicleDocument scans) — validates, uploads via whatever storage
    backend is configured (Azure Blob or local filesystem in dev), and
    returns the file's URL.
    """
    validate_upload(file, purpose)
    ext = _extension(file.name)
    segment = PURPOSE_PATH_SEGMENT.get(purpose, "misc")
    path = f"{company_id}/{segment}/{uuid.uuid4()}.{ext}"
    saved_path = default_storage.save(path, file)
    return default_storage.url(saved_path)
