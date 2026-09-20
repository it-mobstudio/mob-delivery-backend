from django.contrib import admin

from .models import Driver, DriverKyc, Vehicle, VehicleDocument, VehicleType


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
