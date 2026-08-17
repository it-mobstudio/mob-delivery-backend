import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notifications"

    def ready(self):
        import firebase_admin

        if firebase_admin._apps:
            return  # already initialized — avoids double-init under autoreload

        from django.conf import settings

        cred_path = settings.FIREBASE_CREDENTIALS_PATH
        if not cred_path:
            logger.info("FIREBASE_CREDENTIALS_PATH is not set — push notifications are disabled.")
            return

        try:
            from firebase_admin import credentials

            firebase_admin.initialize_app(credentials.Certificate(cred_path))
        except Exception:
            # Must never take the whole app down over a push-notification
            # setup problem — send_push_to_driver already treats every send
            # failure as non-fatal; a missing/bad credentials file at
            # startup gets the same treatment.
            logger.exception("Failed to initialize Firebase Admin SDK — push notifications are disabled.")
