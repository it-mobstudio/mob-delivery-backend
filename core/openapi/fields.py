"""What every field in a request or response means.

Serializers describe *shape* (type, required, limits); this file adds the
*meaning* a developer needs, in one place so it can be read and reviewed like
prose. Lookup order for a field: FIELDS["Schema"]["field"] (through ALIASES for
schemas that share a shape) -> COMMON["field"]. The hook in hooks.py applies
these to any property that has no description already, and core/test_openapi.py
fails if a property is left without one.
"""

# Fields that mean the same thing wherever they appear.
COMMON = {
    "id": "Unique id (UUID).",
    "created_at": "When the record was created (ISO 8601, UTC).",
    "updated_at": "When the record was last changed (ISO 8601, UTC).",
    "count": "Total number of items across all pages.",
    "next": "URL of the next page, or `null` on the last page.",
    "previous": "URL of the previous page, or `null` on the first page.",
    "results": "The items on this page.",
    "currency": "ISO 4217 currency code. Always `INR`.",
    "lat": "Latitude in decimal degrees (-90 to 90).",
    "lng": "Longitude in decimal degrees (-180 to 180).",
    "message": "A short human-readable confirmation.",
    "icon_image_url": "URL of the vehicle type's icon, or `null` if none was set.",
    "photo_url": "URL of the photo, or `null` if none was uploaded.",
    "file_url": "URL of the stored file. Upload one with `POST /uploads` and pass the URL it returns.",
    "phone_number": "Phone number in international format, e.g. `+919876543210`.",
    "full_name": "Full name.",
}

# Schemas that are the same thing seen through a different lens share docs.
ALIASES = {
    "TripList": "Trip",
    "DriverTrip": "Trip",
    "DriverTripList": "Trip",
    "TripCreate": "TripCreate",
    "TripDriverSummary": "Driver",
    "DriverList": "Driver",
    "VehicleList": "Vehicle",
    "VehicleDetail": "Vehicle",
    "VehicleTypeSummary": "VehicleType",
}

_POINT = "A pickup or drop location."

FIELDS = {
    # ------------------------------------------------------------------ trips
    "Point": {
        "address": "Street address as a person would write it. Shown to the driver. Up to 255 characters.",
        "lat": "Latitude of the stop, decimal degrees, up to 6 decimal places. Must be inside the served area or routing fails (`ROUTING_UNAVAILABLE`).",
        "lng": "Longitude of the stop, decimal degrees, up to 6 decimal places.",
        "contact_name": "Who the driver should ask for at this stop. Optional.",
        "contact_phone": "Phone number of that person (up to 20 characters, e.g. `+919888800002`). Optional for the pickup; **required for the drop of a cash-on-delivery trip**, because the delivery OTP is texted to it.",
    },
    "TripCreate": {
        "vehicle_type_id": "Which kind of vehicle to book: the `vehicle_type_id` of one of the options returned by `POST /trips/estimate` (or an `id` from `GET /vehicle-types`). Must be an active vehicle type of your company.",
        "pickup": "Where the driver collects the goods.",
        "drop": "Where the goods are delivered.",
        "payment_mode": "`prepaid` - already settled outside this system; the trip is marked paid immediately and the driver collects nothing. `cod` - cash on delivery: the driver collects the fare at the drop through a Razorpay QR code (see the Payments guide).",
        "reference_id": "Your own order number. Echoed back on the trip so you can match it to your records. Optional, up to 100 characters; it does not have to be unique.",
        "invoice_url": "Link to the invoice (PDF or image) for the goods - the driver's app offers it for download and sharing on WhatsApp. `http(s)` only. Host it yourself, or `POST /uploads` with `purpose=trip_invoice` and pass the URL it returns. Optional.",
        "invoice_number": "The invoice's number as printed on it, shown next to the download button. Optional, up to 100 characters.",
        "verify_items": "`true` makes the driver confirm every item at the drop (delivered / not delivered, optionally with a photo) **before** the trip can be paid or completed; each answer is kept as delivery history. Requires at least one entry in `items`. Default `false`.",
        "items": f"The goods being delivered, one entry per line (at most 100). Optional; required if `verify_items` is `true`.",
    },
    "TripItemInput": {
        "name": "What the item is, as the driver should see it (up to 200 characters).",
        "quantity": "How many. Whole number from 1 to 1,000,000. Default `1`.",
        "unit": "The unit of `quantity`, free text - `pcs`, `kg`, `box`, `bags`. Optional.",
        "sku": "Your product code. Optional, up to 100 characters.",
        "notes": "A handling note for the driver (\"Keep dry\", \"Fragile\"). Optional, up to 255 characters.",
        "image_url": "URL of a picture of the item so the driver can recognise it. `http(s)` only. Optional.",
        "unit_price": "Price per unit as a decimal (e.g. `380.00`), for the driver's information. Optional; it does not change the delivery fare.",
    },
    "TripItem": {
        "position": "The item's order in the list, starting at 0.",
        "name": "What the item is.",
        "quantity": "How many were ordered.",
        "unit": "The unit of `quantity`, or empty.",
        "sku": "Your product code, or empty.",
        "notes": "Handling note you supplied, or empty.",
        "image_url": "Picture of the item you supplied, or `null`.",
        "unit_price": "Price per unit you supplied, as a decimal string, or `null`.",
        "status": "The driver's answer: `pending` (not yet checked), `delivered`, or `not_delivered`. Only moves off `pending` on orders with `verify_items`.",
        "verified_at": "When the driver answered (ISO 8601, UTC), or `null` while `pending`.",
        "proof_image_url": "The photo the driver took when checking this item, or `null` if they took none. Kept as delivery proof.",
        "driver_note": "What the driver wrote - always present when `status` is `not_delivered` (what happened), otherwise usually empty.",
    },
    "Trip": {
        "id": "The trip's id (UUID). Use it in every `/trips/{id}` call.",
        "status": "Where the trip is in its lifecycle: `requested`, `no_driver_available`, `assigned`, `arrived_at_pickup`, `in_progress`, `completed` or `cancelled`. See the Trip lifecycle guide.",
        "reference_id": "Your own order number, exactly as you sent it (empty string if you sent none).",
        "vehicle_type": "The kind of vehicle booked.",
        "driver": "The assigned driver, or `null` until one is assigned (and after a `no_driver_available`).",
        "vehicle": "The vehicle the driver is on, or `null` until a driver is assigned.",
        "pickup_address": "Pickup address, as sent.",
        "pickup_lat": "Pickup latitude, decimal string with 6 decimals.",
        "pickup_lng": "Pickup longitude, decimal string with 6 decimals.",
        "pickup_contact_name": "Contact at the pickup, or empty.",
        "pickup_contact_phone": "Phone of that contact, or empty.",
        "drop_address": "Drop address, as sent.",
        "drop_lat": "Drop latitude, decimal string with 6 decimals.",
        "drop_lng": "Drop longitude, decimal string with 6 decimals.",
        "drop_contact_name": "Contact at the drop, or empty.",
        "drop_contact_phone": "Phone of that contact - the delivery OTP goes here on cash-on-delivery trips.",
        "distance_meters": "Route length in metres, calculated when the trip was booked.",
        "duration_seconds": "Estimated driving time in seconds, calculated when the trip was booked.",
        "route_polyline": "The route as an encoded polyline. Decode it at `polyline_precision` (6 decimal places - not Google's 5) to draw the route on a map.",
        "polyline_precision": "Decimal places `route_polyline` is encoded at. Always 6.",
        "base_fare": "Flat starting fare, decimal string (e.g. `30.00`).",
        "distance_fare": "Charge for the route's distance, decimal string.",
        "time_fare": "Charge for the estimated driving time, decimal string.",
        "surge_multiplier": "Demand multiplier applied to base + distance + time (currently always `1.00`).",
        "total_fare": "What the delivery costs: (base + distance + time) x surge, never below the vehicle type's minimum fare. Decimal string. Fixed at booking - it does not change later.",
        "payment_mode": "`prepaid` or `cod`, as sent.",
        "payment_status": "`pending` or `paid`. Prepaid trips are `paid` from the start; cash-on-delivery trips become `paid` once the customer's Razorpay payment is confirmed.",
        "cod_collected_at": "When the cash-on-delivery payment was confirmed (ISO 8601, UTC), or `null`.",
        "payment_reference": "Razorpay's id for the payment that settled a cash-on-delivery trip (`pay_...`) - for reconciling against your Razorpay dashboard. Empty until paid.",
        "invoice_url": "Absolute URL of the invoice you supplied, or `null`.",
        "invoice_number": "The invoice number you supplied, or empty.",
        "verify_items": "Whether the driver must confirm every item before payment/completion.",
        "items": "The goods, in the order you sent them, with the driver's per-item answers.",
        "cancellation_reason": "Why the trip was cancelled (empty unless `status` is `cancelled`).",
        "cancelled_by": "Who cancelled: `company`, `driver` or `system`; empty unless cancelled.",
        "assigned_at": "When a driver was assigned (ISO 8601, UTC), or `null`.",
        "arrived_at_pickup_at": "When the driver reported arriving at the pickup, or `null`.",
        "started_at": "When the driver picked up the goods and started the delivery, or `null`.",
        "completed_at": "When the delivery was completed, or `null`.",
        "cancelled_at": "When the trip was cancelled, or `null`.",
        "driver_earning": "What this trip pays the assigned driver (decimal string; the driver's share of `total_fare`), or `null` until the trip is completed. Only visible to the driver, never to the company API.",
    },
    "TripVehicleSummary": {"registration_number": "The vehicle's registration plate, e.g. `KA01AB1234`."},
    "VehicleType": {
        "id": "The vehicle type's id (UUID). Pass it as `vehicle_type_id` when booking.",
        "name": "Display name, e.g. `Bike` or `Tempo`. Required, unique within your company, up to 100 characters.",
        "category": "The kind of vehicle: `two_wheeler`, `three_wheeler` or `four_wheeler`. It decides which routing profile is used (a bike takes shortcuts a truck can't) and which driving licences may drive it.",
    },
}

FIELDS.update({
    # ------------------------------------------------------------------ fleet
    "VehicleType": {
        **FIELDS["VehicleType"],
        "default_capacity_kg": "Load capacity in kg given to a new vehicle of this type unless it overrides it. Required; must be above 0.",
        "icon_image_url": "URL of an icon for the type (upload one with `purpose: vehicle_type_icon`). Optional.",
        "status": "`active` - can be booked and appears in estimates. `inactive` - hidden and not bookable (existing trips are unaffected). Default `active`.",
        "base_fare": "Flat starting charge in INR (decimal, 0 or more). Default `0.00`.",
        "per_km_rate": "Charge per kilometre of route (decimal, 0 or more). Default `0.00`.",
        "per_min_rate": "Charge per minute of estimated driving time (decimal, 0 or more). Default `0.00`.",
        "min_fare": "The least a trip on this type can cost. **Must be above 0**: `0` means \"no fare card yet\" and trips can't be booked (`FARE_NOT_CONFIGURED`).",
    },
    "Vehicle": {
        "vehicle_type_id": "The vehicle's type - the `id` of one of your **active** vehicle types. Required.",
        "vehicle_type": "The vehicle's type (name, category, icon).",
        "registration_number": "Registration plate. Required. Upper-cased for you; letters, digits and hyphens only (no spaces); unique among your vehicles, up to 20 characters.",
        "capacity_kg": "Load capacity in kg (decimal, above 0). Optional - defaults to the vehicle type's `default_capacity_kg`.",
        "photo_url": "URL of a photo of the vehicle (upload with `purpose: vehicle_photo`). Optional.",
        "status": "`active`, `maintenance` or `disabled`. Set by the system - `POST /vehicles/{id}/disable` retires a vehicle.",
        "current_driver_id": "The driver last put on this vehicle by going on duty (a driver id), or `null`. Maintained by the system; read-only.",
        "documents": "The vehicle's documents, newest first.",
    },
    "VehicleDocument": {
        "document_type": "What the document is: `insurance`, `fitness`, `rc` (registration certificate), `purchase` or `other`. Required.",
        "file_url": "URL of the document's file - upload it with `POST /uploads` (`purpose: vehicle_document`) or link one you host. Required.",
        "expiry_date": "When the document expires (`YYYY-MM-DD`). **Required for `insurance` and `fitness`**, optional for the rest.",
    },
    # ------------------------------------------------------------------ drivers (admin)
    "Driver": {
        "id": "The driver's id (UUID).",
        "full_name": "The driver's full name. Required when you create a driver; up to 150 characters.",
        "phone_number": "The driver's mobile number in international format (`+919777700001`; 10-15 digits, `+` optional). Required. This is what they sign in with, so it must be unique in your company.",
        "emergency_contact_name": "Who to call if the driver needs help. **Required** when you create a driver (up to 150 characters).",
        "emergency_contact_phone": "That person's number (up to 20 characters). **Required** when you create a driver.",
        "email": "The driver's email, if they gave one in the app. Read-only.",
        "date_of_birth": "Date of birth (`YYYY-MM-DD`), as entered by the driver. Read-only here.",
        "city": "The driver's city, as entered by the driver. Read-only here.",
        "profile_photo_url": "URL of the driver's selfie, or `null`.",
        "onboarding_status": "Where the driver is in joining: `profile_incomplete`, `documents_required`, `under_review` (waiting for you), `action_required` (something was rejected) or `approved`. Derived from their profile and KYC; read-only.",
        "aadhar_status": "Aadhaar verification: `pending`, `verified` or `rejected`.",
        "dl_status": "Driving-licence verification: `pending`, `verified` or `rejected`.",
        "police_status": "Police-verification status: `pending`, `verified` or `rejected`.",
        "dl_expiry_date": "The verified licence's expiry date, or `null`.",
        "dl_allowed_categories": "Vehicle categories the verified licence covers (`two_wheeler`, `three_wheeler`, `four_wheeler`) - decides which vehicles the driver may take.",
        "account_status": "`active`, `locked_dl_expired` (licence lapsed) or `disabled`. Only `active` drivers can sign in and get trips.",
        "current_vehicle_id": "The vehicle the driver is on duty with, or `null` when off duty.",
        "is_eligible_for_assignment": "`true` when the driver can receive trips: Aadhaar, licence and police verified, licence not expired, account `active`.",
    },
    "DriverKyc": {
        "aadhar_number_last4": "Last four digits of the Aadhaar number. The full number is never stored.",
        "aadhar_doc_url": "URL of the Aadhaar front scan, or `null`.",
        "aadhar_back_doc_url": "URL of the Aadhaar back scan, or `null`.",
        "aadhar_status": "`pending`, `verified` or `rejected`.",
        "aadhar_verified_by": "Id of the admin who decided, or `null`.",
        "aadhar_verified_at": "When it was decided (ISO 8601, UTC), or `null`.",
        "aadhar_rejection_note": "Why it was rejected - shown to the driver - or `null`.",
        "dl_number": "The licence number the driver entered.",
        "dl_doc_url": "URL of the licence front scan, or `null`.",
        "dl_back_doc_url": "URL of the licence back scan, or `null`.",
        "dl_status": "`pending`, `verified` or `rejected`.",
        "dl_expiry_date": "Licence expiry (`YYYY-MM-DD`): what the driver entered, then what you confirmed.",
        "dl_allowed_categories": "Vehicle categories you confirmed the licence covers.",
        "dl_verified_by": "Id of the admin who decided, or `null`.",
        "dl_verified_at": "When it was decided, or `null`.",
        "dl_rejection_note": "Why it was rejected, or `null`.",
        "police_doc_url": "URL of the police-verification certificate, or `null`.",
        "police_status": "`pending`, `verified` or `rejected`.",
        "police_verified_by": "Id of the admin who decided, or `null`.",
        "police_verified_at": "When it was decided, or `null`.",
        "police_rejection_note": "Why it was rejected, or `null`.",
    },
    "DriverKycDecision": {
        "status": "Your decision: `verified` or `rejected`. Required.",
        "note": "Why - **required when `rejected`** (the driver sees it). Optional otherwise.",
    },
    "DriverKycDl": {
        "status": "Your decision: `verified` or `rejected`. Required.",
        "note": "Why - **required when `rejected`**.",
        "expiry_date": "The licence's expiry date (`YYYY-MM-DD`). **Required when `verified`**; must be in the future.",
        "allowed_categories": "The vehicle categories the licence covers, from `two_wheeler`, `three_wheeler`, `four_wheeler`. **Required when `verified`** (at least one).",
    },
    # ------------------------------------------------------------------ wallet
    "WalletEntryCreate": {
        "kind": "`payout` (money you sent the driver - subtracts), `bonus` (adds), `penalty` (subtracts) or `adjustment` (a correction; you choose the sign). Required.",
        "amount": "A decimal with up to 2 places. **Positive** for `payout`, `bonus` and `penalty` (the server applies the sign); for `adjustment`, positive adds and negative subtracts. Never zero. Required.",
        "description": "A note the driver sees in their statement (up to 255 characters). Optional.",
        "reference": "Your reference - a bank/UTR number, say (up to 100 characters). Optional.",
    },
    "WalletTransaction": {
        "kind": "`trip_earning`, `bonus`, `penalty`, `payout` or `adjustment`.",
        "kind_label": "The kind, as display text (\"Trip earning\").",
        "type": "`credit` if the entry added money, `debit` if it took money away.",
        "amount": "Signed decimal string: positive adds to the balance, negative subtracts.",
        "balance_after": "The driver's balance right after this entry.",
        "description": "The note recorded with the entry.",
        "reference": "The reference recorded with the entry (a UTR number, ...).",
        "trip_id": "For a `trip_earning`, the trip that paid it; otherwise `null`.",
    },
})
ALIASES.update({"DriverAvailableVehicle": "DriverVehicleSummary"})

FIELDS.update({
    # ------------------------------------------------------------------ driver app: sign-in
    "DriverOtpRequest": {"phone_number": "The driver's mobile number in international format (`+919000000001`; 10-15 digits, `+` optional). Required. Send it exactly as the company registered it."},
    "DriverOtpVerify": {
        "phone_number": "The same number the code was sent to. Required.",
        "otp": "The 6-digit code from the SMS (valid 5 minutes, single use). Required.",
    },
    "DriverTokenRefresh": {"refreshToken": "The `refreshToken` from sign-in (or the last refresh). Required."},
    # ------------------------------------------------------------------ driver app: profile
    "DriverMe": {
        "id": "The driver's id (UUID).",
        "full_name": "The driver's name; empty until they give it in onboarding.",
        "phone_number": "The number they sign in with.",
        "email": "Email, or empty.",
        "date_of_birth": "Date of birth (`YYYY-MM-DD`), or `null` until given.",
        "address_line": "Street address, or empty.",
        "city": "City, or empty.",
        "pincode": "6-digit postal code, or empty.",
        "profile_photo_url": "URL of the driver's selfie, or `null`.",
        "emergency_contact_name": "Who to call if the driver needs help, or empty.",
        "emergency_contact_phone": "That person's number, or empty.",
        "account_status": "`active`, `locked_dl_expired` (licence lapsed) or `disabled`.",
        "is_profile_complete": "`true` once name, date of birth and an emergency contact are all set.",
        "onboarding_status": "Where the driver is in joining: `profile_incomplete`, `documents_required`, `under_review`, `action_required` or `approved`. **Key the app's first screen on this.**",
        "aadhar_status": "Aadhaar verification: `pending`, `verified` or `rejected`. (Also inside `kyc`.)",
        "dl_status": "Driving-licence verification: `pending`, `verified` or `rejected`.",
        "police_status": "Police-verification status: `pending`, `verified` or `rejected`.",
        "dl_expiry_date": "Licence expiry (`YYYY-MM-DD`), or `null`.",
        "dl_allowed_categories": "Vehicle categories the verified licence covers - which vehicles the driver may take.",
        "aadhar_rejection_note": "Why the company rejected the Aadhaar (empty unless rejected).",
        "dl_rejection_note": "Why the company rejected the licence (empty unless rejected).",
        "police_rejection_note": "Why the company rejected the police certificate (empty unless rejected).",
        "is_eligible_for_assignment": "`true` when the driver can receive trips (all three documents verified, licence unexpired, account active).",
        "kyc": "What the driver has submitted for each document, so the app can show it back to them and what to fix.",
        "payout": "Where the driver's payouts go (account number masked).",
        "current_vehicle_id": "The vehicle the driver is on duty with, or `null` when off duty.",
        "current_vehicle": "That vehicle in full, or `null`.",
        "is_online": "`true` while the driver is on duty and can be matched to trips.",
    },
    "DriverProfileUpdate": {
        "full_name": "Full name, at least 2 characters. Locked once the Aadhaar is verified.",
        "date_of_birth": "`YYYY-MM-DD`. The driver must be 18 to 80. Locked once the Aadhaar is verified.",
        "email": "Email address.",
        "address_line": "Street address.",
        "city": "City.",
        "pincode": "6-digit postal code.",
        "emergency_contact_name": "Who to call if the driver needs help. Cannot be blank once sent.",
        "emergency_contact_phone": "That person's number (10-15 digits). **Must not be the driver's own number.** Cannot be blank once sent.",
        "payout_upi_id": "UPI id payouts are sent to, like `name@bank`. Send this *or* a full bank account.",
        "bank_account_holder": "Name on the bank account. A bank account needs all of holder, number and IFSC.",
        "bank_account_number": "Bank account number, 9-18 digits. Stored in full, shown back only as its last four digits.",
        "bank_ifsc": "The branch's IFSC code, like `HDFC0001234` (upper-cased for you).",
    },
    "DriverAadharSubmit": {
        "number": "The 12-digit Aadhaar number (spaces are ignored). Required. Only its last four digits are kept.",
        "front": "Image of the front of the card (`jpg`, `jpeg`, `png`, `webp`, up to 10 MB). Required.",
        "back": "Image of the back of the card. Required.",
    },
    "DriverDlSubmit": {
        "number": "The licence number (8-25 letters, digits, spaces or hyphens; upper-cased for you). Required.",
        "expiry_date": "The licence's expiry date (`YYYY-MM-DD`), which must be in the future. Required. The company confirms it when verifying.",
        "front": "Image of the front of the licence. Required.",
        "back": "Image of the back of the licence. Optional.",
    },
    "DriverPoliceSubmit": {"document": "The police-verification certificate: an image (`jpg`, `jpeg`, `png`, `webp`) or a PDF, up to 10 MB. Required."},
    "DriverPhoto": {"photo": "A clear picture of the driver's face (`jpg`, `jpeg`, `png`, `webp`, up to 10 MB). Required."},
    # ------------------------------------------------------------------ driver app: duty
    "DriverDutyOn": {
        "vehicle_id": "The vehicle to go on duty with - an `id` from `GET /driver/vehicles`. Required.",
        "lat": "The driver's first GPS latitude, so they can be matched immediately. Optional, but send `lat` and `lng` together.",
        "lng": "The driver's first GPS longitude. Optional, but send `lat` and `lng` together.",
    },
    "DriverLocation": {"lat": "The driver's current latitude, decimal degrees. Required.", "lng": "The driver's current longitude, decimal degrees. Required."},
    "DriverVehicleSummary": {
        "id": "The vehicle's id (UUID) - pass it as `vehicle_id` to `POST /driver/duty/start`.",
        "registration_number": "The vehicle's registration plate.",
        "capacity_kg": "Load capacity in kg (decimal string).",
        "status": "`active`, `maintenance` or `disabled`.",
        "vehicle_type": "The vehicle's type (name, category, icon).",
        "photo_url": "URL of the vehicle's photo, or `null`.",
    },
    "DriverAvailableVehicle": {"is_current": "`true` for the vehicle the driver is on duty with right now."},
    "DriverAvailableVehicles": {"vehicles": "The vehicles the driver may take on duty."},
    # ------------------------------------------------------------------ driver app: trip actions
    "TripCancel": {"reason": "Why the trip is being cancelled (up to 255 characters). Required. The other party sees it."},
    "TripComplete": {"otp": "The 6-digit delivery OTP the customer was texted. **Required for a cash-on-delivery trip**, ignored for prepaid."},
    "TripItemVerify": {
        "status": "The driver's answer: `delivered` or `not_delivered`. Required.",
        "note": "What happened, up to 255 characters. **Required when `not_delivered`**; optional otherwise.",
        "photo": "A picture of the item, taken with the phone's camera as proof. Optional. Image, up to 10 MB. A new photo replaces the old one; none keeps it.",
    },
})


def lookup(schema, field):
    """The documented meaning of `schema.field`, or None."""
    for name in (schema, ALIASES.get(schema)):
        text = FIELDS.get(name, {}).get(field)
        if text:
            return text
    return COMMON.get(field)
