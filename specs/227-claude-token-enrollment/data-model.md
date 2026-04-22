# Data Model: Claude Token Enrollment Drawer

## ClaudeEnrollmentState

Represents the drawer-visible lifecycle state for manual token enrollment.

Fields:
- `step`: one of `not_connected`, `awaiting_external_step`, `awaiting_token_paste`, `validating_token`, `saving_secret`, `updating_profile`, `ready`, `failed`.
- `profileId`: provider profile being enrolled.
- `tokenValue`: transient local UI value for the secure paste field.
- `failureReason`: redacted failure text shown to the operator when `step` is `failed`.

Validation rules:
- `tokenValue` must be cleared when the drawer closes, cancellation occurs, or enrollment reaches `ready`.
- `failureReason` must not contain the submitted token or token-like strings.
- Empty `tokenValue` cannot transition to validation.

## ClaudeReadinessMetadata

Trusted provider-profile metadata rendered in the provider row status area.

Fields:
- `connected`: optional boolean.
- `lastValidatedAt`: optional timestamp/display string.
- `failureReason`: optional redacted status string.
- `backingSecretExists`: optional boolean.
- `launchReady`: optional boolean.

Validation rules:
- Missing fields are omitted rather than guessed.
- Failure reason is redacted before display.
- Metadata is display-only and must not include token values.

## ManualAuthRequest

Secret-carrying request made only after explicit operator submission.

Fields:
- `token`: submitted Claude token.
- `accountLabel`: optional account/profile display override.

Validation rules:
- `token` is required and non-empty.
- `token` is never rendered after submission.

## ManualAuthResult

Secret-free result used to update the drawer and provider row.

Fields:
- `status`: success or failure.
- `statusLabel`: optional display summary.
- `readiness`: optional `ClaudeReadinessMetadata`.
- `failureReason`: optional redacted or redactable failure text.

Validation rules:
- Result must not include raw token values.
- Failure text is redacted before rendering.
