from django.contrib import admin

from .models import TripIssue


@admin.register(TripIssue)
class TripIssueAdmin(admin.ModelAdmin):
    list_display = ("trip", "issue_type", "severity", "status", "company", "created_at")
    list_filter = ("issue_type", "severity", "status", "company")
    search_fields = ("note",)
