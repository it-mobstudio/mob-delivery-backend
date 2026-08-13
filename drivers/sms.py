import logging

logger = logging.getLogger(__name__)


class SmsProvider:
    """Pluggable interface for sending a driver's OTP by SMS. Swap in a real
    gateway (Twilio, MSG91, ...) later by adding one concrete subclass and
    pointing get_sms_provider() at it — the OTP flow itself doesn't change.
    """

    def send_otp(self, phone_number, otp):
        raise NotImplementedError


class LogSmsProvider(SmsProvider):
    """Stand-in used until a real SMS gateway is chosen — just logs the OTP
    server-side instead of sending it."""

    def send_otp(self, phone_number, otp):
        logger.info("SMS provider stand-in: would send OTP %s to %s", otp, phone_number)


def get_sms_provider():
    return LogSmsProvider()
