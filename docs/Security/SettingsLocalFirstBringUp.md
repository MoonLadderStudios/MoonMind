# Local-first Settings Bring-Up

**Related design documents:** [SettingsSystem.md](./SettingsSystem.md), [SecretsSystem.md](./SecretsSystem.md), [ProviderProfiles.md](./ProviderProfiles.md)

Status: **Operator walkthrough — pre-release**
Owners: MoonMind Engineering
Last Updated: 2026-05-17
Ticket: **MM-713**

This document explains how a fresh, single-operator MoonMind deployment can
go from a clean database to a working Settings configuration **through
Mission Control alone**, without any of the external infrastructure that a
production deployment would normally provide (no SSO directory, no managed
identity provider, no external secret manager, no GitHub App, no Slack
integration). It satisfies the **local-first baseline** named in
`docs/Security/SettingsSystem.md` §3 (goal 10) and §22, and is exercised by
the integration_ci smoke test
[`tests/integration/api/test_settings_local_first_smoke.py`](../../tests/integration/api/test_settings_local_first_smoke.py).

The walkthrough is intentionally short, declarative, and end-to-end. Each
step links to the design section it implements so reviewers can trace it
back to the contract.

---

## 0. What "local-first" means here

In a local-first deployment the operator brings only:

1. A Postgres database (or a SQLite tree for personal use).
2. A running MoonMind API service and worker.
3. A first-party login (the `is_superuser` bootstrap user).

There is **no external secret manager**, **no OAuth IdP**, **no provider
profile pre-seed**, and **no per-workspace policy administrator**. Every
remaining configuration step happens through the Settings UI.

The Settings System's contract (§3, §4, §5, §10, §22, §29) holds even in
this minimal environment: descriptors are catalog-driven, raw secrets are
never persisted into setting overrides, every effective value carries a
source explanation, and a partial restore surfaces broken references
instead of silently degrading.

---

## 1. Empty-state catalog read (§5.1, §10, §29.8)

Right after the database is created and the application starts, the
operator can read the catalog without any prior writes:

```http
GET /api/v1/settings/catalog?section=user-workspace&scope=workspace
```

Expectations:

- Every descriptor is returned with `source` in
  `{"default", "config_file", "environment"}` because no override row
  exists yet.
- `effective_value` matches the descriptor's default or environment
  fallback.
- `value_version` is `1` (no override row, but the catalog never returns
  version `0` — see §11.1).
- No descriptor returns `plaintext`, `ciphertext`, or `resolved_value`
  fields. (§22.2, §29.5).

The smoke test asserts these properties against a fresh sqlite database.

---

## 2. Create a managed secret in Mission Control (§14.1, §27.2)

The operator opens **Settings → Providers & Secrets → Managed Secrets** and
creates a single managed secret. The browser sends:

```http
POST /api/v1/secrets
{
  "slug": "github-pat-local",
  "plaintext": "ghp_local_first_plaintext_only_in_request"
}
```

Expectations (and the test asserts them):

- The response body never echoes the plaintext (`SecretMetadataResponse`
  excludes `ciphertext`/`plaintext`/`value`). (§22.2)
- The response body advertises a SecretRef value (`secretRef =
  "db://github-pat-local"`). (§5.3, §7.9, §27.2)
- A row in `managed_secrets` is created with status `ACTIVE`.

This is the only step where plaintext crosses the wire. Future Settings
operations only ever reference the SecretRef.

---

## 3. Reference the SecretRef from a Settings override (§5.3, §10.4)

The operator goes back to **Settings → User / Workspace** and points
`integrations.github.token_ref` at the SecretRef returned in §2:

```http
PATCH /api/v1/settings/workspace
{
  "changes": {"integrations.github.token_ref": "db://github-pat-local"},
  "expected_versions": {"integrations.github.token_ref": 1}
}
```

Expectations:

- The response contains a `setting_changed` change event with
  `source="workspace_override"` and `apply_mode="next_launch"`. (§19.2)
- A new row is written to `settings_overrides` for the workspace scope.
- A row is written to `settings_audit_events` with `redacted=True` (because
  the descriptor declares `audit.redact=True`). (§21.1, §21.2, §22.8)
- The settings catalog now reports `source="workspace_override"` and
  `source_explanation` non-empty. (§29.8)

The override row stores `db://github-pat-local` as a reference — never the
plaintext that crossed the wire in §2.

---

## 4. Cross-check the SecretRef usage view (§14.4)

The Settings UI offers a usage view that lists every consumer of the
managed secret:

```http
GET /api/v1/secrets/github-pat-local/usage
```

Expectations:

- The response lists a `setting_override` consumer pointing at
  `integrations.github.token_ref` with `scope="workspace"`. (§14.4)
- The response never includes the underlying plaintext value.

This step is the operator's confidence-check that the SecretRef they just
saved is wired into the correct Settings binding.

---

## 5. Reset returns to inheritance, not to a mutated default (§5.4, §29.9)

The operator decides the GitHub binding was a test and resets it:

```http
DELETE /api/v1/settings/workspace/integrations.github.token_ref
```

Expectations:

- The response carries `source` in `{"default", "config_file",
  "environment"}` again.
- The `settings_overrides` row for the workspace scope is deleted.
- The descriptor's `default_value` is unchanged in the catalog. (§5.4)
- A `settings.override.reset` audit event is written. (§21.1)

---

## 6. Partial-restore safety (§23.3, §23.4)

If a future operator restores **only** the `settings_overrides` table
(without `managed_secrets`), the SecretRef saved in step 3 will resolve
against a missing managed secret. The local-first walkthrough does **not**
require this branch, but the operator can exercise it manually with:

```python
from api_service.services.settings_backup import scan_broken_references

broken = await scan_broken_references(session)
```

Expectations (covered by
`tests/integration/api/test_settings_backup_recovery_contract.py`):

- Every SecretRef whose target is missing/disabled is surfaced as a
  `SettingsBrokenReference`.
- The catalog still loads — the broken reference is rendered as a
  `secret_ref_unresolved` diagnostic on the descriptor, not a 500. (§23.3)

The smoke test in this story does not run the broken-reference scan
itself; it relies on the dedicated backup/recovery contract suite to keep
that path covered.

---

## 7. Where the walkthrough stops

The local-first walkthrough deliberately stops at:

- one managed secret,
- one SecretRef-bound Settings override,
- one workspace scope, and
- one reset.

It does **not** cover:

- OAuth volume bootstrap (see [`OAuthTerminal.md`](../ManagedAgents/OAuthTerminal.md))
- Multi-workspace inheritance (`SettingsSystem.md` §16.4)
- Operator locks (`SettingsSystem.md` §10.2)
- Operational commands (`SettingsSystem.md` §17)

Those flows have their own dedicated docs and tests. The intent of this
document is to keep the **smallest** path through the Settings system
testable on a clean install.

---

## 8. Smoke test mapping

The smoke test
[`tests/integration/api/test_settings_local_first_smoke.py`](../../tests/integration/api/test_settings_local_first_smoke.py)
exercises steps 1–5 against a freshly created database and the real
`api_service.main.app`. It runs under the required `integration_ci`
suite (`./tools/test_integration.sh`).

Each pytest assertion in the smoke test cites the section above with a
short comment. If a future change breaks this contract, the smoke test
fails and points back here.

## 9. Related guardrails

- `tests/unit/services/test_settings_guardrails.py` — single named
  unit-test suite mapping every §4 non-goal, §22 security requirement,
  and §29 invariant to at least one assertion.
- `tests/unit/services/test_settings_catalog_snapshot.py` — catalog
  drift detection on the required CI suite.
- `tests/integration/api/test_settings_backup_recovery_contract.py` —
  backup/restore + broken-reference scans (§23).
