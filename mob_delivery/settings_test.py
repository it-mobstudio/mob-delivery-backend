"""Settings overrides used only when running the test suite (pytest -p
pytest_django, DJANGO_SETTINGS_MODULE pointed here by pytest.ini). Keeps
CELERY_TASK_ALWAYS_EAGER etc. out of the real settings module so it can
never accidentally ship to production.
"""

from .settings import *  # noqa: F401,F403

# Celery tasks run synchronously, in-process, during tests — no broker/
# worker required, and task assertions can run inline instead of racing a
# background worker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Keep OTP requests fast and deterministic in tests regardless of what a
# developer's local .env has DEBUG set to.
DRIVER_OTP_DEBUG_RESPONSE = True

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
