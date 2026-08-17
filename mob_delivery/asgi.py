"""
ASGI config for mob_delivery project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mob_delivery.settings')

# Must be called before importing anything that touches models (channels
# routing -> consumers -> models) so the app registry is ready.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

import sos.routing  # noqa: E402
import tracking.routing  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(tracking.routing.websocket_urlpatterns + sos.routing.websocket_urlpatterns),
    }
)
