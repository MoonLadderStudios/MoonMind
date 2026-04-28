# Data Model: Secret-Safe Settings and Managed Secrets Workflows

## ManagedSecret

- Existing table: `managed_secrets`.
- Existing fields: `slug`, encrypted `ciphertext`, `status`, `details`, `created_at`, `updated_at`.
- Browser-visible metadata derives `secretRef` as `db://<slug>`.
- Browser-visible metadata must not include `ciphertext` or plaintext.

## SecretRef

- Reference string stored in settings or profile bindings.
- `db://<slug>` references a `ManagedSecret` by slug.
- `env://<name>` references an environment-provided secret.
- Generic settings store the string only and do not resolve plaintext.

## SecretValidationDiagnostic

- `valid`: boolean result.
- `status`: normalized non-secret result, such as `active`, `missing`, `disabled`, `rotated`, `deleted`, or `invalid`.
- `checkedAt`: timestamp of validation.
- `diagnostics`: list of code/message/severity objects with no plaintext.

## State Rules

- Active managed secret refs are valid for generic binding.
- Missing, disabled, deleted, invalid, or rotated refs are explicit diagnostics for Settings.
- Replacement and rotation update encrypted value but response remains metadata-only.
