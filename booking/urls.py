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
    path("api/otp/request/", views.api_otp_request, name="api_otp_request"),
    path("api/otp/verify/", views.api_otp_verify, name="api_otp_verify"),
    path("api/places/", views.api_places, name="api_places"),
    path("api/trips/", views.api_trips, name="api_trips"),
    path("api/profile/", views.api_profile, name="api_profile"),
    path("api/place/", views.api_place, name="api_place"),
    path("api/saved/", views.api_saved, name="api_saved"),
    path("api/saved/<uuid:pk>/", views.api_saved_place, name="api_saved_place"),
    path("api/reverse/", views.api_reverse, name="api_reverse"),
    path("api/nearby/", views.api_nearby, name="api_nearby"),
    path("api/options/", views.api_options, name="api_options"),
    path("api/book/", views.api_book, name="api_book"),
    path("api/trips/<uuid:pk>/", views.api_trip, name="api_trip"),
    path("api/trips/<uuid:pk>/cancel/", views.api_cancel, name="api_cancel"),
]
