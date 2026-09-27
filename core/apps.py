from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Python calls .m4a "audio/mp4a-latm" (a raw AAC stream type). Served
        # with that — and nosniff — iPhones and strict players refuse the
        # dispatcher's voice notes; "audio/mp4" is what an .m4a file is.
        import mimetypes

        mimetypes.add_type("audio/mp4", ".m4a")

        # Attach the API documentation to the views (core/openapi/operations/).
        from .openapi import register

        register()

