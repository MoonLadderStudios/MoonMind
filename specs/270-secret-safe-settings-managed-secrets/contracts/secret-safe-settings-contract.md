# Contract: Secret-Safe Settings and Managed Secrets

## Managed Secret Metadata

`GET /api/v1/secrets` and mutating secret endpoints return metadata items:

```json
{
  "slug": "github-pat-main",
  "secretRef": "db://github-pat-main",
  "status": "active",
  "details": {},
  "createdAt": "2026-04-28T00:00:00Z",
  "updatedAt": "2026-04-28T00:00:00Z"
}
```

The response must not include `ciphertext`, `plaintext`, decrypted values, tokens, auth headers, private keys, or credential-bearing generated config.

## Secret Validation

`GET /api/v1/secrets/{slug}/validate` returns a redacted diagnostic envelope:

```json
{
  "valid": true,
  "status": "active",
  "checkedAt": "2026-04-28T00:00:00Z",
  "diagnostics": [
    {
      "code": "secret_ref_resolvable",
      "message": "Managed secret is active.",
      "severity": "info"
    }
  ]
}
```

Missing or inactive secrets return `valid: false` with redacted diagnostics and never include plaintext.

## Settings SecretRef Diagnostics

Settings catalog and effective responses for `db://<slug>` values report explicit diagnostics when the backing managed secret is missing or not active. Generic settings still return the SecretRef string only.
