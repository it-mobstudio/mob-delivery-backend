"""Importing this package documents every endpoint (each module calls document()).
Add a module here when a new area of the API appears."""

from . import (  # noqa: F401
    company_auth,
    driver_auth,
    driver_duty,
    driver_earnings,
    driver_payments,
    driver_profile,
    driver_trips,
    driver_vehicles,
    drivers_admin,
    fleet,
    trips,
    uploads_webhooks,
)
