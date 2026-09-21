from django.contrib import admin

from .models import Driver, DriverKyc, Vehicle, VehicleDocument, VehicleType, WalletTransaction


class DriverKycInline(admin.StackedInline):
    model = DriverKyc
    extra = 0
    can_delete = False


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "phone_number",
        "company",
        "account_status",
        "aadhar_status",
        "dl_status",
        "police_status",
    )
    list_filter = (
        "account_status",
        "kyc__aadhar_status",
        "kyc__dl_status",
        "kyc__police_status",
        "company",
    )
    search_fields = ("full_name", "phone_number")
    inlines = [DriverKycInline]

    @admin.display(description="Aadhar")
    def aadhar_status(self, driver):
        return driver.kyc.aadhar_status

    @admin.display(description="DL")
    def dl_status(self, driver):
        return driver.kyc.dl_status

    @admin.display(description="Police")
    def police_status(self, driver):
        return driver.kyc.police_status


@admin.register(VehicleType)
class VehicleTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "company", "status", "default_capacity_kg")
    list_filter = ("category", "status", "company")
    search_fields = ("name",)


class VehicleDocumentInline(admin.TabularInline):
    model = VehicleDocument
    extra = 0


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("registration_number", "vehicle_type", "company", "status", "capacity_kg")
    list_filter = ("status", "company", "vehicle_type__category")
    search_fields = ("registration_number",)
    inlines = [VehicleDocumentInline]


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    """The ledger is append-only — corrections are new ADJUSTMENT rows made
    through the API (POST /drivers/{id}/wallet/transactions), so here it's
    look-don't-touch."""

    list_display = ("created_at", "driver", "kind", "amount", "balance_after", "reference")
    list_filter = ("kind", "company")
    search_fields = ("driver__full_name", "driver__phone_number", "reference", "description")
    readonly_fields = [f.name for f in WalletTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
