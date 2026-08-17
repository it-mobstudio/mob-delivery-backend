"""
URL configuration for mob_delivery project.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/", include("accounts.urls")),
    path("api/v1/", include("uploads.urls")),
    path("api/v1/", include("vehicles.urls")),
    path("api/v1/", include("drivers.urls")),
    path("api/v1/", include("trips.urls")),
    path("api/v1/", include("tracking.urls")),
    path("api/v1/", include("damage_reports.urls")),
    path("api/v1/", include("dashboard.urls")),
    path("api/v1/", include("issues.urls")),
    path("api/v1/", include("webhooks.urls")),
    path("api/v1/", include("sos.urls")),
    path("api/v1/", include("notifications.urls")),
    path("api/v1/", include("tenant_settings.urls")),
    path("api/v1/", include("accounts.admin_urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
