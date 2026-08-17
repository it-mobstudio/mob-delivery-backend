from django.contrib import admin

from .models import WebhookDeliveryLog, WebhookEvent


class WebhookDeliveryLogInline(admin.TabularInline):
    model = WebhookDeliveryLog
    extra = 0
    readonly_fields = ("attempt_number", "response_status_code", "response_body", "error_message", "attempted_at")
    can_delete = False


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "order_ref", "status", "attempt_count", "company", "created_at")
    list_filter = ("status", "event_type", "company")
    search_fields = ("order_ref",)
    inlines = [WebhookDeliveryLogInline]
