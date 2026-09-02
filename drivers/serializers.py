import re
from datetime import date

from rest_framework import serializers

from vehicles.models import VehicleCategory

from . import services
from .models import Driver, VerificationStatus

PHONE_NUMBER_RE = re.compile(r"^\+?[0-9]{10,15}$")
OTP_RE = re.compile(r"^\d{6}$")

# Direct-upload file input -> the model URL field it resolves to once
# uploaded to blob storage. validate() still sees the raw file (mutual-
# exclusivity with the URL field is checked there); DriverViewSet's
# perform_create/perform_update upload it and swap validated_data's file
# key for the URL key *between* is_valid() and save(), so create()/update()
# below only ever see a URL, uploaded or not.
DOC_FILE_TO_URL_FIELDS = {
    "aadhar_doc": "aadhar_doc_url",
    "dl_doc": "dl_doc_url",
    "police_doc": "police_doc_url",
}


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


class DriverCreateSerializer(DriverSerializer):
    """POST /drivers only — lets an admin who already has all three documents
    on hand at onboarding time attach them in the same call, instead of a
    mandatory separate step. Each document can be provided either as an
    already-hosted URL (aadharDocUrl/dlDocUrl/policeDocUrl — e.g. from a
    prior POST /uploads call) or as a direct multipart file
    (aadharDoc/dlDoc/policeDoc), uploaded to blob storage by this endpoint
    itself — provide at most one of the two per document (see
    DriverViewSet.perform_create). Providing a document, either way, does
    not verify it: every *_status still defaults to pending, exactly as if
    the document were attached later via the KYC endpoints. dl_expiry_date
    is the only field from the read-only base set that becomes writable
    here — the others (statuses, dl_allowed_categories, account_status,
    current_vehicle_id) still only ever change through the KYC/assignment
    flows, never at creation.
    """

    aadhar_doc = serializers.FileField(required=False, write_only=True)
    dl_doc = serializers.FileField(required=False, write_only=True)
    police_doc = serializers.FileField(required=False, write_only=True)

    class Meta(DriverSerializer.Meta):
        fields = DriverSerializer.Meta.fields + [
            "aadhar_doc_url", "aadhar_doc", "dl_doc_url", "dl_doc", "police_doc_url", "police_doc",
        ]
        read_only_fields = [f for f in DriverSerializer.Meta.read_only_fields if f != "dl_expiry_date"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        for file_field, url_field in DOC_FILE_TO_URL_FIELDS.items():
            if attrs.get(file_field) and attrs.get(url_field):
                raise serializers.ValidationError(
                    {file_field: f"Provide either {file_field} or {url_field}, not both."}
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
    """GET /driver/me — the driver's own profile, verification statuses, and
    (when a document was rejected) the reviewing admin's rejection note, so
    the driver's app can show *why* and prompt a resubmission. Includes the
    document URLs themselves so the driver can review what's on file.
    """

    class Meta:
        model = Driver
        fields = [
            "id",
            "full_name",
            "phone_number",
            "account_status",
            "aadhar_status",
            "aadhar_doc_url",
            "aadhar_rejection_note",
            "dl_status",
            "dl_doc_url",
            "dl_rejection_note",
            "dl_expiry_date",
            "police_status",
            "police_doc_url",
            "police_rejection_note",
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


class DriverUpdateSerializer(DriverSerializer):
    """PUT/PATCH /drivers/{id} — the general profile fields, plus any subset
    of the three KYC documents' doc/docUrl/status/note and the DL's
    expiryDate/allowedCategories, all in the same call. True partial-update
    semantics throughout: only whichever fields are actually sent get
    touched, everything else on the driver record is left exactly as it
    was — this is what used to be three separate PATCH .../kyc/{docType}
    endpoints, folded into the one endpoint that already updates a driver.

    Per document type, the same rules the old dedicated endpoints had still
    apply: doc_url and doc (a direct multipart upload) are mutually
    exclusive; providing either with no status resets that doc to pending
    review (first submission or resubmission after a rejection); a note is
    required when rejecting; and verifying the DL needs an expiry date and
    allowed categories to check against — either freshly provided here, or
    already on file from an earlier call, since re-confirming an
    already-verified DL's categories on every single call would defeat the
    point of a partial update.
    """

    aadhar_doc_url = serializers.URLField(required=False)
    aadhar_doc = serializers.FileField(required=False, write_only=True)
    aadhar_status = serializers.ChoiceField(choices=[VerificationStatus.VERIFIED, VerificationStatus.REJECTED], required=False)
    aadhar_note = serializers.CharField(required=False, allow_blank=True, write_only=True)

    dl_doc_url = serializers.URLField(required=False)
    dl_doc = serializers.FileField(required=False, write_only=True)
    dl_status = serializers.ChoiceField(choices=[VerificationStatus.VERIFIED, VerificationStatus.REJECTED], required=False)
    dl_note = serializers.CharField(required=False, allow_blank=True, write_only=True)
    dl_expiry_date = serializers.DateField(required=False)
    dl_allowed_categories = serializers.ListField(
        child=serializers.ChoiceField(choices=VehicleCategory.choices), required=False
    )

    police_doc_url = serializers.URLField(required=False)
    police_doc = serializers.FileField(required=False, write_only=True)
    police_status = serializers.ChoiceField(choices=[VerificationStatus.VERIFIED, VerificationStatus.REJECTED], required=False)
    police_note = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta(DriverSerializer.Meta):
        fields = DriverSerializer.Meta.fields + [
            "aadhar_doc_url", "aadhar_doc", "aadhar_note",
            "dl_doc_url", "dl_doc", "dl_note",
            "police_doc_url", "police_doc", "police_note",
        ]
        read_only_fields = [f for f in DriverSerializer.Meta.read_only_fields if f != "dl_expiry_date"]

    # doc-type prefix -> the field names it groups together, all four ways
    # (create/update, aadhar/dl/police) share this shape.
    DOC_TYPES = {
        "aadhar": {"doc_url": "aadhar_doc_url", "doc": "aadhar_doc", "status": "aadhar_status", "note": "aadhar_note"},
        "dl": {"doc_url": "dl_doc_url", "doc": "dl_doc", "status": "dl_status", "note": "dl_note"},
        "police": {"doc_url": "police_doc_url", "doc": "police_doc", "status": "police_status", "note": "police_note"},
    }

    def validate(self, attrs):
        attrs = super().validate(attrs)

        for fields in self.DOC_TYPES.values():
            doc_url, doc = attrs.get(fields["doc_url"]), attrs.get(fields["doc"])
            status, note = attrs.get(fields["status"]), attrs.get(fields["note"])

            if doc_url and doc:
                raise serializers.ValidationError(
                    {fields["doc"]: f"Provide either {fields['doc_url']} or {fields['doc']}, not both."}
                )
            if note and not doc_url and not doc and not status:
                raise serializers.ValidationError(
                    {fields["note"]: "Only meaningful alongside a status decision or a document."}
                )
            if status == VerificationStatus.REJECTED and not note:
                raise serializers.ValidationError({fields["note"]: "Required when status is 'rejected'."})

        if attrs.get("dl_status") == VerificationStatus.VERIFIED:
            effective_expiry = attrs.get("dl_expiry_date") or (self.instance.dl_expiry_date if self.instance else None)
            effective_categories = attrs.get("dl_allowed_categories") or (
                self.instance.dl_allowed_categories if self.instance else None
            )
            if not effective_expiry:
                raise serializers.ValidationError(
                    {"dl_expiry_date": "Required (in this request, or already on file) when dl_status is 'verified'."}
                )
            if effective_expiry < date.today():
                raise serializers.ValidationError({"dl_expiry_date": "Must be in the future."})
            if not effective_categories:
                raise serializers.ValidationError(
                    {"dl_allowed_categories": "Required (in this request, or already on file) when dl_status is 'verified'."}
                )
        return attrs

    def update(self, instance, validated_data):
        admin_id = self.context["request"].user.id

        for doc_type, fields in self.DOC_TYPES.items():
            doc_url = validated_data.pop(fields["doc_url"], None)
            status = validated_data.pop(fields["status"], None)
            note = validated_data.pop(fields["note"], None)
            validated_data.pop(fields["doc"], None)  # resolved to doc_url in perform_update; nothing left to do here

            if doc_url is None and status is None:
                continue  # this doc type wasn't touched in this request

            kwargs = {"admin_id": admin_id, "status": status, "note": note, "doc_url": doc_url}
            if doc_type == "dl":
                kwargs["expiry_date"] = validated_data.pop("dl_expiry_date", None)
                kwargs["allowed_categories"] = validated_data.pop("dl_allowed_categories", None)
            getattr(services, f"verify_{doc_type}")(instance, **kwargs)

        # Whatever's left — full_name, phone_number, emergency contacts, or
        # a bare dl_expiry_date/dl_allowed_categories change with no status
        # in this request — the normal model update.
        return super().update(instance, validated_data)
