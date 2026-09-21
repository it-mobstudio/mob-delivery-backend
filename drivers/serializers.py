from datetime import date

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.choices import VehicleCategory, VerificationStatus, WalletTransactionKind
from core.constants import (
    AADHAR_NUMBER_RE,
    BANK_ACCOUNT_NUMBER_RE,
    DRIVER_MAX_AGE_YEARS,
    DRIVER_MIN_AGE_YEARS,
    DRIVING_LICENCE_NUMBER_RE,
    IFSC_RE,
    OTP_RE,
    PHONE_NUMBER_RE,
    PINCODE_RE,
    UPI_ID_RE,
)
from core.serializers import MediaUrlField
from core.uploads import absolute_media_url

from .models import Driver, DriverKyc, Vehicle, WalletTransaction
from .vehicle_serializers import VehicleTypeSummarySerializer


class DriverOtpRequestSerializer(serializers.Serializer):
    phone_number = serializers.CharField()

    def validate_phone_number(self, value):
        value = value.strip()
        if not PHONE_NUMBER_RE.match(value):
            raise serializers.ValidationError("Enter a valid phone number.")
        return value


class DriverOtpVerifySerializer(DriverOtpRequestSerializer):
    otp = serializers.CharField()

    def validate_otp(self, value):
        if not OTP_RE.match(value):
            raise serializers.ValidationError("OTP must be exactly 6 digits.")
        return value


class DriverTokenRefreshSerializer(serializers.Serializer):
    refreshToken = serializers.CharField()


class DriverSerializer(serializers.ModelSerializer):
    """Admin CRUD for the basic profile — verification statuses are managed
    exclusively through the KYC endpoints, not this serializer. The KYC
    fields below are read-only projections of the related DriverKyc row
    (see Driver.kyc) — kept here, flattened, so this endpoint's response
    shape doesn't change just because KYC moved to its own table.
    """

    aadhar_status = serializers.CharField(source="kyc.aadhar_status", read_only=True)
    dl_status = serializers.CharField(source="kyc.dl_status", read_only=True)
    police_status = serializers.CharField(source="kyc.police_status", read_only=True)
    dl_expiry_date = serializers.DateField(source="kyc.dl_expiry_date", read_only=True, allow_null=True)
    dl_allowed_categories = serializers.JSONField(source="kyc.dl_allowed_categories", read_only=True)
    onboarding_status = serializers.CharField(read_only=True)
    profile_photo_url = MediaUrlField()

    class Meta:
        model = Driver
        # full_name became blankable so a driver can sign themselves up before
        # giving it; a company creating a driver still has to supply one.
        extra_kwargs = {"full_name": {"required": True, "allow_blank": False}}
        fields = [
            "id",
            "full_name",
            "phone_number",
            "emergency_contact_name",
            "emergency_contact_phone",
            "email",
            "date_of_birth",
            "city",
            "profile_photo_url",
            "onboarding_status",
            "aadhar_status",
            "dl_status",
            "police_status",
            "dl_expiry_date",
            "dl_allowed_categories",
            "account_status",
            "current_vehicle_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "email",
            "date_of_birth",
            "city",
            "account_status",
            "current_vehicle_id",
            "created_at",
            "updated_at",
        ]

    def validate_phone_number(self, value):
        value = value.strip()
        if not PHONE_NUMBER_RE.match(value):
            raise serializers.ValidationError("Enter a valid phone number.")
        return value

    def validate(self, attrs):
        # DRF's automatic unique-together validation skips UniqueConstraints
        # that have a `condition` (ours is soft-delete-aware), so per-company
        # uniqueness has to be checked explicitly — same reasoning as
        # VehicleSerializer.validate.
        request = self.context.get("request")
        phone_number = attrs.get("phone_number", getattr(self.instance, "phone_number", None))
        if request is not None and phone_number:
            qs = Driver.objects.filter(company_id=request.user.company_id, phone_number=phone_number)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"phone_number": "A driver with this phone number already exists."}
                )
        return attrs


class DriverListSerializer(serializers.ModelSerializer):
    is_eligible_for_assignment = serializers.BooleanField(read_only=True)
    onboarding_status = serializers.CharField(read_only=True)
    aadhar_status = serializers.CharField(source="kyc.aadhar_status", read_only=True)
    dl_status = serializers.CharField(source="kyc.dl_status", read_only=True)
    police_status = serializers.CharField(source="kyc.police_status", read_only=True)

    class Meta:
        model = Driver
        fields = [
            "id",
            "full_name",
            "phone_number",
            "aadhar_status",
            "dl_status",
            "police_status",
            "onboarding_status",
            "account_status",
            "is_eligible_for_assignment",
            "current_vehicle_id",
            "created_at",
            "updated_at",
        ]


class DriverVehicleSummarySerializer(serializers.ModelSerializer):
    vehicle_type = VehicleTypeSummarySerializer(read_only=True)

    class Meta:
        model = Vehicle
        fields = ["id", "registration_number", "capacity_kg", "photo_url", "status", "vehicle_type"]
        read_only_fields = fields


class AadharSummarySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=VerificationStatus.choices, help_text="The company's decision on this document.")
    submitted = serializers.BooleanField(help_text="The driver has uploaded something for it.")
    number_last4 = serializers.CharField(allow_null=True, help_text="Last four digits of the Aadhaar number — the only part that is stored.")
    front_url = serializers.URLField(allow_null=True, help_text="Absolute URL of the front scan.")
    back_url = serializers.URLField(allow_null=True, help_text="Absolute URL of the back scan.")
    rejection_note = serializers.CharField(allow_null=True, help_text="Why the company rejected it (only while `status` is `rejected`).")


class LicenceSummarySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=VerificationStatus.choices, help_text="The company's decision on this document.")
    submitted = serializers.BooleanField(help_text="The driver has uploaded something for it.")
    number = serializers.CharField(allow_null=True, help_text="Licence number as submitted (tidied and upper-cased).")
    expiry_date = serializers.DateField(allow_null=True, help_text="Expiry date. The driver's own claim until the company verifies it, then the company's.")
    front_url = serializers.URLField(allow_null=True, help_text="Absolute URL of the front scan.")
    back_url = serializers.URLField(allow_null=True, help_text="Absolute URL of the back scan (optional to upload).")
    rejection_note = serializers.CharField(allow_null=True, help_text="Why the company rejected it (only while `status` is `rejected`).")


class PoliceSummarySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=VerificationStatus.choices, help_text="The company's decision on this document.")
    submitted = serializers.BooleanField(help_text="The driver has uploaded a certificate.")
    document_url = serializers.URLField(allow_null=True, help_text="Absolute URL of the uploaded certificate.")
    rejection_note = serializers.CharField(allow_null=True, help_text="Why the company rejected it (only while `status` is `rejected`).")


class DriverKycSummarySerializer(serializers.Serializer):
    aadhar = AadharSummarySerializer()
    dl = LicenceSummarySerializer()
    police = PoliceSummarySerializer()


class DriverPayoutSummarySerializer(serializers.Serializer):
    upi_id = serializers.CharField(allow_null=True, help_text="UPI id payouts are sent to.")
    bank_account_holder = serializers.CharField(allow_null=True, help_text="Name on the bank account.")
    bank_account_last4 = serializers.CharField(allow_null=True, help_text="Last four digits of the account number. The full number is never returned.")
    bank_ifsc = serializers.CharField(allow_null=True, help_text="Bank branch IFSC code.")
    is_set = serializers.BooleanField(help_text="A UPI id or a bank account is on file.")


class DriverMeSerializer(serializers.ModelSerializer):
    aadhar_status = serializers.CharField(source="kyc.aadhar_status", read_only=True)
    dl_status = serializers.CharField(source="kyc.dl_status", read_only=True)
    police_status = serializers.CharField(source="kyc.police_status", read_only=True)
    dl_expiry_date = serializers.DateField(source="kyc.dl_expiry_date", read_only=True, allow_null=True)
    dl_allowed_categories = serializers.JSONField(source="kyc.dl_allowed_categories", read_only=True)
    aadhar_rejection_note = serializers.CharField(source="kyc.aadhar_rejection_note", read_only=True, allow_null=True)
    dl_rejection_note = serializers.CharField(source="kyc.dl_rejection_note", read_only=True, allow_null=True)
    police_rejection_note = serializers.CharField(source="kyc.police_rejection_note", read_only=True, allow_null=True)
    is_eligible_for_assignment = serializers.BooleanField(read_only=True)
    is_profile_complete = serializers.BooleanField(read_only=True)
    onboarding_status = serializers.CharField(read_only=True)
    profile_photo_url = MediaUrlField()
    kyc = serializers.SerializerMethodField()
    payout = serializers.SerializerMethodField()
    current_vehicle = serializers.SerializerMethodField()

    class Meta:
        model = Driver
        fields = [
            "id",
            "full_name",
            "phone_number",
            "email",
            "date_of_birth",
            "address_line",
            "city",
            "pincode",
            "profile_photo_url",
            "emergency_contact_name",
            "emergency_contact_phone",
            "account_status",
            "is_profile_complete",
            "onboarding_status",
            "aadhar_status",
            "dl_status",
            "police_status",
            "dl_expiry_date",
            "dl_allowed_categories",
            "aadhar_rejection_note",
            "dl_rejection_note",
            "police_rejection_note",
            "is_eligible_for_assignment",
            "kyc",
            "payout",
            "current_vehicle_id",
            "current_vehicle",
            "is_online",
        ]

    @extend_schema_field(DriverVehicleSummarySerializer(allow_null=True))
    def get_current_vehicle(self, driver):
        if driver.current_vehicle_id is None:
            return None
        vehicle = Vehicle.objects.select_related("vehicle_type").filter(pk=driver.current_vehicle_id).first()
        return DriverVehicleSummarySerializer(vehicle).data if vehicle else None

    @extend_schema_field(DriverKycSummarySerializer)
    def get_kyc(self, driver):
        """What the driver has submitted for each document, so the app can
        show it back to them (and what to fix when one was rejected). The flat
        `*_status` fields above predate this and are kept for older clients."""
        kyc = driver.kyc
        request = self.context.get("request")

        def url(value):
            return absolute_media_url(value, request) or None

        return {
            "aadhar": {
                "status": kyc.aadhar_status,
                "submitted": bool(kyc.aadhar_doc_url),
                "number_last4": kyc.aadhar_number_last4 or None,
                "front_url": url(kyc.aadhar_doc_url),
                "back_url": url(kyc.aadhar_back_doc_url),
                "rejection_note": kyc.aadhar_rejection_note,
            },
            "dl": {
                "status": kyc.dl_status,
                "submitted": bool(kyc.dl_doc_url),
                "number": kyc.dl_number or None,
                "expiry_date": kyc.dl_expiry_date.isoformat() if kyc.dl_expiry_date else None,
                "front_url": url(kyc.dl_doc_url),
                "back_url": url(kyc.dl_back_doc_url),
                "rejection_note": kyc.dl_rejection_note,
            },
            "police": {
                "status": kyc.police_status,
                "submitted": bool(kyc.police_doc_url),
                "document_url": url(kyc.police_doc_url),
                "rejection_note": kyc.police_rejection_note,
            },
        }

    @extend_schema_field(DriverPayoutSummarySerializer)
    def get_payout(self, driver):
        """Where payouts go. The account number is masked: the app only ever
        needs to show which account it is, never the number."""
        number = driver.bank_account_number
        return {
            "upi_id": driver.payout_upi_id or None,
            "bank_account_holder": driver.bank_account_holder or None,
            "bank_account_last4": number[-4:] if number else None,
            "bank_ifsc": driver.bank_ifsc or None,
            "is_set": bool(driver.payout_upi_id or number),
        }


class DriverProfileUpdateSerializer(serializers.ModelSerializer):
    """PATCH /driver/me — the details a driver fills in about themselves.
    Everything is optional (a partial update); what's *required* to count as
    onboarded is Driver.is_profile_complete."""

    class Meta:
        model = Driver
        fields = [
            "full_name",
            "date_of_birth",
            "email",
            "address_line",
            "city",
            "pincode",
            "emergency_contact_name",
            "emergency_contact_phone",
            "payout_upi_id",
            "bank_account_holder",
            "bank_account_number",
            "bank_ifsc",
        ]
        extra_kwargs = {
            "full_name": {"allow_blank": False},
            "emergency_contact_name": {"allow_blank": False},
            "emergency_contact_phone": {"allow_blank": False},
        }

    def validate_full_name(self, value):
        value = " ".join(value.split())
        if len(value) < 2:
            raise serializers.ValidationError("Enter your full name.")
        return value

    def validate_date_of_birth(self, value):
        today = date.today()
        age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
        if age < DRIVER_MIN_AGE_YEARS:
            raise serializers.ValidationError(f"You must be at least {DRIVER_MIN_AGE_YEARS} years old.")
        if age > DRIVER_MAX_AGE_YEARS:
            raise serializers.ValidationError("Enter a valid date of birth.")
        return value

    def validate_emergency_contact_phone(self, value):
        value = value.strip()
        if not PHONE_NUMBER_RE.match(value):
            raise serializers.ValidationError("Enter a valid phone number.")
        if self.instance is not None and value == self.instance.phone_number:
            raise serializers.ValidationError("Use someone else's number — this is who we call if you need help.")
        return value

    def validate_pincode(self, value):
        value = value.strip()
        if value and not PINCODE_RE.match(value):
            raise serializers.ValidationError("Enter a valid 6-digit pincode.")
        return value

    def validate_payout_upi_id(self, value):
        value = value.strip()
        if value and not UPI_ID_RE.match(value):
            raise serializers.ValidationError("Enter a valid UPI id, like name@bank.")
        return value

    def validate_bank_account_number(self, value):
        value = value.strip()
        if value and not BANK_ACCOUNT_NUMBER_RE.match(value):
            raise serializers.ValidationError("Enter a valid bank account number (9–18 digits).")
        return value

    def validate_bank_ifsc(self, value):
        value = value.strip().upper()
        if value and not IFSC_RE.match(value):
            raise serializers.ValidationError("Enter a valid IFSC code, like HDFC0001234.")
        return value

    def validate(self, attrs):
        # A bank account is only usable as a whole.
        def merged(name):
            return attrs.get(name, getattr(self.instance, name, ""))

        bank = [merged("bank_account_holder"), merged("bank_account_number"), merged("bank_ifsc")]
        if any(bank) and not all(bank):
            missing = {
                "bank_account_holder": "Enter the account holder's name.",
                "bank_account_number": "Enter the account number.",
                "bank_ifsc": "Enter the IFSC code.",
            }
            raise serializers.ValidationError(
                {name: message for name, message in missing.items() if not merged(name)}
            )
        return attrs


class DriverAadharSubmitSerializer(serializers.Serializer):
    number = serializers.CharField()
    front = serializers.FileField()
    back = serializers.FileField()

    def validate_number(self, value):
        value = "".join(value.split())
        if not AADHAR_NUMBER_RE.match(value):
            raise serializers.ValidationError("Enter the 12-digit Aadhaar number.")
        return value


class DriverDlSubmitSerializer(serializers.Serializer):
    number = serializers.CharField()
    expiry_date = serializers.DateField()
    front = serializers.FileField()
    back = serializers.FileField(required=False)

    def validate_number(self, value):
        value = " ".join(value.split()).upper()
        if not DRIVING_LICENCE_NUMBER_RE.match(value):
            raise serializers.ValidationError("Enter your driving licence number.")
        return value

    def validate_expiry_date(self, value):
        if value < date.today():
            raise serializers.ValidationError("This licence has expired.")
        return value


class DriverPoliceSubmitSerializer(serializers.Serializer):
    document = serializers.FileField()


class DriverPhotoSerializer(serializers.Serializer):
    photo = serializers.FileField()


class WalletTransactionSerializer(serializers.ModelSerializer):
    type = serializers.SerializerMethodField()
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = WalletTransaction
        fields = [
            "id",
            "kind",
            "kind_label",
            "type",
            "amount",
            "balance_after",
            "description",
            "reference",
            "trip_id",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=["credit", "debit"]))
    def get_type(self, entry):
        return "credit" if entry.amount > 0 else "debit"


class WalletQuerySerializer(serializers.Serializer):
    # Same idea as DriverStatsQuerySerializer: the driver's UTC offset, so
    # "today" starts at their midnight.
    utc_offset_minutes = serializers.IntegerField(required=False, default=0, min_value=-840, max_value=840)


class WalletEntryCreateSerializer(serializers.Serializer):
    """Company-recorded ledger entry — POST /drivers/{id}/wallet/transactions."""

    kind = serializers.ChoiceField(
        choices=[
            (WalletTransactionKind.PAYOUT, "Payout"),
            (WalletTransactionKind.BONUS, "Bonus"),
            (WalletTransactionKind.PENALTY, "Penalty"),
            (WalletTransactionKind.ADJUSTMENT, "Adjustment"),
        ]
    )
    # Positive for everything but an adjustment (which may be negative) —
    # WalletService.record_manual applies the sign.
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    description = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")


class DriverLocationSerializer(serializers.Serializer):
    lat = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90)
    lng = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180)


class DriverDutyOnSerializer(serializers.Serializer):
    vehicle_id = serializers.UUIDField()
    # The driver's first GPS fix, sent with the go-online call itself so
    # they're assignable immediately (see DriverService.go_online).
    lat = serializers.DecimalField(
        max_digits=9, decimal_places=6, min_value=-90, max_value=90, required=False
    )
    lng = serializers.DecimalField(
        max_digits=9, decimal_places=6, min_value=-180, max_value=180, required=False
    )

    def validate(self, attrs):
        if ("lat" in attrs) != ("lng" in attrs):
            raise serializers.ValidationError({"lat": "lat and lng must be sent together."})
        return attrs


class DriverStatsQuerySerializer(serializers.Serializer):
    # The server runs in UTC but a driver's "today" starts at local midnight —
    # the app sends its own UTC offset (minutes east of UTC, e.g. 330 for
    # IST) so the day boundary lands where the driver expects it.
    utc_offset_minutes = serializers.IntegerField(required=False, default=0, min_value=-840, max_value=840)


class DriverKycSerializer(serializers.ModelSerializer):
    aadhar_doc_url = MediaUrlField()
    aadhar_back_doc_url = MediaUrlField()
    dl_doc_url = MediaUrlField()
    dl_back_doc_url = MediaUrlField()
    police_doc_url = MediaUrlField()

    class Meta:
        model = DriverKyc
        fields = [
            "id",
            "aadhar_number_last4",
            "aadhar_doc_url",
            "aadhar_back_doc_url",
            "aadhar_status",
            "aadhar_verified_by",
            "aadhar_verified_at",
            "aadhar_rejection_note",
            "dl_number",
            "dl_doc_url",
            "dl_back_doc_url",
            "dl_status",
            "dl_expiry_date",
            "dl_allowed_categories",
            "dl_verified_by",
            "dl_verified_at",
            "dl_rejection_note",
            "police_doc_url",
            "police_status",
            "police_verified_by",
            "police_verified_at",
            "police_rejection_note",
        ]


class DriverKycDecisionSerializer(serializers.Serializer):
    """Shared shape for the aadhar and police KYC decision endpoints."""

    status = serializers.ChoiceField(choices=[VerificationStatus.VERIFIED, VerificationStatus.REJECTED])
    note = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs["status"] == VerificationStatus.REJECTED and not attrs.get("note"):
            raise serializers.ValidationError({"note": "Required when status is 'rejected'."})
        return attrs


class DriverKycDlSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[VerificationStatus.VERIFIED, VerificationStatus.REJECTED])
    note = serializers.CharField(required=False, allow_blank=True)
    expiry_date = serializers.DateField(required=False)
    allowed_categories = serializers.ListField(
        child=serializers.ChoiceField(choices=VehicleCategory.choices), required=False
    )

    def validate(self, attrs):
        status = attrs["status"]
        if status == VerificationStatus.REJECTED and not attrs.get("note"):
            raise serializers.ValidationError({"note": "Required when status is 'rejected'."})

        if status == VerificationStatus.VERIFIED:
            expiry_date = attrs.get("expiry_date")
            allowed_categories = attrs.get("allowed_categories")
            if not expiry_date:
                raise serializers.ValidationError({"expiry_date": "Required when status is 'verified'."})
            if expiry_date < date.today():
                raise serializers.ValidationError({"expiry_date": "Must be in the future."})
            if not allowed_categories:
                raise serializers.ValidationError({"allowed_categories": "Required when status is 'verified'."})
        return attrs


# -- Response shapes -------------------------------------------------------------
# Hand-built responses (dicts, not model instances). They describe the wire
# format for the API schema (core.openapi) and double as documentation of what
# each field means.


class DriverOtpRequestResultSerializer(serializers.Serializer):
    message = serializers.CharField(help_text="Confirmation that an SMS was sent to the number.")
    otp = serializers.CharField(
        required=False,
        help_text="The six-digit code itself. **Only present on non-production servers** (`DRIVER_OTP_DEBUG_RESPONSE=True`) so login can be tested without an SMS gateway. A production server never returns it.",
    )


class DriverSessionSerializer(serializers.Serializer):
    accessToken = serializers.CharField(help_text="Short-lived JWT (60 minutes by default). Send it as `Authorization: Bearer <accessToken>`.")
    tokenType = serializers.CharField(help_text="Always `Bearer`.")
    expiresInSeconds = serializers.IntegerField(help_text="Lifetime of `accessToken` in seconds.")
    refreshToken = serializers.CharField(help_text="Long-lived JWT (30 days by default). Trade it at `POST /driver/auth/refresh` for a new pair.")
    refreshExpiresInSeconds = serializers.IntegerField(help_text="Lifetime of `refreshToken` in seconds.")
    driverName = serializers.CharField(help_text="The driver's full name (empty for a driver who hasn't given it yet).")
    driver = DriverMeSerializer(help_text="The driver's profile — the same object `GET /driver/me` returns.")


class DriverAvailableVehicleSerializer(DriverVehicleSummarySerializer):
    is_current = serializers.BooleanField(help_text="This is the vehicle the driver is on duty with right now.")

    class Meta(DriverVehicleSummarySerializer.Meta):
        fields = DriverVehicleSummarySerializer.Meta.fields + ["is_current"]
        read_only_fields = fields


class DriverAvailableVehiclesSerializer(serializers.Serializer):
    vehicles = DriverAvailableVehicleSerializer(many=True)


class StatsPeriodSerializer(serializers.Serializer):
    trips_completed = serializers.IntegerField(help_text="Trips finished in the period.")
    trips_cancelled = serializers.IntegerField(help_text="Trips the driver cancelled in the period.")
    total_fare = serializers.DecimalField(max_digits=12, decimal_places=2, help_text="What customers were charged for the completed trips.")
    earnings = serializers.DecimalField(max_digits=12, decimal_places=2, help_text="What the driver earned from them (their wallet credits).")
    cod_collected = serializers.DecimalField(max_digits=12, decimal_places=2, help_text="Fare value of the completed cash-on-delivery trips.")
    distance_meters = serializers.IntegerField(help_text="Route distance of the completed trips.")


class DriverStatsSerializer(serializers.Serializer):
    today = StatsPeriodSerializer(help_text="From the driver's local midnight (per `utc_offset_minutes`) until now.")
    all_time = StatsPeriodSerializer(help_text="Everything since the driver joined.")


class WalletPeriodSerializer(serializers.Serializer):
    earnings = serializers.DecimalField(max_digits=12, decimal_places=2, help_text="Trip earnings plus bonuses in the period.")
    trips = serializers.IntegerField(help_text="Trips that paid the driver in the period.")


class WalletLifetimeSerializer(WalletPeriodSerializer):
    payouts = serializers.DecimalField(max_digits=12, decimal_places=2, help_text="Total the company has paid out so far.")


class WalletDaySerializer(serializers.Serializer):
    date = serializers.DateField(help_text="The driver's local calendar day.")
    earnings = serializers.DecimalField(max_digits=12, decimal_places=2, help_text="Earned that day (zero if nothing).")
    trips = serializers.IntegerField(help_text="Trips that paid the driver that day.")


class WalletSummarySerializer(serializers.Serializer):
    balance = serializers.DecimalField(max_digits=12, decimal_places=2, help_text="What the company currently owes the driver: every credit minus every payout, penalty and negative adjustment.")
    currency = serializers.CharField(help_text="ISO currency code (`INR`).")
    today = WalletPeriodSerializer(help_text="Since the driver's local midnight.")
    week = WalletPeriodSerializer(help_text="Since the driver's local Monday.")
    month = WalletPeriodSerializer(help_text="Since the first of the driver's local month.")
    lifetime = WalletLifetimeSerializer(help_text="Ever.")
    last_7_days = WalletDaySerializer(many=True, help_text="Seven entries, oldest first, ending today; days with no earnings are present with zeroes.")


class WalletAdminSerializer(WalletSummarySerializer):
    recent = WalletTransactionSerializer(many=True, help_text="The driver's twenty most recent ledger entries, newest first.")
