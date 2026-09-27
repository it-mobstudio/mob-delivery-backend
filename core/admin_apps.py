from django.contrib.admin.apps import AdminConfig


class MobAdminConfig(AdminConfig):
    """Django's admin with the operations dashboard (core.admin_site)."""

    default_site = "core.admin_site.MobAdminSite"
