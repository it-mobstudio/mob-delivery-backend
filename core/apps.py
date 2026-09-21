from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Attach the API documentation to the views (core/openapi/operations/).
        from .openapi import register

        register()
