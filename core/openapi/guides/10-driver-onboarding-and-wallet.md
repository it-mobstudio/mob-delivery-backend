## Joining: self sign-up and onboarding

A driver can be **created by the company** (`POST /drivers`, admin token) or can **sign themselves up** from the app: signing in with a phone number nobody has registered - when the server allows self sign-up -
creates a brand-new, empty driver account. Either way a driver **cannot receive trips until the company has verified their documents**.

### The journey

```
 sign up (SMS OTP)         about me           documents              company review          approved
 profile_incomplete ──▶ documents_required ──▶ under_review ──▶  approved
                                                   │  ▲
                                          rejected │  │ re-submit
                                                   ▼  │
                                              action_required
```

`onboarding_status` (on `GET /driver/me` and in the company's driver list) says where a driver is. It is **derived, never stored**, from their profile and the state of their documents:

| `onboarding_status` | Meaning | What happens next |
|---|---|---|
| `profile_incomplete` | Missing name, date of birth or emergency contact. | The driver fills in *about me* (`PATCH /driver/me`). |
| `documents_required` | Profile done; Aadhaar or licence not yet uploaded. | The driver uploads them. |
| `under_review` | Everything provided; waiting on the company. | The company verifies (below). |
| `action_required` | The company **rejected** a document. `kyc.<doc>.rejection_note` says why. | The driver fixes and re-submits. |
| `approved` | Aadhaar, licence and police all verified, licence unexpired, account active. | The driver can go on duty and receive trips. |

A driver the company created and verified is `approved` regardless of profile details.

### What the driver provides

| Item | Endpoint (multipart where files) | Notes |
|---|---|---|
| About me | `PATCH /driver/me` | Name, date of birth (18-80), email, address, city, pincode, **emergency contact** (not their own number), payout details. All optional individually. |
| Selfie | `POST /driver/me/photo` | `photo` |
| **Aadhaar** | `POST /driver/me/kyc/aadhar` | `number` (12 digits), `front`, `back`. **Only the last 4 digits of the number are kept.** |
| **Driving licence** | `POST /driver/me/kyc/dl` | `number`, `expiry_date` (future), `front`, `back` (optional). |
| **Police verification** | `POST /driver/me/kyc/police` | `document` (image or PDF). Optional to upload - the company may verify it another way. |
| Payout details | `PATCH /driver/me` | A `payout_upi_id` *or* bank details (`bank_account_holder`, `bank_account_number`, `bank_ifsc` - all three). |

Each upload replaces the previous one and puts that document back in review. Once the company has **verified** a document it is **locked** (`KYC_ALREADY_VERIFIED`), and after Aadhaar is verified the driver's
**name and date of birth are locked too** (`PROFILE_LOCKED`).

### The company reviews (admin token)

`GET /drivers` lists your drivers, each with its `onboarding_status` (filter with `aadhar_status`, `dl_status`, `police_status` or `account_status` - e.g. `?dl_status=pending` for licences waiting on you), and `GET /drivers/{id}/kyc` shows the scans. Then decide each document:

```bash
curl -X PATCH "$BASE_URL/drivers/$DRIVER/kyc/aadhar" -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" -d '{"status": "verified"}'
curl -X PATCH "$BASE_URL/drivers/$DRIVER/kyc/police" -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" -d '{"status": "rejected", "note": "Certificate is unreadable - please upload a clearer scan"}'
curl -X PATCH "$BASE_URL/drivers/$DRIVER/kyc/dl"     -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"status": "verified", "expiry_date": "2031-06-30", "allowed_categories": ["two_wheeler", "three_wheeler"]}'
```

- A **rejection needs a `note`** - the driver sees it.
- Verifying the **licence** also needs the `expiry_date` you read off it (in the future) and the `allowed_categories` it covers: that decides which vehicles the driver may take (`two_wheeler`, `three_wheeler`, `four_wheeler`).
- When a verified licence's expiry date passes, the driver's account is **locked** automatically (`ACCOUNT_LOCKED`) until a new licence is verified.

## The wallet

Every driver has a **wallet**: a running ledger of what the company owes them.

- **Earning**: when a trip completes, the driver is credited a share of its fare - **80% by default** (`DRIVER_EARNING_PERCENT`); the rest is the company's margin. The trip shows it as `driver_earning`.
- **Payouts** happen **outside** this system (bank or UPI transfer by the company). The company then **records** each one (`POST /drivers/{id}/wallet/transactions`, `kind: payout`) which lowers the balance. The driver's payout details (`GET /driver/me` -> `payout`) say where to send it.
- The company can also record a **bonus** (adds), a **penalty** (subtracts) or an **adjustment** (a correction, either sign).
- The ledger is **append-only**. A mistake is fixed by another entry, never by editing history - so the driver's statement always adds up. Each row carries the running `balance_after`.

| Kind | Effect on the balance | Who creates it |
|---|---|---|
| `trip_earning` | + | the system, on trip completion |
| `bonus` | + | the company |
| `penalty` | - | the company |
| `payout` | - | the company (after paying the driver) |
| `adjustment` | + or - | the company |

**Balance** = every credit minus every payout, penalty and negative adjustment. **Earnings** (today / week / month / lifetime) count trip earnings and bonuses only.

| Who | Reads it | Records entries |
|---|---|---|
| The driver | `GET /driver/wallet` (summary), `GET /driver/wallet/transactions` (statement), `GET /driver/stats` | - |
| The company (admin) | `GET /drivers/{id}/wallet` (balance + last 20 entries) | `POST /drivers/{id}/wallet/transactions` |

A payout larger than the balance is refused (`INSUFFICIENT_BALANCE`), so read the balance first.

## Leaving

A driver can **delete their own account** (`DELETE /driver/me`) - an app-store requirement for apps with self sign-up. It is refused while they have an **active trip** or while the company still **owes them money**
(`WALLET_BALANCE_PENDING`: pay the balance out first). A deleted (or company-removed) driver is disabled and signed out; their trips and wallet history are kept.
