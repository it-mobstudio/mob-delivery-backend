import re
from datetime import date

from rest_framework import serializers

from vehicles.models import VehicleCategory

from .models import Driver, VerificationStatus

PHONE_NUMBER_RE = re.compile(r"^\+?[0-9]{10,15}$")
OTP_RE = re.compile(r"^\d{6}$")


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


class DriverSerializer(serializers.ModelSerializer):
    """Admin CRUD for the basic profile — verification statuses are managed
    exclusively through the KYC endpoints, not this serializer."""

    class Meta:
        model = Driver
        fields = [
            "id",
            "full_name",
            "phone_number",
            "emergency_contact_name",
            "emergency_contact_phone",
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

    class Meta:
        model = Driver
        fields = [
            "id",
            "full_name",
            "phone_number",
            "aadhar_status",
            "dl_status",
            "police_status",
            "account_status",
            "is_eligible_for_assignment",
            "current_vehicle_id",
            "created_at",
            "updated_at",
        ]


class DriverMeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Driver
        fields = [
            "id",
            "full_name",
            "phone_number",
            "aadhar_status",
            "dl_status",
            "police_status",
            "dl_expiry_date",
            "current_vehicle_id",
        ]


class DriverKycSerializer(serializers.ModelSerializer):
    class Meta:
        model = Driver
        fields = [
            "id",
            "aadhar_doc_url",
            "aadhar_status",
            "aadhar_verified_by",
            "aadhar_verified_at",
            "aadhar_rejection_note",
            "dl_doc_url",
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
