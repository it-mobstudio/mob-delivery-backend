from django.urls import path

from .views import auth, dashboard, drivers, finance, fleet, gallery, live, orders, search, settings

app_name = "console"

urlpatterns = [
    path("login/", auth.LoginView.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("switch-company/", auth.switch_company, name="switch_company"),
    path("", dashboard.dashboard, name="dashboard"),
    path("live/", live.live, name="live"),
    path("live/data/", live.live_data, name="live_data"),
    path("search/", search.search, name="search"),
    path("orders/", orders.orders, name="orders"),
    path("orders/<uuid:pk>/", orders.order, name="order"),
    path("orders/<uuid:pk>/cancel/", orders.order_cancel, name="order_cancel"),
    path("orders/<uuid:pk>/retry/", orders.order_retry, name="order_retry"),
    path("orders/<uuid:pk>/assign/", orders.order_assign, name="order_assign"),
    path("orders/<uuid:pk>/notes/", orders.order_notes, name="order_notes"),
    path("orders/<uuid:pk>/voice/", orders.order_voice, name="order_voice"),
    path("gallery/", gallery.gallery, name="gallery"),
    path("drivers/", drivers.drivers, name="drivers"),
    path("drivers/<uuid:pk>/", drivers.driver, name="driver"),
    path("drivers/<uuid:pk>/kyc/", drivers.driver_kyc, name="driver_kyc"),
    path("drivers/<uuid:pk>/status/", drivers.driver_status, name="driver_status"),
    path("drivers/<uuid:pk>/wallet/", drivers.driver_wallet, name="driver_wallet"),
    path("kyc/", drivers.kyc_queue, name="kyc"),
    path("kyc/reject/", drivers.kyc_reject, name="kyc_reject"),
    path("vehicles/", fleet.vehicles, name="vehicles"),
    path("vehicles/<uuid:pk>/", fleet.vehicle, name="vehicle"),
    path("fares/", fleet.fares, name="fares"),
    path("payouts/", finance.payouts, name="payouts"),
    path("ledger/", finance.ledger, name="ledger"),
    path("companies/", settings.companies, name="companies"),
    path("team/", settings.team, name="team"),
    path("team/save/", settings.team_save, name="team_save"),
    path("api-clients/", settings.api_clients, name="api_clients"),
]
