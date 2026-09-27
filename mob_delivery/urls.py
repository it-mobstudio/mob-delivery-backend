"""
URL configuration for mob_delivery project.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from core.openapi.views import RedocView

urlpatterns = [
    # The operations console is the admin people use; Django's own admin
    # stays underneath it for raw data.
    path("admin/db/", admin.site.urls),
    path("admin/", include("console.urls")),
    path("api/v1/auth/", include("accounts.urls")),
    path("api/v1/", include("core.urls")),
    path("api/v1/", include("drivers.urls")),
    path("api/v1/", include("trips.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", RedocView.as_view(), name="redoc"),
    # The customer booking web app lives at the site root (it used to be under
    # /book/ — old links still work). Last, so it never shadows anything above.
    re_path(r"^book/(?P<rest>.*)$", RedirectView.as_view(url="/%(rest)s", query_string=True, permanent=True)),
    path("", include("booking.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
