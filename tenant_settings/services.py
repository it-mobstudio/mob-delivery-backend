from .models import TenantSetting

# Every hardcoded-with-a-"move to tenant settings later"-note constant across
# the codebase lands here as its seeded default. Keep this list in sync with
# whatever get_tenant_setting() call sites actually exist.
DEFAULT_SETTINGS = {
    "assignment_window_minutes": "30",  # Trips — get_assignment_candidates
    "assignment_eta_speed_kmph": "25",  # Trips — _estimate_minutes_until_free
    "assignment_eta_dwell_minutes": "5",  # Trips — _estimate_minutes_until_free
    "stationary_radius_meters": "50",  # Tracking — detect_stationary_vehicles
    "stationary_duration_minutes": "10",  # Tracking — detect_stationary_vehicles
    "wrong_direction_degrees": "90",  # Tracking — detect_wrong_direction
    "geofence_meters": "100",  # Trips — complete_stop delivery geofence check
    "document_expiry_warning_days": "14",  # Vehicles — flag_expiring_vehicle_documents
    "shift_variance_threshold_percent": "20",  # Tracking — end_shift KM variance reconciliation
    "gps_offline_minutes": "5",  # Tracking — detect_offline_vehicles
}


def seed_default_settings(company):
    """Call once at company bootstrap (see accounts.management.commands.
    bootstrap_company) — idempotent, safe to call again (e.g. after adding a
    new key to DEFAULT_SETTINGS) since it only fills in what's missing.
    """
    for key, value in DEFAULT_SETTINGS.items():
        TenantSetting.objects.get_or_create(company=company, key=key, defaults={"value": value})


def _cast(raw, default):
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


def get_tenant_setting(company_id, key, default):
    try:
        raw = TenantSetting.objects.get(company_id=company_id, key=key).value
    except TenantSetting.DoesNotExist:
        return default
    return _cast(raw, default)


def set_tenant_setting(company_id, key, value):
    setting, _created = TenantSetting.objects.update_or_create(
        company_id=company_id, key=key, defaults={"value": str(value)}
    )
    return setting
