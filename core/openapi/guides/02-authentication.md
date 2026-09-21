## Overview

Every endpoint except sign-in itself needs a **bearer token**:

```
Authorization: Bearer <token>
```

There are **three kinds of token** - one for each kind of caller (a *principal*). Each token is tied to one company, and the server decides what you may do from the
kind of token you present.

| Principal | Who it is | How to get a token | Lifetime | Renew with |
|---|---|---|---|---|
| **API client** | A company's **backend** (server-to-server integration) | `POST /auth/client-token` with a client id + secret | 60 minutes | Request a new one - **no refresh token** |
| **Admin** | A person using the company's **admin panel** | `POST /auth/login` with email + password | access 60 min, refresh 7 days | `POST /auth/refresh` |
| **Driver** | A person using the **driver app** | `POST /driver/auth/otp/request`, then `.../otp/verify` (SMS code) | access 60 min, refresh 30 days | `POST /driver/auth/refresh` |

In this documentation the three bearer schemes are named **ApiClientToken**, **AdminToken** and **DriverToken**. In Swagger UI, click **Authorize** and paste the token into the matching box
(they are all "Bearer" tokens - only the source differs).

## Who can call what

| Area | API client | Admin | Driver |
|---|:-:|:-:|:-:|
| Trips - estimate, book, list, read, assign, cancel | yes | yes | no |
| Vehicle types, vehicles, vehicle documents | yes | yes | no |
| Uploads (`POST /uploads`) | yes | yes | no |
| **Drivers** - roster, KYC review, **driver wallets** | **no** | yes | no |
| **Own vehicles** (`/driver/my-vehicles`) and **vehicle types for drivers** | no | no | yes |
| **Driver app** endpoints (`/driver/...`) | no | no | yes |

Calling an endpoint with the wrong kind of token returns **`403 PERMISSION_DENIED`** (not 401): you are signed in, just not as the right principal. Every operation in this reference
states **"Who may call this"**.

## For a server-to-server integration (API client)

```bash
curl -X POST "$BASE_URL/auth/client-token" \
  -H "Content-Type: application/json" \
  -d '{"client_id": "0b1c3a52-6f0e-4d3e-9a55-1f2b7c9d4e10", "client_secret": "sk_live_..."}'
```

```json
{ "access": "eyJhbGciOiJIUzI1NiIs...", "expires_in": 3600 }
```

- Keep the **client secret on your server**. Never put it in a web page or mobile app. It is shown once when the client is created; if it is lost, have a new client created.
- Cache the token and reuse it until it is about to expire (`expires_in` seconds). When any call returns **`401 TOKEN_NOT_VALID`**, fetch a new token and retry once.
- API clients are created by the platform operator for your company; ask them for your client id and secret.

## For the admin panel

`POST /auth/login` with `email` and `password` returns an `access` and a `refresh` token. Use `access` on every call; before it expires (or when you get `401 TOKEN_NOT_VALID`) trade the
`refresh` token at `POST /auth/refresh` for a new `access` token. When the refresh token itself expires, the person signs in again.

## For the driver app

Drivers have no passwords. They sign in with their **phone number and an SMS code**:

1. `POST /driver/auth/otp/request` with the phone number - the driver gets a 6-digit code (valid 5 minutes).
2. `POST /driver/auth/otp/verify` with the phone and the code - you get an `accessToken`, a `refreshToken` and the driver's profile.
3. Send `Authorization: Bearer <accessToken>` on every driver call.
4. Before the access token expires, `POST /driver/auth/refresh` returns a **new pair** - store both tokens again. A driver stays signed in for up to 30 days without another SMS.
5. `POST /driver/auth/logout` revokes the refresh token.

The full flow is in *Driver app walkthrough*.

## Things that trip people up

- **Sign-in endpoints ignore any `Authorization` header**, so a stale token left in your HTTP client's default headers can't stop you getting a new one.
- **`401` vs `403`.** `401` means *we don't accept your token* (missing, expired, malformed - `NOT_AUTHENTICATED` or `TOKEN_NOT_VALID`): get a new token. `403` means *your token is fine but is the
  wrong kind, or the action isn't yours* (`PERMISSION_DENIED`, `NOT_YOUR_TRIP`): a new token won't help.
- **An account that gets disabled stops working immediately**, even with an unexpired token: the token is re-checked against the account on every request.
- **Tokens are not interchangeable across environments.** A token from staging is `TOKEN_NOT_VALID` in production.
