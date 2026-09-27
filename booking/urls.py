from django.urls import path

from . import views

app_name = "booking"

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login, name="login"),
    path("logout/", views.logout, name="logout"),
    path("trips/", views.trips, name="trips"),
    path("trips/<uuid:pk>/", views.trip, name="trip"),
    path("profile/", views.profile, name="profile"),
    path("api/web/otp/request/", views.api_otp_request, name="api_otp_request"),
    path("api/web/otp/verify/", views.api_otp_verify, name="api_otp_verify"),
    path("api/web/places/", views.api_places, name="api_places"),
    path("api/web/trips/", views.api_trips, name="api_trips"),
    path("api/web/profile/", views.api_profile, name="api_profile"),
    path("api/web/place/", views.api_place, name="api_place"),
    path("api/web/saved/", views.api_saved, name="api_saved"),
    path("api/web/saved/<uuid:pk>/", views.api_saved_place, name="api_saved_place"),
    path("api/web/reverse/", views.api_reverse, name="api_reverse"),
    path("api/web/nearby/", views.api_nearby, name="api_nearby"),
    path("api/web/options/", views.api_options, name="api_options"),
    path("api/web/book/", views.api_book, name="api_book"),
    path("api/web/trips/<uuid:pk>/", views.api_trip, name="api_trip"),
    path("api/web/trips/<uuid:pk>/cancel/", views.api_cancel, name="api_cancel"),
]
