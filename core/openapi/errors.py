"""Every error code the API can return, in one place.

The catalogue feeds three things, so they can never disagree with each other:
the error responses shown on each endpoint, the table in the "Errors" guide,
and a test that fails when a `DomainError("SOME_CODE", ...)` appears in the
code without being documented here (see core/test_openapi.py).

`code` is what a client should branch on: it is stable. `message` is for
people and may be reworded. Field-level validation problems arrive as
`INVALID` with a `details` object naming each field.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    status: int
    group: str
    when: str  # what caused it
    fix: str  # what the caller should do about it
    message: str  # a real example of the `message` text


def _e(code, status, group, when, fix, message):
    return ErrorInfo(code, status, group, when, fix, message)


G_GENERAL = "General"
G_AUTH = "Authentication & access"
G_TRIP = "Trips"
G_PAY = "Payments"
G_ITEMS = "Items & invoices"
G_DUTY = "Driver duty & vehicles"
G_ONBOARD = "Driver onboarding"
G_WALLET = "Wallet & payouts"
G_FLEET = "Fleet"
G_UPLOAD = "Uploads & webhooks"

ERRORS = [
    # -- General (produced by the framework for any endpoint) -----------------
    _e("INVALID", 400, G_GENERAL,
       "The request body, query string or form is missing a required field or has a value that isn't acceptable. `details` names every offending field.",
       "Read `error.details` (`{field: [problems]}`), fix each field and resend. Nothing was changed.",
       "Request could not be processed."),
    _e("PARSE_ERROR", 400, G_GENERAL,
       "The body could not be parsed - usually malformed JSON, or a `Content-Type` that doesn't match the body.",
       "Send valid JSON with `Content-Type: application/json` (or `multipart/form-data` on the upload endpoints).",
       "JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"),
    _e("UNSUPPORTED_MEDIA_TYPE", 415, G_GENERAL,
       "The endpoint doesn't accept the `Content-Type` you sent (e.g. JSON sent to a file-upload endpoint).",
       "Use the content type shown on the endpoint's request body (`multipart/form-data` for uploads).",
       'Unsupported media type "application/json" in request.'),
    _e("NOT_FOUND", 404, G_GENERAL,
       "No such resource - or it belongs to another company. The API never confirms that other companies' data exists.",
       "Check the id. If it came from this API it may have been deleted, or the token belongs to a different company.",
       "No Trip matches the given query."),
    _e("METHOD_NOT_ALLOWED", 405, G_GENERAL,
       "The path exists but not for that HTTP method.",
       "Use the method shown in this documentation.",
       'Method "PUT" not allowed.'),
    # -- Authentication & access ------------------------------------------------
    _e("NOT_AUTHENTICATED", 401, G_AUTH,
       "No `Authorization` header was sent.",
       "Send `Authorization: Bearer <access token>`. See the Authentication guide.",
       "Authentication credentials were not provided."),
    _e("TOKEN_NOT_VALID", 401, G_AUTH,
       "The bearer token is expired, malformed, or was signed by another environment.",
       "Get a new token (API clients: `POST /auth/client-token`; admins and drivers: use the refresh endpoint).",
       "Given token not valid for any token type"),
    _e("AUTHENTICATION_FAILED", 401, G_AUTH,
       "Wrong credentials (client id/secret, email/password), or the account behind a valid token is inactive.",
       "Check the credentials. For an API client, confirm it is still active in the admin panel.",
       "Invalid client credentials."),
    _e("PERMISSION_DENIED", 403, G_AUTH,
       "You are signed in, but this kind of token may not use this endpoint (e.g. a driver token on a company endpoint, or the reverse).",
       "Use the right principal for the endpoint - each operation lists who may call it.",
       "You do not have permission to perform this action."),
    _e("NOT_YOUR_TRIP", 403, G_AUTH,
       "A driver called a trip endpoint for a trip that isn't assigned to them.",
       "Only act on trips returned by `GET /driver/trips/active` or `GET /driver/trips`.",
       "This trip is not assigned to you."),
    _e("INVALID_REFRESH_TOKEN", 401, G_AUTH,
       "The driver's refresh token has expired or was already used up.",
       "Send the driver through the OTP login again.",
       "Your session has expired. Please sign in again."),
    _e("INVALID_OTP", 400, G_AUTH,
       "The login OTP is wrong or has expired (OTPs last 5 minutes).",
       "Ask for a new OTP with `POST /driver/auth/otp/request`.",
       "The OTP is invalid or has expired."),
    _e("DRIVER_NOT_FOUND", 404, G_AUTH,
       "Sign-up is closed on this server and no driver is registered with this phone number. (When sign-up is open, an unknown number simply becomes a new driver.)",
       "Ask the company to register the driver (`POST /drivers`), or check the number (`+919000000001`).",
       "No driver found with this phone number. Ask your company to register you."),
    _e("DRIVER_PHONE_AMBIGUOUS", 409, G_AUTH,
       "The same phone number belongs to drivers in more than one company, so the login can't tell which one to use.",
       "Contact support - phone numbers must be unique across companies.",
       "This phone number matches drivers in more than one company."),
    _e("ACCOUNT_DISABLED", 403, G_AUTH,
       "The company has disabled this driver.",
       "Nothing to retry - the company has to re-enable the driver.",
       "This driver's account has been disabled."),
    _e("ACCOUNT_LOCKED", 403, G_AUTH,
       "The driver's account is locked because their driving licence has expired.",
       "The driver has to submit a valid licence, then the company re-verifies it.",
       "This driver's account is locked because their driving licence has expired."),
    # -- Trips ------------------------------------------------------------------------
    _e("VEHICLE_TYPE_NOT_FOUND", 404, G_TRIP,
       "`vehicle_type_id` doesn't match an active vehicle type of your company.",
       "List your vehicle types with `GET /vehicle-types` and use one of those ids.",
       "This vehicle type is not available."),
    _e("FARE_NOT_CONFIGURED", 422, G_TRIP,
       "The vehicle type has no fare card, so a price can't be calculated.",
       "Configure the fare fields on the vehicle type (`base_fare`, `per_km_rate`, ...).",
       "No fare card is configured for vehicle type 'Bike'."),
    _e("ROUTING_UNAVAILABLE", 503, G_TRIP,
       "The routing engine couldn't route between the pickup and the drop (out of coverage, or the engine is down).",
       "Check the coordinates are inside the served area; otherwise retry shortly.",
       "Could not compute a route for this pickup/drop pair."),
    _e("TRIP_NOT_RETRYABLE", 409, G_TRIP,
       "You tried to (re)assign a trip that isn't in `no_driver_available`.",
       "Only trips with status `no_driver_available` can be assigned again.",
       "Only a trip with no driver available can be (re)assigned."),
    _e("TRIP_NOT_CANCELLABLE", 409, G_TRIP,
       "The trip is past the point where it can be cancelled (in progress, completed, or already cancelled).",
       "Trips can be cancelled up to `arrived_at_pickup`. Fetch the trip to see its current status.",
       "A trip in status 'cancelled' cannot be cancelled."),
    _e("INVALID_TRIP_STATUS_TRANSITION", 409, G_TRIP,
       "The driver action isn't valid for the trip's current status (e.g. `start` before `arrive`).",
       "Fetch the trip and follow the lifecycle: arrive -> start -> (pay) -> complete.",
       "Trip must be 'arrived_at_pickup' to do this (currently 'assigned')."),
    _e("TRIP_NOT_ACTIVE", 409, G_TRIP,
       "Navigation was requested for a trip that has nothing left to navigate to (completed or cancelled).",
       "Stop asking for navigation once the trip is finished.",
       "A trip in status 'completed' has nothing left to navigate."),
    _e("DRIVER_HAS_ACTIVE_TRIP", 409, G_TRIP,
       "The driver can't do this (go off duty, change vehicle, delete the account, be disabled) while a trip is active.",
       "Complete or cancel the active trip first.",
       "Finish or cancel your active trip before deleting your account."),
    _e("INVALID_DELIVERY_OTP", 400, G_TRIP,
       "The delivery OTP the driver entered is wrong or has expired (valid for 5 minutes).",
       "Re-enter it, or resend it with `POST /driver/trips/{id}/delivery-otp/resend`.",
       "The delivery OTP is invalid or has expired."),
    _e("OTP_ALREADY_REQUESTED", 429, G_TRIP,
       "A delivery OTP was sent for this trip less than 30 seconds ago.",
       "Wait for the cool-down (30 s) and try again.",
       "An OTP was sent for this trip recently. Please wait before requesting another."),
    # -- Payments ---------------------------------------------------------------------
    _e("NOT_COD_TRIP", 409, G_PAY,
       "A payment step was used on a prepaid trip. Prepaid trips take no payment at the drop.",
       "Skip payment for `payment_mode = prepaid` and complete the trip directly.",
       "This trip is not COD; there's nothing to collect."),
    _e("TRIP_NOT_IN_PROGRESS", 409, G_PAY,
       "The payment code is only available once the delivery has started (`in_progress`).",
       "Call `start` first.",
       "The payment code is shown at the drop, once the delivery has started."),
    _e("ALREADY_PAID", 409, G_PAY,
       "The trip's fare has already been paid (the customer scanned an earlier code, or it was collected already).",
       "Nothing to collect - go on to the delivery OTP and `complete`.",
       "This trip has already been paid."),
    _e("PAYMENT_NOT_RECEIVED", 409, G_PAY,
       "The driver asked the server to confirm the payment, and the payment provider has no payment for this trip's code yet (or it was for less than the fare).",
       "Not a fault: ask the customer to scan and pay, then check again in a moment.",
       "The customer's payment hasn't arrived yet. Ask them to scan the code and pay, then check again in a moment."),
    _e("PAYMENT_NOT_COLLECTED", 409, G_PAY,
       "`complete` was called on a COD trip before its payment was confirmed.",
       "Show the payment code and wait for the payment before completing.",
       "Collect the COD payment first; that's what sends the OTP."),
    _e("PAYMENT_PROVIDER_ERROR", 502, G_PAY,
       "The payment provider (Razorpay) refused a request or sent an answer that couldn't be read. The message says why.",
       "Retry once; if it persists, contact the platform operator with the message.",
       "The payment provider refused the request: The amount must be at least INR 1.00"),
    _e("PAYMENT_PROVIDER_UNAVAILABLE", 503, G_PAY,
       "The payment provider couldn't be reached or is failing.",
       "Retry in a moment. Nothing was charged and the trip is unchanged.",
       "The payment provider is having trouble. Please try again in a moment."),
    _e("PAYMENT_PROVIDER_NOT_CONFIGURED", 503, G_PAY,
       "This server has no (valid) payment-provider keys - or the keys are valid but Razorpay's QR Codes product isn't activated on the account (Razorpay answers \"The requested URL was not found\" for that).",
       "Operator action: set the Razorpay keys, and if they are set, ask Razorpay support to activate QR Codes on the account (see the Payments guide).",
       "Online payments aren't set up on this server yet."),
    # -- Items & invoices -----------------------------------------------------------------
    _e("ITEMS_NOT_VERIFIED", 409, G_ITEMS,
       "The order asked for its items to be verified (`verify_items: true`) and some are still unanswered. Payment and completion are blocked until every item is marked delivered / not delivered.",
       "Have the driver answer each item (`POST /driver/trips/{id}/items/{item_id}/verify`), then retry.",
       "Verify every item on this order before continuing."),
    _e("ITEM_NOT_FOUND", 404, G_ITEMS,
       "`item_id` isn't an item of this trip.",
       "Use the ids in the trip's `items` array.",
       "That item isn't on this order."),
    _e("VERIFICATION_NOT_REQUESTED", 409, G_ITEMS,
       "The driver tried to verify items on an order created without `verify_items`.",
       "Only orders created with `verify_items: true` have a verification step.",
       "This order doesn't need its items verified."),
    # -- Driver duty & vehicles ----------------------------------------------------------------
    _e("DRIVER_NOT_ELIGIBLE", 403, G_DUTY,
       "The driver can't go on duty: KYC isn't fully verified or the account isn't active.",
       "Finish onboarding and wait for the company's approval (see `onboarding_status` on `GET /driver/me`).",
       "This driver's KYC is incomplete or their account is not active."),
    _e("VEHICLE_CATEGORY_NOT_ALLOWED", 403, G_DUTY,
       "The driver's verified licence doesn't cover this vehicle's category.",
       "Choose a vehicle from `GET /driver/vehicles` (it lists only what the driver may take).",
       "Your driving licence is not verified for this type of vehicle."),
    _e("VEHICLE_IN_USE", 409, G_DUTY,
       "Another driver is on duty with this vehicle.",
       "Pick another vehicle.",
       "This vehicle is being used by another driver."),
    _e("VEHICLE_NOT_ACTIVE", 409, G_DUTY,
       "The vehicle has been disabled by the company.",
       "Pick another vehicle.",
       "This vehicle is not active."),
    _e("VEHICLE_LIMIT_REACHED", 409, G_DUTY,
       "The driver already has the maximum number of vehicles they can register (10).",
       "Remove one (`DELETE /driver/my-vehicles/{id}`) before adding another.",
       "You can register up to 10 vehicles. Remove one to add another."),
    _e("PHOTO_LIMIT_REACHED", 409, G_DUTY,
       "The vehicle already has the maximum number of pictures (6).",
       "Remove one (`DELETE /driver/my-vehicles/{id}/photos/{photo_id}`) before adding another.",
       "A vehicle can have up to 6 photos. Remove one to add another."),
    _e("VEHICLE_ON_DUTY", 409, G_DUTY,
       "The driver tried to remove the vehicle they are on duty with.",
       "Go off duty (`POST /driver/duty/end`) first.",
       "You're on duty with this vehicle. Go off duty before removing it."),
    # -- Driver onboarding ------------------------------------------------------------------------
    _e("PROFILE_LOCKED", 409, G_ONBOARD,
       "Name and date of birth can't change once the driver's ID has been verified.",
       "Ask the company's support to correct it.",
       "Your name and date of birth can't be changed after your ID is verified. Contact support."),
    _e("KYC_ALREADY_VERIFIED", 409, G_ONBOARD,
       "The driver tried to re-submit a document the company already verified.",
       "Nothing to do; contact support if it must change.",
       "Your driving licence is already verified. Contact support if it needs to change."),
    _e("INVALID_UPLOAD", 400, G_UPLOAD,
       "The file isn't an acceptable image/PDF (wrong type, too large, or corrupt).",
       "Send a JPEG/PNG/WebP (or a PDF where documents allow it) within the size limit.",
       "Unsupported file type '.pdf' for purpose 'vehicle_photo'. Allowed: ['jpeg', 'jpg', 'png', 'webp']."),
    # -- Wallet & payouts ---------------------------------------------------------------------------
    _e("INVALID_AMOUNT", 400, G_WALLET,
       "A wallet entry's amount is zero, or isn't positive for a payout, bonus or penalty (the server applies the sign for those kinds).",
       "Send a positive amount for `payout`, `bonus` and `penalty`. Only `adjustment` may be negative, and none may be zero.",
       "Enter a positive amount."),
    _e("INVALID_KIND", 400, G_WALLET,
       "A trip-earning entry was posted by hand. Earnings are credited automatically when a trip completes. (The request schema doesn't allow `trip_earning`, so an ordinary client gets `INVALID` first.)",
       "Post only `payout`, `bonus`, `penalty` or `adjustment`.",
       "Trip earnings are credited automatically."),
    _e("INSUFFICIENT_BALANCE", 409, G_WALLET,
       "The payout is more than what the company currently owes the driver.",
       "Read `GET /drivers/{id}/wallet` and pay out at most the `balance`.",
       "This payout is more than the driver's balance (₹18.00)."),
    _e("WALLET_BALANCE_PENDING", 409, G_WALLET,
       "The driver tried to delete their account while the company still owes them money.",
       "The company pays the balance out first, then the driver can delete the account.",
       "You still have ₹18.00 in your wallet. Ask your company to pay it out, then delete your account."),
    # -- Fleet -----------------------------------------------------------------------------------------------
    _e("VEHICLE_HAS_ACTIVE_TRIP", 409, G_FLEET,
       "The vehicle can't be disabled while a trip is using it.",
       "Wait until the trip is finished, then disable it.",
       "This vehicle has an active trip and cannot be disabled."),
    _e("VEHICLE_TYPE_IN_USE", 409, G_FLEET,
       "The vehicle type can't be deleted while vehicles still use it.",
       "Re-assign or remove those vehicles first (or mark the type inactive instead of deleting).",
       "This vehicle type is still assigned to one or more vehicles."),
    # -- Webhooks -----------------------------------------------------------------------------------------------
    _e("INVALID_SIGNATURE", 400, G_UPLOAD,
       "The webhook's `X-Razorpay-Signature` doesn't match the body - it wasn't sent by Razorpay (or the secret is wrong).",
       "Only Razorpay should call this endpoint. Check `RAZORPAY_WEBHOOK_SECRET` matches the secret in the Razorpay dashboard.",
       "The webhook signature doesn't match."),
    _e("INVALID_PAYLOAD", 400, G_UPLOAD,
       "The webhook body isn't a JSON object.",
       "Razorpay always sends JSON; this only happens with hand-made requests.",
       "The webhook body isn't a JSON object."),
    _e("WEBHOOK_NOT_CONFIGURED", 503, G_UPLOAD,
       "The server has no webhook secret, so it can't tell real Razorpay calls from fake ones and refuses all of them.",
       "Operator action: set `RAZORPAY_WEBHOOK_SECRET`.",
       "Payment webhooks aren't set up on this server."),
]

BY_CODE = {e.code: e for e in ERRORS}
GROUPS = list(dict.fromkeys(e.group for e in ERRORS))


def envelope(code, message=None, details=None):
    """The exact body the API sends for an error."""
    info = BY_CODE[code]
    error = {"code": code, "message": message or info.message}
    if details is not None:
        error["details"] = details
    return {"success": False, "error": error}


def catalog_markdown():
    """The Errors guide's table, generated (so it can't drift from ERRORS)."""
    out = []
    for group in GROUPS:
        out.append(f"### {group}\n")
        out.append("| Code | HTTP | When it happens | What to do |")
        out.append("|---|---|---|---|")
        for e in (x for x in ERRORS if x.group == group):
            out.append(f"| `{e.code}` | {e.status} | {e.when} | {e.fix} |")
        out.append("")
    return "\n".join(out)
