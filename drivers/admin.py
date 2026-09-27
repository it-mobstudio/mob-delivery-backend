from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.db.models import Count, OuterRef, Q, Subquery, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.html import format_html, format_html_join

from core.admin_utils import (
    CompanyScopedAdmin,
    admin_link,
    badge,
    export_csv_action,
    gallery,
    is_platform_admin,
    map_link,
    money,
    thumb,
)
from core.choices import DriverAccountStatus, TripStatus, VerificationStatus, WalletTransactionKind
from core.exceptions import DomainError

from .models import Driver, DriverKyc, Vehicle, VehicleDocument, VehiclePhoto, VehicleType, WalletTransaction
from .services import DriverKycService, DriverService
from .wallet import WalletService

# -- drivers -------------------------------------------------------------------------


class ReviewFilter(admin.SimpleListFilter):
    """The two queues an operator works through: documents to review, and
    licences about to lapse."""

    title = "needs review"
    parameter_name = "review"

    def lookups(self, request, model_admin):
        return [("pending", "Documents waiting for review"), ("expiring", "Licence expiring in 30 days"),
                ("rejected", "Has a rejected document")]

    def queryset(self, request, queryset):
        today = timezone.localdate()
        if self.value() == "pending":
            return queryset.filter(
                Q(kyc__aadhar_status=VerificationStatus.PENDING, kyc__aadhar_doc_url__isnull=False)
                | Q(kyc__dl_status=VerificationStatus.PENDING, kyc__dl_doc_url__isnull=False)
                | Q(kyc__police_status=VerificationStatus.PENDING, kyc__police_doc_url__isnull=False)
            )
        if self.value() == "expiring":
            return queryset.filter(kyc__dl_status=VerificationStatus.VERIFIED,
                                   kyc__dl_expiry_date__range=(today, today + timedelta(days=30)))
        if self.value() == "rejected":
            return queryset.filter(
                Q(kyc__aadhar_status=VerificationStatus.REJECTED) | Q(kyc__dl_status=VerificationStatus.REJECTED)
                | Q(kyc__police_status=VerificationStatus.REJECTED)
            )
        return queryset


class DriverKycInline(admin.StackedInline):
    """The documents, side by side with the decision on each. Changing a
    status stamps who decided and when (see DriverAdmin.save_formset)."""

    model = DriverKyc
    extra = 0
    can_delete = False
    verbose_name = "KYC documents"
    verbose_name_plural = "KYC documents"
    fieldsets = (
        ("Aadhaar", {"fields": ("aadhar_docs", ("aadhar_number_last4", "aadhar_status"), "aadhar_rejection_note")}),
        ("Driving licence", {"fields": (
            "dl_docs", ("dl_number", "dl_expiry_date", "dl_status"), "dl_allowed_categories", "dl_rejection_note",
        )}),
        ("Police verification", {"fields": ("police_docs", "police_status", "police_rejection_note")}),
    )
    readonly_fields = ("aadhar_docs", "dl_docs", "police_docs", "aadhar_number_last4")

    @admin.display(description="Scans")
    def aadhar_docs(self, kyc):
        return gallery([("Front", kyc.aadhar_doc_url), ("Back", kyc.aadhar_back_doc_url)], size=180)

    @admin.display(description="Scans")
    def dl_docs(self, kyc):
        return gallery([("Front", kyc.dl_doc_url), ("Back", kyc.dl_back_doc_url)], size=180)

    @admin.display(description="Certificate")
    def police_docs(self, kyc):
        return gallery([("Certificate", kyc.police_doc_url)], size=180)


DRIVER_CSV = export_csv_action(
    [
        ("Name", "full_name"), ("Phone", "phone_number"), ("Email", "email"), ("City", "city"),
        ("Company", "company.name"), ("Account", "account_status"), ("Onboarding", "onboarding_status"),
        ("Online", "is_online"), ("Aadhaar", "kyc.aadhar_status"), ("Licence", "kyc.dl_status"),
        ("Licence expiry", "kyc.dl_expiry_date"), ("Police", "kyc.police_status"),
        ("Completed trips", lambda d: getattr(d, "trips_done", "")), ("Wallet balance", lambda d: getattr(d, "balance", "")),
        ("UPI", "payout_upi_id"), ("Joined", "created_at"),
    ],
    "drivers",
)


@admin.register(Driver)
class DriverAdmin(CompanyScopedAdmin):
    list_display = ("avatar", "person", "account", "onboarding", "online", "trips", "wallet", "last_seen", "city", "company")
    list_display_links = ("avatar", "person")
    list_filter = (ReviewFilter, "account_status", "is_online", "kyc__aadhar_status", "kyc__dl_status",
                   "kyc__police_status", "city", "created_at", "company")
    search_fields = ("full_name", "phone_number", "email", "kyc__dl_number", "city", "pincode")
    search_help_text = "Name, phone, email, licence number, city or PIN"
    list_select_related = ("kyc", "company")
    inlines = [DriverKycInline]
    actions = ["approve_kyc", "take_offline", "block_accounts", "reactivate_accounts", DRIVER_CSV]
    ordering = ("-created_at",)
    date_hierarchy = "created_at"

    fieldsets = (
        ("Profile", {"fields": (
            "photo", ("full_name", "phone_number"), ("email", "date_of_birth"), ("address_line", "city", "pincode"),
            ("emergency_contact_name", "emergency_contact_phone"),
        )}),
        ("Status", {"fields": (("account_status", "onboarding"), ("online", "last_location", "last_location_at"), "vehicle")}),
        ("Performance", {"fields": ("performance", "recent_trips")}),
        ("Wallet & payout", {"fields": ("wallet_summary", ("payout_upi_id", "bank_ifsc"), ("bank_account_holder", "bank_account_masked"))}),
    )
    readonly_fields = (
        "photo", "phone_number", "onboarding", "online", "last_location", "last_location_at", "vehicle",
        "performance", "recent_trips", "wallet_summary", "bank_account_masked",
    )

    def has_add_permission(self, request):
        return False  # drivers sign up in the app (or through the fleet API)

    def get_queryset(self, request):
        balance = (
            WalletTransaction.objects.filter(driver=OuterRef("pk")).values("driver")
            .annotate(total=Sum("amount")).values("total")
        )
        return super().get_queryset(request).annotate(
            trips_done=Count("trips", filter=Q(trips__status=TripStatus.COMPLETED), distinct=True),
            balance=Subquery(balance),
        )

    # -- columns -------------------------------------------------------------

    @admin.display(description="")
    def avatar(self, d):
        if d.profile_photo_url:
            return thumb(d.profile_photo_url, 40, d.full_name)
        initial = (d.full_name or "D").strip()[:1].upper()
        return format_html(
            '<span style="display:inline-grid;place-items:center;width:40px;height:40px;border-radius:10px;'
            'background:#eaf2ff;color:#176bff;font-weight:700">{}</span>', initial)

    @admin.display(description="Driver", ordering="full_name")
    def person(self, d):
        return format_html('<div class="mob-stack"><b>{}</b><span class="mob-sub">{}</span></div>',
                           d.full_name or "(no name yet)", d.phone_number)

    @admin.display(description="Account", ordering="account_status")
    def account(self, d):
        return badge(d.account_status, d.get_account_status_display())

    @admin.display(description="Onboarding")
    def onboarding(self, d):
        return badge(d.onboarding_status)

    @admin.display(description="Online", ordering="is_online", boolean=False)
    def online(self, d):
        return badge("active" if d.is_online else "inactive", "On duty" if d.is_online else "Off duty")

    @admin.display(description="Trips", ordering="trips_done")
    def trips(self, d):
        url = reverse("admin:trips_trip_changelist") + f"?driver__id__exact={d.pk}"
        return format_html('<a href="{}">{}</a>', url, getattr(d, "trips_done", 0))

    @admin.display(description="Wallet", ordering="balance")
    def wallet(self, d):
        url = reverse("admin:drivers_wallettransaction_changelist") + f"?driver__id__exact={d.pk}"
        return format_html('<a href="{}">{}</a>', url, money(getattr(d, "balance", None) or 0))

    @admin.display(description="Last seen", ordering="last_location_at")
    def last_seen(self, d):
        if not d.last_location_at:
            return mark_safe('<span class="mob-muted">never</span>')
        from django.utils.timesince import timesince

        return f"{timesince(d.last_location_at).split(',')[0]} ago"

    # -- detail --------------------------------------------------------------

    @admin.display(description="Photo")
    def photo(self, d):
        return thumb(d.profile_photo_url, 120, d.full_name)

    @admin.display(description="Last location")
    def last_location(self, d):
        return map_link(d.last_known_lat, d.last_known_lng)

    @admin.display(description="Current vehicle")
    def vehicle(self, d):
        vehicle = Vehicle.objects.filter(pk=d.current_vehicle_id).first() if d.current_vehicle_id else None
        return admin_link(vehicle, vehicle.registration_number if vehicle else None)

    @admin.display(description="Numbers")
    def performance(self, d):
        stats = d.trips.aggregate(
            done=Count("id", filter=Q(status=TripStatus.COMPLETED)),
            cancelled=Count("id", filter=Q(status=TripStatus.CANCELLED, cancelled_by="driver")),
            earned=Sum("driver_earning"),
        )
        return format_html(
            "<b>{}</b> completed · <b>{}</b> cancelled by them · <b>{}</b> earned in total",
            stats["done"], stats["cancelled"], money(stats["earned"] or 0),
        )

    @admin.display(description="Recent trips")
    def recent_trips(self, d):
        rows = d.trips.order_by("-created_at")[:8]
        if not rows:
            return mark_safe('<span class="mob-muted">No trips yet</span>')
        body = format_html_join(
            "", "<tr><td>{}</td><td>{}</td><td>{}</td><td style='text-align:right'>{}</td></tr>",
            ((admin_link(t, t.order_number or str(t.pk)[:8]), badge(t.status), timezone.localtime(t.created_at).strftime("%d %b, %I:%M %p"),
              money(t.driver_earning) if t.driver_earning else "—") for t in rows),
        )
        more = reverse("admin:trips_trip_changelist") + f"?driver__id__exact={d.pk}"
        return format_html('<table class="mob-table"><thead><tr><th>Order</th><th>Status</th><th>Booked</th>'
                           '<th style="text-align:right">Earned</th></tr></thead><tbody>{}</tbody></table>'
                           '<p><a href="{}">All trips of this driver →</a></p>', body, more)

    @admin.display(description="Balance")
    def wallet_summary(self, d):
        url = reverse("admin:drivers_wallettransaction_changelist") + f"?driver__id__exact={d.pk}"
        add = reverse("admin:drivers_wallettransaction_add") + f"?driver={d.pk}"
        return format_html('<b style="font-size:18px">{}</b> &nbsp; <a href="{}">Ledger →</a> &nbsp; '
                           '<a href="{}">Record a payout / bonus / fine →</a>', money(WalletService.balance(d)), url, add)

    @admin.display(description="Bank account")
    def bank_account_masked(self, d):
        number = d.bank_account_number or ""
        return f"•••• {number[-4:]}" if number else "—"

    # -- saving: stamp KYC decisions ------------------------------------------

    def save_formset(self, request, form, formset, change):
        if formset.model is DriverKyc:
            for kyc_form in formset.forms:
                kyc = kyc_form.instance
                for part in ("aadhar", "dl", "police"):
                    if f"{part}_status" in kyc_form.changed_data:
                        setattr(kyc, f"{part}_verified_at", timezone.now())
                        setattr(kyc, f"{part}_verified_by", request.user.pk)
                        if getattr(kyc, f"{part}_status") != VerificationStatus.REJECTED:
                            setattr(kyc, f"{part}_rejection_note", None)
        super().save_formset(request, form, formset, change)

    # -- actions -------------------------------------------------------------

    @admin.action(description="Approve all KYC documents")
    def approve_kyc(self, request, queryset):
        approved, skipped = 0, []
        for driver in queryset.select_related("kyc"):
            kyc = driver.kyc
            if not kyc.dl_expiry_date:
                skipped.append(f"{driver}: the licence has no expiry date — open the driver and add it first")
                continue
            DriverKycService.verify_aadhar(driver, VerificationStatus.VERIFIED, request.user.pk)
            DriverKycService.verify_police(driver, VerificationStatus.VERIFIED, request.user.pk)
            DriverKycService.verify_dl(
                driver, VerificationStatus.VERIFIED, request.user.pk, expiry_date=kyc.dl_expiry_date,
                allowed_categories=kyc.dl_allowed_categories or [c for c, _ in _categories()],
            )
            approved += 1
        if approved:
            self.message_user(request, f"Approved {approved} driver(s).", messages.SUCCESS)
        for line in skipped[:10]:
            self.message_user(request, line, messages.WARNING)

    @admin.action(description="Take off duty")
    def take_offline(self, request, queryset):
        self._each(request, queryset, DriverService.go_offline, "Taken off duty")

    @admin.action(description="Block account (can't log in or take trips)")
    def block_accounts(self, request, queryset):
        def block(driver):
            if DriverService.has_active_trip(driver):
                raise DomainError("DRIVER_HAS_ACTIVE_TRIP", "is on a trip right now")
            driver.account_status = DriverAccountStatus.DISABLED
            driver.is_online = False
            driver.save(update_fields=["account_status", "is_online"])

        self._each(request, queryset, block, "Blocked")

    @admin.action(description="Reactivate account")
    def reactivate_accounts(self, request, queryset):
        def reactivate(driver):
            driver.account_status = DriverAccountStatus.ACTIVE
            driver.save(update_fields=["account_status"])

        self._each(request, queryset, reactivate, "Reactivated")

    def _each(self, request, queryset, fn, verb):
        done, refused = 0, []
        for driver in queryset:
            try:
                fn(driver)
                done += 1
            except DomainError as exc:
                refused.append(f"{driver}: {exc.detail}")
        if done:
            self.message_user(request, f"{verb}: {done} driver(s).", messages.SUCCESS)
        for line in refused[:10]:
            self.message_user(request, line, messages.WARNING)

    def has_delete_permission(self, request, obj=None):
        return False  # drivers carry trip and wallet history; block them instead


def _categories():
    from core.choices import VehicleCategory

    return VehicleCategory.choices


# -- vehicles --------------------------------------------------------------------------


@admin.register(VehicleType)
class VehicleTypeAdmin(CompanyScopedAdmin):
    list_display = ("icon", "name", "category", "state", "base_fare", "per_km_rate", "per_min_rate", "min_fare",
                    "default_capacity_kg", "company")
    list_display_links = ("icon", "name")
    list_editable = ("base_fare", "per_km_rate", "per_min_rate", "min_fare")
    list_filter = ("category", "status", "company")
    search_fields = ("name",)
    fieldsets = (
        (None, {"fields": ("name", "category", "status", "default_capacity_kg", "icon_image_url", "icon_preview")}),
        ("Fare card", {"description": "What a trip costs: base + km × rate + minutes × rate, never below the minimum.",
                       "fields": (("base_fare", "min_fare"), ("per_km_rate", "per_min_rate"))}),
    )
    readonly_fields = ("icon_preview",)

    @admin.display(description="")
    def icon(self, vt):
        return thumb(vt.icon_image_url, 36, vt.name)

    @admin.display(description="Preview")
    def icon_preview(self, vt):
        return thumb(vt.icon_image_url, 96, vt.name)

    @admin.display(description="Status", ordering="status")
    def state(self, vt):
        return badge(vt.status)


class VehiclePhotoInline(admin.TabularInline):
    model = VehiclePhoto
    extra = 0
    fields = ("preview", "url")
    readonly_fields = ("preview",)

    @admin.display(description="")
    def preview(self, photo):
        return thumb(photo.url, 72)


class VehicleDocumentInline(admin.TabularInline):
    model = VehicleDocument
    extra = 0
    fields = ("document_type", "preview", "file_url", "expiry_date", "expiry")
    readonly_fields = ("preview", "expiry")

    @admin.display(description="")
    def preview(self, doc):
        return thumb(doc.file_url, 56)

    @admin.display(description="")
    def expiry(self, doc):
        if not doc.expiry_date:
            return ""
        days = (doc.expiry_date - timezone.localdate()).days
        if days < 0:
            return badge("rejected", "Expired")
        if days <= 30:
            return badge("pending", f"{days} days left")
        return badge("verified", "Valid")


@admin.register(Vehicle)
class VehicleAdmin(CompanyScopedAdmin):
    list_display = ("picture", "registration_number", "vehicle_type", "owner", "driving", "state", "capacity_kg", "company")
    list_display_links = ("picture", "registration_number")
    list_filter = ("status", "vehicle_type__category", "vehicle_type", "company")
    search_fields = ("registration_number", "owner_driver__full_name", "owner_driver__phone_number")
    search_help_text = "Registration number, or the owner's name / phone"
    list_select_related = ("vehicle_type", "owner_driver", "company")
    inlines = [VehiclePhotoInline, VehicleDocumentInline]
    readonly_fields = ("picture_large", "driving")
    fields = ("picture_large", "registration_number", "vehicle_type", "capacity_kg", "status", "photo_url",
              "owner_driver", "driving")
    autocomplete_fields = ("owner_driver",)

    @admin.display(description="")
    def picture(self, v):
        return thumb(v.photo_url or getattr(v.photos.first(), "url", None), 44, v.registration_number)

    @admin.display(description="Photo")
    def picture_large(self, v):
        return thumb(v.photo_url, 160, v.registration_number)

    @admin.display(description="Owner", ordering="owner_driver__full_name")
    def owner(self, v):
        return admin_link(v.owner_driver, v.owner_driver.full_name if v.owner_driver else None) if v.owner_driver else "Company"

    @admin.display(description="Being driven by")
    def driving(self, v):
        driver = Driver.objects.filter(pk=v.current_driver_id, is_online=True).first() if v.current_driver_id else None
        return admin_link(driver, driver.full_name if driver else None) if driver else mark_safe('<span class="mob-muted">—</span>')

    @admin.display(description="Status", ordering="status")
    def state(self, v):
        return badge(v.status)


# -- wallet -----------------------------------------------------------------------------

MANUAL_KINDS = [(k, label) for k, label in WalletTransactionKind.choices if k != WalletTransactionKind.TRIP_EARNING]


class ManualEntryForm(forms.ModelForm):
    kind = forms.ChoiceField(choices=MANUAL_KINDS, help_text="Payout, bonus and penalty take a positive amount; "
                                                             "an adjustment's sign is yours.")

    class Meta:
        model = WalletTransaction
        fields = ("driver", "kind", "amount", "description", "reference")


@admin.register(WalletTransaction)
class WalletTransactionAdmin(CompanyScopedAdmin):
    """The ledger is append-only: rows are never edited or deleted — a mistake
    is fixed with a new adjustment. Adding here goes through the same
    WalletService the API uses, so balances and payout checks stay right."""

    list_display = ("created_at", "driver_link", "entry", "amount_col", "balance_after", "description", "trip", "company")
    list_filter = ("kind", "created_at", "company")
    search_fields = ("driver__full_name", "driver__phone_number", "reference", "description")
    search_help_text = "Driver name / phone, reference or description"
    date_hierarchy = "created_at"
    list_select_related = ("driver", "company")
    ordering = ("-created_at",)
    autocomplete_fields = ("driver",)
    actions = [export_csv_action(
        [("When", "created_at"), ("Driver", "driver.full_name"), ("Phone", "driver.phone_number"), ("Kind", "kind"),
         ("Amount", "amount"), ("Balance after", "balance_after"), ("Description", "description"),
         ("Reference", "reference"), ("Trip", "trip_id")],
        "wallet-ledger",
    )]

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = ManualEntryForm
        return super().get_form(request, obj, **kwargs)

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("driver", "kind", "amount", "description", "reference")
        return [f.name for f in WalletTransaction._meta.fields if f.name not in ("is_deleted", "deleted_at")]

    def get_readonly_fields(self, request, obj=None):
        return self.get_fields(request, obj) if obj else ()

    def get_changeform_initial_data(self, request):
        return {"driver": request.GET.get("driver")}

    def save_model(self, request, obj, form, change):
        entry = WalletService.record_manual(
            obj.driver, form.cleaned_data["kind"], form.cleaned_data["amount"],
            description=form.cleaned_data.get("description", ""), reference=form.cleaned_data.get("reference", ""),
            created_by=request.user.pk,
        )
        obj.pk, obj.amount, obj.balance_after, obj.company_id = entry.pk, entry.amount, entry.balance_after, entry.company_id
        obj._state.adding = False

    def add_view(self, request, form_url="", extra_context=None):
        try:
            return super().add_view(request, form_url, extra_context)
        except DomainError as exc:
            self.message_user(request, exc.detail, messages.ERROR)
            from django.http import HttpResponseRedirect

            return HttpResponseRedirect(request.get_full_path())

    def has_change_permission(self, request, obj=None):
        return obj is None and super().has_change_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Driver", ordering="driver__full_name")
    def driver_link(self, t):
        return admin_link(t.driver, t.driver.full_name or t.driver.phone_number)

    @admin.display(description="Kind", ordering="kind")
    def entry(self, t):
        return badge(t.kind, t.get_kind_display())

    @admin.display(description="Amount", ordering="amount")
    def amount_col(self, t):
        colour = "#0e7a4e" if t.amount >= 0 else "#b3372a"
        return format_html('<b style="color:{}">{}{}</b>', colour, "+" if t.amount >= 0 else "−", money(abs(t.amount)))

    @admin.display(description="Trip")
    def trip(self, t):
        if not t.trip_id:
            return "—"
        from trips.models import Trip

        trip = Trip.objects.filter(pk=t.trip_id).only("order_number").first()
        return admin_link(trip, trip.order_number) if trip else "—"

