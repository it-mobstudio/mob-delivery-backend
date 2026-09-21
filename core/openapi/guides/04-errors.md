## The error envelope

Every error - from a validation failure to a business rule - has the same shape:

```json
{
  "success": false,
  "error": {
    "code": "TRIP_NOT_CANCELLABLE",
    "message": "A trip in status 'in_progress' cannot be cancelled."
  }
}
```

| Field | Meaning |
|---|---|
| `success` | Always `false` for an error. (Successful responses don't have this wrapper: the body **is** the resource.) |
| `error.code` | A **stable, machine-readable** code. **Branch on this.** |
| `error.message` | A sentence for people. Safe to show a user, but it may be reworded - **never match on it.** |
| `error.details` | Only for `INVALID`: an object naming every bad field, `{"field": ["problem", ...]}`. |

## Validation errors: `INVALID`

When a request has bad or missing fields you get **HTTP 400** with code `INVALID` and a `details` object listing **every** problem at once, so you can fix them in one go. Nothing is changed.

```json
{
  "success": false,
  "error": {
    "code": "INVALID",
    "message": "Request could not be processed.",
    "details": {
      "pickup": ["This field is required."],
      "drop": {"contact_phone": ["Required for a COD trip - the finalizing OTP is sent to this number."]}
    }
  }
}
```

Nested objects appear under their own field name; list items are keyed by position.

## Handling errors well

1. **Look at the HTTP status first** - it tells you the *class* of problem (fix the request, get a new token, retry later).
2. **Then `error.code`** - it tells you exactly which rule. Show the `message` to people, but decide behaviour from the `code`.
3. **`5xx` and `429` are retryable** with a pause. `4xx` are not - repeating the same request gives the same error. The exception is `401 TOKEN_NOT_VALID`: get a new token, then retry once.
4. **`409` means "the world is not in the state this call needs"** - fetch the resource and look at its current status before deciding what to do.

## Every error code

The table below is generated from the same catalogue the endpoint pages use, so it is always complete. Each endpoint page lists the codes that **it** can return.

{{ERROR_CATALOG}}
