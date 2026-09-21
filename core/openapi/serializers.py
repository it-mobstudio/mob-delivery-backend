"""Shapes that exist only to describe the API (nothing here parses a request)."""

from rest_framework import serializers


class ErrorBodySerializer(serializers.Serializer):
    code = serializers.CharField(
        help_text="A stable, machine-readable code (`TRIP_NOT_CANCELLABLE`). Branch on this, never on `message`. All codes are listed in the **Errors** guide."
    )
    message = serializers.CharField(
        help_text="A sentence for people. Safe to show to a user, but it may be reworded - don't match on it."
    )
    details = serializers.DictField(
        required=False,
        child=serializers.ListField(child=serializers.CharField()),
        help_text="Only for `INVALID`: `{field_name: [problem, ...]}` - one entry per offending field. Nested objects appear under their own field name.",
    )


class ErrorEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(help_text="Always `false` for an error.")
    error = ErrorBodySerializer()

    class Meta:
        ref_name = "Error"


class RazorpayEventSerializer(serializers.Serializer):
    """The parts of Razorpay's webhook body this API reads. Razorpay sends more
    (`account_id`, `created_at`, ...); it is ignored."""

    event = serializers.CharField(
        help_text="The event name. Only `qr_code.credited` (a QR code received a payment) does anything; every other event is acknowledged and ignored."
    )
    payload = serializers.DictField(
        help_text="Razorpay's event payload. This API reads `payload.qr_code.entity.id` (which trip's code was paid) and `payload.payment.entity.id` / `.amount` (the payment, in **paise**)."
    )


class WebhookResultSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=["paid", "already_paid", "underpaid", "trip_not_in_progress", "unknown_qr", "ignored"],
        help_text=(
            "What the server did with the event - for your logs; every value is a `200` so Razorpay does not retry.\n\n"
            "- `paid` - the trip is now paid and the customer was texted their delivery OTP.\n"
            "- `already_paid` - the trip had already been marked paid (a repeat delivery); nothing changed.\n"
            "- `underpaid` - the payment was less than the fare; the trip stays unpaid.\n"
            "- `trip_not_in_progress` - money arrived for a trip that is no longer on the road (e.g. cancelled); nothing was marked paid and it **needs a manual refund**.\n"
            "- `unknown_qr` - no trip has this QR code.\n"
            "- `ignored` - an event type this API doesn't act on."
        ),
    )
