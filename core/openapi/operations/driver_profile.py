"""The signed-in driver's own profile, KYC submissions and account controls."""

from core.openapi.dsl import DRIVER, doc, document, ex, ok, raw_ex
from drivers.serializers import (
    DriverAadharSubmitSerializer,
    DriverDlSubmitSerializer,
    DriverMeSerializer,
    DriverPhotoSerializer,
    DriverPoliceSubmitSerializer,
    DriverProfileUpdateSerializer,
)
from drivers.views import (
    DriverAadharSubmitView,
    DriverDlSubmitView,
    DriverMeView,
    DriverPhotoView,
    DriverPoliceSubmitView,
)

TAG = "Driver profile & onboarding"
MULTIPART = "multipart/form-data"

document(
    DriverMeView,
    get=doc(
        id="driverGetProfile",
        tag=TAG,
        summary="Get my profile",
        description="""
The signed-in driver: details, verification state of each document (with the company's rejection note when one was refused), payout
details (masked), the vehicle they are on duty with and whether they are online.

**`onboarding_status`** is what the app should key its first screen on:
`profile_incomplete` / `documents_required` -> run onboarding; `under_review` -> "waiting for the company";
`action_required` -> something was rejected, show `kyc.*.rejection_note` and let them re-submit; `approved` -> go to the dashboard.
""",
        auth=DRIVER,
        responses={200: ok(DriverMeSerializer, ex("driver_me.approved", "An approved driver"), ex("driver_me.new", "A brand-new driver"))},
    ),
    patch=doc(
        id="driverUpdateProfile",
        tag=TAG,
        summary="Update my profile",
        description="""
Saves the driver's own details. **Every field is optional** - send only what changed (this is how each onboarding step saves).
The profile counts as *complete* once `full_name`, `date_of_birth`, `emergency_contact_name` and `emergency_contact_phone` are set.

Rules the server checks (each failure comes back under `error.details` with a sentence to show the driver):
the driver must be 18 to 80; the emergency contact can't be their own number; `pincode` is 6 digits; `payout_upi_id` looks like `name@bank`;
a bank account needs **all three** of `bank_account_holder`, `bank_account_number` (9-18 digits) and `bank_ifsc`.

Once the driver's Aadhaar is **verified**, `full_name` and `date_of_birth` are locked (`PROFILE_LOCKED`) - contact the company.
""",
        auth=DRIVER,
        request=DriverProfileUpdateSerializer,
        request_examples=[
            raw_ex("Onboarding: about me", {"full_name": "Ravi Kumar", "date_of_birth": "1994-03-12", "email": "ravi@example.com", "city": "Bengaluru", "pincode": "560001", "emergency_contact_name": "Sunita Kumar", "emergency_contact_phone": "+919555500002"}, request=True),
            raw_ex("Where to send my payouts (UPI)", {"payout_upi_id": "ravi@okhdfc"}, request=True),
            raw_ex("Where to send my payouts (bank)", {"bank_account_holder": "Ravi Kumar", "bank_account_number": "123456789012", "bank_ifsc": "HDFC0001234"}, request=True),
        ],
        responses={200: ok(DriverMeSerializer, ex("driver_me.patch", "Saved"))},
        errors=["PROFILE_LOCKED"],
    ),
    delete=doc(
        id="driverDeleteAccount",
        tag=TAG,
        summary="Delete my account",
        description="""
Closes the driver's own account (an app-store requirement for apps that let people sign themselves up). The account is disabled and
signed out everywhere; trips and wallet history are kept for the company's records.

Refused while it would strand something: an **active trip** (`DRIVER_HAS_ACTIVE_TRIP`) or **money the company still owes them**
(`WALLET_BALANCE_PENDING` - the message says how much).
""",
        auth=DRIVER,
        responses={204: ok(None, description="Account closed. No body.")},
        errors=["DRIVER_HAS_ACTIVE_TRIP", "WALLET_BALANCE_PENDING"],
    ),
)

UPLOAD_NOTE = """
Send as **`multipart/form-data`** (not JSON). Images only (`jpg`, `jpeg`, `png`, `webp`) unless noted, up to 10 MB each; the content is
checked, so a renamed text file is refused with `INVALID_UPLOAD`. The response is the refreshed profile, so the app updates in one round trip.
"""

document(
    DriverPhotoView,
    post=doc(
        id="driverUploadPhoto",
        tag=TAG,
        summary="Upload my selfie",
        description="Sets the driver's profile photo (a clear picture of their face).",
        auth=DRIVER,
        request={MULTIPART: DriverPhotoSerializer},
        responses={200: ok(DriverMeSerializer, ex("driver_me.photo", "Photo saved"))},
        errors=["INVALID_UPLOAD"],
        notes=UPLOAD_NOTE,
    ),
)

document(
    DriverAadharSubmitView,
    post=doc(
        id="driverSubmitAadhar",
        tag=TAG,
        summary="Submit my Aadhaar",
        description="""
Sends the Aadhaar number and scans of **both sides** for the company to verify. The full number is checked (12 digits) and then reduced to its last
four digits - only those are kept. Submitting again replaces the scans and puts it back in review (useful after a rejection); once the company has
**verified** it, it is locked (`KYC_ALREADY_VERIFIED`).
""",
        auth=DRIVER,
        request={MULTIPART: DriverAadharSubmitSerializer},
        responses={200: ok(DriverMeSerializer, ex("driver_me.aadhar", "Submitted, waiting for review"))},
        errors=["KYC_ALREADY_VERIFIED", "INVALID_UPLOAD"],
        notes=UPLOAD_NOTE,
    ),
)

document(
    DriverDlSubmitView,
    post=doc(
        id="driverSubmitLicence",
        tag=TAG,
        summary="Submit my driving licence",
        description="""
Sends the licence number, its expiry date (must be in the future) and a scan of the front (the back is optional). The company confirms the expiry
and which vehicle categories it covers when it verifies - that decides which vehicles the driver may take on duty. Locked once verified.
""",
        auth=DRIVER,
        request={MULTIPART: DriverDlSubmitSerializer},
        responses={200: ok(DriverMeSerializer, ex("driver_me.dl", "Submitted, waiting for review"))},
        errors=["KYC_ALREADY_VERIFIED", "INVALID_UPLOAD"],
        notes=UPLOAD_NOTE,
    ),
)

document(
    DriverPoliceSubmitView,
    post=doc(
        id="driverSubmitPolice",
        tag=TAG,
        summary="Submit my police verification",
        description="Sends the police-verification certificate (an image, or a PDF). Optional for the driver to upload - the company may verify it another way - but a driver can't take trips until it is verified.",
        auth=DRIVER,
        request={MULTIPART: DriverPoliceSubmitSerializer},
        responses={200: ok(DriverMeSerializer, ex("driver_me.police", "Submitted, waiting for review"))},
        errors=["KYC_ALREADY_VERIFIED", "INVALID_UPLOAD"],
        notes=UPLOAD_NOTE,
    ),
)
