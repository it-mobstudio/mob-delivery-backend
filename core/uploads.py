import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from PIL import Image

from .constants import UPLOAD_IMAGE_EXTENSIONS, UPLOAD_PURPOSE_ALLOWED_EXTENSIONS, UPLOAD_PURPOSE_PATH_SEGMENT


class UploadService:
    """The one reusable upload path for VehicleType icons, Vehicle photos,
    VehicleDocument scans, and Driver KYC documents — validates, stores via
    whatever storage backend is configured (Azure Blob or local filesystem
    in dev), and returns the file's URL. Folded in from a standalone
    `uploads` app: no models, so there was nothing app-shaped about it.
    """

    @staticmethod
    def _extension(filename):
        return Path(filename).suffix.lstrip(".").lower()

    @classmethod
    def validate(cls, file, purpose):
        max_size = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if file.size > max_size:
            raise ValidationError(f"File exceeds the {settings.MAX_UPLOAD_SIZE_MB}MB limit.")

        ext = cls._extension(file.name)
        allowed = UPLOAD_PURPOSE_ALLOWED_EXTENSIONS.get(purpose, UPLOAD_IMAGE_EXTENSIONS)
        if ext not in allowed:
            raise ValidationError(
                f"Unsupported file type '.{ext}' for purpose '{purpose}'. Allowed: {sorted(allowed)}."
            )

        if ext == "pdf":
            head = file.read(5)
            file.seek(0)
            if head != b"%PDF-":
                raise ValidationError("File is not a valid PDF.")
        else:
            try:
                Image.open(file).verify()
            except Exception:
                # Pillow raises different exception types for different kinds
                # of corrupt/invalid image data (UnidentifiedImageError,
                # SyntaxError, OSError, ...) — all mean the same thing here.
                raise ValidationError("File is not a valid image.")
            finally:
                file.seek(0)

    @classmethod
    def store(cls, file, purpose, company_id):
        cls.validate(file, purpose)
        ext = cls._extension(file.name)
        segment = UPLOAD_PURPOSE_PATH_SEGMENT.get(purpose, "misc")
        path = f"{company_id}/{segment}/{uuid.uuid4()}.{ext}"
        saved_path = default_storage.save(path, file)
        return default_storage.url(saved_path)


def absolute_media_url(url, request=None):
    """Makes a stored file URL usable by whoever is asking.

    Azure Blob hands back absolute URLs, which pass through untouched. Local
    dev storage hands back `/media/...`, which a phone can't load (it has no
    idea what host that's relative to) — so it's resolved against the host the
    *request* came in on. That's also why the relative form is what's stored:
    an Android emulator reaches this server as 10.0.2.2, a browser as
    localhost, and each gets a URL that works for it.
    """
    if not url:
        return url
    if url.startswith("/") and request is not None:
        return request.build_absolute_uri(url)
    return url
