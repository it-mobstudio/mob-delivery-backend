"""The company admin's view of drivers: roster, KYC review, wallet ledger."""

from core.choices import DriverAccountStatus, VerificationStatus
from core.openapi.dsl import ADMIN, doc, document, ex, ok, path_param, query_param, raw_ex
from drivers.serializers import (
    DriverKycDecisionSerializer,
    DriverKycDlSerializer,
    DriverKycSerializer,
    DriverListSerializer,
    DriverSerializer,
    WalletAdminSerializer,
    WalletEntryCreateSerializer,
    WalletTransactionSerializer,
)
from drivers.views import (
    DriverKycAadharView,
    DriverKycDlView,
    DriverKycPoliceView,
    DriverKycView,
    DriverViewSet,
    DriverWalletAdminView,
    DriverWalletEntryView,
)

DRIVER_ID = path_param("id", "The driver's id (UUID).")
PAGING = [
    query_param("page", "Page number, starting at 1.", type=int),
    query_param("page_size", "Rows per page (default 20, maximum 100).", type=int),
]
VERIFICATION = [c for c, _ in VerificationStatus.choices]

ADMIN_ONLY = """
**Admin token only.** These endpoints manage people, so an API-client token is refused with `PERMISSION_DENIED`. Sign in as an
admin with `POST /auth/login`.
"""

document(
    DriverViewSet,
    list=doc(
        id="listDrivers",
        tag="Drivers",
        summary="List drivers",
        description="""
Your company's drivers, newest first, 20 per page. Includes drivers who signed up from the app and are still
being onboarded - check `onboarding_status`. `is_eligible_for_assignment` is `true` only for drivers who can actually
receive trips (all three documents verified, licence unexpired, account active).

`search` matches part of the name or phone number.
""",
        auth=ADMIN,
        params=[
            query_param("account_status", "Only drivers with this account status.", enum=[c for c, _ in DriverAccountStatus.choices]),
            query_param("aadhar_status", "Only drivers whose Aadhaar is in this state.", enum=VERIFICATION),
            query_param("dl_status", "Only drivers whose driving licence is in this state.", enum=VERIFICATION),
            query_param("police_status", "Only drivers whose police verification is in this state.", enum=VERIFICATION),
            query_param("search", "Part of a name or phone number."),
            *PAGING,
        ],
        responses={200: ok(DriverListSerializer, ex("driver_admin.list", "A driver in the list", item=0))},
        notes=ADMIN_ONLY,
    ),
    create=doc(
        id="createDriver",
        tag="Drivers",
        summary="Create a driver",
        description="""
Adds a driver to your company. **Four fields are required**: `full_name`, `phone_number`, and an emergency contact - `emergency_contact_name` and `emergency_contact_phone`.

The driver then signs in **from the app** with that phone number (SMS OTP) - see *Driver app walkthrough*. A new driver starts with
all documents `pending` and **cannot receive trips** until Aadhaar, licence and police verification have been verified with the
KYC endpoints below.

The phone number must be unique in your company.
""",
        auth=ADMIN,
        request=DriverSerializer,
        request_examples=[
            raw_ex("A new driver", {"full_name": "Meena Rao", "phone_number": "+919777700001", "emergency_contact_name": "Ravi Rao", "emergency_contact_phone": "+919777700002"}, request=True),
        ],
        responses={201: ok(DriverSerializer, ex("driver_admin.create", "Created"))},
        notes=ADMIN_ONLY,
    ),
    retrieve=doc(
        id="getDriver",
        tag="Drivers",
        summary="Get a driver",
        description="One driver with their verification statuses, licence details and current vehicle.",
        auth=ADMIN,
        params=[DRIVER_ID],
        by_id=True,
        responses={200: ok(DriverSerializer, ex("driver_admin.create", "A driver", statuses=[200]))},
        notes=ADMIN_ONLY,
    ),
    update=doc(
        id="replaceDriver",
        tag="Drivers",
        summary="Replace a driver's details",
        description="Full update: send all four editable fields (`full_name`, `phone_number`, `emergency_contact_name`, `emergency_contact_phone`). Everything else (documents, statuses, vehicle) is managed elsewhere. To change one field use `PATCH`.",
        auth=ADMIN,
        params=[DRIVER_ID],
        by_id=True,
        request=DriverSerializer,
        request_examples=[raw_ex("Full replacement", {"full_name": "Meena Rao", "phone_number": "+919777700001", "emergency_contact_name": "Ravi Rao", "emergency_contact_phone": "+919777700002"}, request=True)],
        responses={200: ok(DriverSerializer, ex("driver_admin.create", "Updated", statuses=[200]))},
        notes=ADMIN_ONLY,
    ),
    partial_update=doc(
        id="updateDriver",
        tag="Drivers",
        summary="Update a driver's details",
        description="""
Changes only the fields you send: `full_name`, `phone_number`, `emergency_contact_name`, `emergency_contact_phone`.

Email, date of birth and city are the driver's own to edit from the app. Verification statuses change only through the KYC endpoints.
""",
        auth=ADMIN,
        params=[DRIVER_ID],
        by_id=True,
        request=DriverSerializer,
        request_examples=[raw_ex("Correct a phone number", {"phone_number": "+919777700009"}, request=True)],
        responses={200: ok(DriverSerializer, ex("driver_admin.create", "Updated", statuses=[200]))},
        notes=ADMIN_ONLY,
    ),
    destroy=doc(
        id="deleteDriver",
        tag="Drivers",
        summary="Remove a driver",
        description="""
Retires the driver - exactly like `POST /drivers/{id}/disable`. The driver is taken off duty, can no longer sign in, and drops out of
the roster, but their trips and wallet history are **kept** (nothing is destroyed). Refused with `DRIVER_HAS_ACTIVE_TRIP` while
the driver is on a trip.
""",
        auth=ADMIN,
        params=[DRIVER_ID],
        by_id=True,
        responses={204: ok(None, description="Removed. No body.")},
        errors=["DRIVER_HAS_ACTIVE_TRIP"],
        notes=ADMIN_ONLY,
    ),
    disable=doc(
        id="disableDriver",
        tag="Drivers",
        summary="Disable a driver",
        description="""
Sets the driver's `account_status` to `disabled`, takes them off duty and removes them from the roster. Their history is kept.
Refused with `DRIVER_HAS_ACTIVE_TRIP` while they are on a trip.
""",
        auth=ADMIN,
        params=[DRIVER_ID],
        by_id=True,
        request=None,
        responses={200: ok(DriverSerializer, ex("driver_admin.create", "Disabled", statuses=[200]))},
        errors=["DRIVER_HAS_ACTIVE_TRIP"],
        notes=ADMIN_ONLY,
    ),
)

# -- KYC review ---------------------------------------------------------------------------------
KYC_INTRO = """
**How KYC works.** A driver needs three things verified before they can receive trips: **Aadhaar**, **driving licence** and
**police verification**. The driver uploads them from the app (see *Driver onboarding & wallet*); each starts `pending`. You look at
the scan URLs here and decide - `verified` or `rejected` (a rejection needs a `note`, which the driver sees so they can fix
it and re-submit). A verified document is locked: the driver can't replace it.
"""

document(
    DriverKycView,
    get=doc(
        id="getDriverKyc",
        tag="Drivers",
        summary="Get a driver's KYC documents",
        description="""
Everything the driver has submitted and where each document stands: scan URLs (open them to review), Aadhaar's **last four digits
only** (the full number is never stored), licence number/expiry/allowed categories, who decided and when, and any rejection note.
""",
        auth=ADMIN,
        params=[DRIVER_ID],
        by_id=True,
        responses={200: ok(DriverKycSerializer, ex("driver_admin.kyc.get", "A driver who has submitted nothing yet"))},
        notes=ADMIN_ONLY + KYC_INTRO,
    ),
)

DECISIONS = [
    raw_ex("Approve", {"status": "verified"}, request=True),
    raw_ex("Reject with a reason", {"status": "rejected", "note": "The photo is blurry - please upload a clearer scan"}, request=True),
]

document(
    DriverKycAadharView,
    patch=doc(
        id="reviewDriverAadhar",
        tag="Drivers",
        summary="Verify or reject a driver's Aadhaar",
        description="Records your decision on the driver's Aadhaar scans. `note` is required when `status` is `rejected` and is shown to the driver.",
        auth=ADMIN,
        params=[DRIVER_ID],
        by_id=True,
        request=DriverKycDecisionSerializer,
        request_examples=DECISIONS,
        responses={200: ok(DriverKycSerializer, ex("driver_admin.kyc.verify_aadhar", "Aadhaar verified"))},
        notes=ADMIN_ONLY + KYC_INTRO,
    ),
)

document(
    DriverKycPoliceView,
    patch=doc(
        id="reviewDriverPolice",
        tag="Drivers",
        summary="Verify or reject a driver's police verification",
        description="Records your decision on the police-verification certificate. `note` is required when `status` is `rejected`.",
        auth=ADMIN,
        params=[DRIVER_ID],
        by_id=True,
        request=DriverKycDecisionSerializer,
        request_examples=DECISIONS,
        responses={200: ok(DriverKycSerializer, ex("driver_admin.kyc.reject", "Rejected with a note", statuses=[200]))},
        notes=ADMIN_ONLY + KYC_INTRO,
    ),
)

document(
    DriverKycDlView,
    patch=doc(
        id="reviewDriverLicence",
        tag="Drivers",
        summary="Verify or reject a driver's driving licence",
        description="""
Records your decision on the driving licence. **Verifying needs more than a yes:** you must also read the licence and enter its
`expiry_date` (in the future) and the vehicle categories it covers in `allowed_categories` - that is what decides which vehicles the
driver may take on duty. Rejecting needs a `note`.

When a verified licence's expiry date passes, the driver's account is locked automatically (`ACCOUNT_LOCKED`) until a new licence is verified.
""",
        auth=ADMIN,
        params=[DRIVER_ID],
        by_id=True,
        request=DriverKycDlSerializer,
        request_examples=[
            raw_ex("Approve", {"status": "verified", "expiry_date": "2035-03-31", "allowed_categories": ["two_wheeler", "three_wheeler"]}, request=True),
            raw_ex("Reject with a reason", {"status": "rejected", "note": "Licence is expired"}, request=True),
        ],
        responses={200: ok(DriverKycSerializer, ex("driver_admin.kyc.dl", "Licence verified for two-wheelers", statuses=[200]))},
        notes=ADMIN_ONLY + KYC_INTRO,
    ),
)

# -- wallet (company side) ------------------------------------------------------------------------------
WALLET_INTRO = """
**How the wallet works.** Every completed trip credits the driver a share of the fare (`DRIVER_EARNING_PERCENT`, 80% by default) automatically.
The company pays drivers **outside this system** (bank/UPI) and then records it here as a `payout`, which lowers the balance. You can
also record a `bonus` (adds), a `penalty` (subtracts) or an `adjustment` (a correction, either sign). The ledger is append-only: a
mistake is fixed with another entry, never by editing history.
"""

document(
    DriverWalletAdminView,
    get=doc(
        id="getDriverWallet",
        tag="Driver wallets",
        summary="Get a driver's balance and recent statement",
        description="""
The driver's **balance** (what you currently owe them), earnings for today / this week / this month / lifetime, the last seven days,
and their 20 most recent ledger entries. Read this before recording a payout so you pay the right amount.
""",
        auth=ADMIN,
        params=[DRIVER_ID],
        by_id=True,
        responses={200: ok(WalletAdminSerializer, ex("wallet_admin.get", "A driver with a balance"))},
        notes=ADMIN_ONLY + WALLET_INTRO,
    ),
)

document(
    DriverWalletEntryView,
    post=doc(
        id="recordWalletEntry",
        tag="Driver wallets",
        summary="Record a payout, bonus, penalty or adjustment",
        description="""
Appends one entry to the driver's ledger and returns it with the new running `balance_after`.

Send `amount` as a **positive** number for `payout`, `bonus` and `penalty` - the server applies the sign (a payout and a penalty
subtract, a bonus adds). Only `adjustment` takes the sign you send (positive adds, negative subtracts). No amount may be zero.

A payout larger than the driver's current balance is refused with `INSUFFICIENT_BALANCE`. Use `reference` for your bank/UTR
reference so the driver and you can match it later.
""",
        auth=ADMIN,
        params=[DRIVER_ID],
        by_id=True,
        request=WalletEntryCreateSerializer,
        request_examples=[
            raw_ex("A weekly payout", {"kind": "payout", "amount": "50.00", "description": "Weekly payout", "reference": "UTR-4820193"}, request=True),
            raw_ex("A bonus", {"kind": "bonus", "amount": "100.00", "description": "Festival bonus"}, request=True),
            raw_ex("A correction (negative)", {"kind": "adjustment", "amount": "-25.00", "description": "Duplicate credit reversed"}, request=True),
        ],
        responses={201: ok(WalletTransactionSerializer, ex("wallet_admin.payout", "A payout of 50.00"))},
        errors=["INSUFFICIENT_BALANCE", "INVALID_AMOUNT", "INVALID_KIND"],
        notes=ADMIN_ONLY + WALLET_INTRO,
    ),
)
