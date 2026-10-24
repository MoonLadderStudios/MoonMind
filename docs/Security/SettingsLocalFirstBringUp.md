# Local-first Settings Bring-Up

Related designs: [Settings](SettingsSystem.md), [Secrets](SecretsSystem.md),
[Provider Profiles](ProviderProfiles.md).

A fresh MoonMind instance uses the existing dashboard editors for provider
profiles, managed secrets and source-control connections. The Settings API
example below separately demonstrates a SecretRef-backed instance override;
it is not a dashboard-only walkthrough.

Use the deployment's protected operator URL, with guarded conversion complete.
Local access uses loopback transport; remote access uses approved ingress.
No MoonMind application account or external identity provider is needed. See
[Operator and machine access](AuthenticationContracts.md).

## Dashboard setup

Open **Settings → Providers & Secrets**:

- Use **Provider Profiles** to select a Harness, Provider and authentication
  method, create a profile, and configure its credentials in the existing
  profile editor. These operations use `/api/v1/provider-profiles` and its
  credential endpoints. Profile readiness and the Harness default are managed
  there; see [Provider Profiles](ProviderProfiles.md) and
  [OAuth Terminal](../ManagedAgents/OAuthTerminal.md).
- For GitHub repository access, use **Source Control → Add token connection**.
  Saving sends the token to `POST /api/v1/repository-connections/pat`, which
  stores it as a managed secret and returns connection metadata. Assign an
  `owner/repo` in the connection detail pane, choosing read-only or Publish
  access. Assignments use `POST /api/v1/repository-connections/{id}/assignments`;
  saving a token alone does not assign a repository.
- Use **Managed Secrets** when another consumer needs a named SecretRef.
  Create a secret, copy its `db://` reference, and use **View usage** to inspect
  its consumers. This editor manages secrets; it does not create a Settings
  binding merely by saving one.

**Settings → Instance** edits the `user-workspace` catalog section. That name
selects a section, not a person or workspace scope: all settings use one
instance configuration. The default page does not expose
`integrations.github.token_ref`, whose descriptor belongs to
`providers-secrets`. Use the Source Control editor for repository connections;
the override example below is an API operation.

## 1. Read the empty-state catalog

```http
GET /api/v1/settings/catalog?section=user-workspace
```

On a fresh database, descriptors report an inherited source (`default`,
`config_file` or `environment`), an effective value and a source explanation.
The initial `value_version` is `1`. Catalog responses do not contain
plaintext, ciphertext or resolved secret values.

For the API example's GitHub descriptor, read the `providers-secrets` section.
Use its current `value_version` in subsequent writes instead of assuming the
fresh-database version on an existing installation.

## 2. Create a managed secret

Managed Secrets uses the same endpoint as this request:

```http
POST /api/v1/secrets
{
  "slug": "github-pat-local",
  "plaintext": "<github-token-present-only-in-request>"
}
```

The response returns metadata and `secretRef="db://github-pat-local"`, never
the plaintext. The database stores the secret encrypted with status `ACTIVE`.
Later Settings operations pass the reference rather than the token.

## 3. Bind the reference through the Settings API

This example writes the GitHub descriptor directly; it is not an action on
**Settings → Instance** and does not create a Source Control connection:

```http
PATCH /api/v1/settings
{
  "changes": {"integrations.github.token_ref": "db://github-pat-local"},
  "expected_versions": {"integrations.github.token_ref": 1}
}
```

The write creates an instance override without a subject or scope. Its
`setting_changed` event reports `source="instance_override"` and
`apply_mode="next_launch"`. The catalog reports `source="secret_ref"` for a
resolvable `db://` binding, with a source explanation. The override stores
only the reference; audit events are redacted according to the descriptor's
policy and never contain the token.

## 4. Inspect usage

In Managed Secrets, **View usage** requests:

```http
GET /api/v1/secrets/github-pat-local/usage
```

After the API write, the response lists a `setting_override` consumer for
`integrations.github.token_ref`, named `Instance setting
integrations.github.token_ref`. It does not return the underlying plaintext.

## 5. Reset the API example

```http
DELETE /api/v1/settings/integrations.github.token_ref
```

This removes the instance override and records a `settings.override.reset`
audit event. The value inherits from the default, configuration file or
environment again; the descriptor's default remains unchanged. The managed
secret remains available to its other consumers.

## Recovery and verification

Restoring `settings_overrides` without the referenced `managed_secrets` can
leave a broken binding. `scan_broken_references(session)` from
`api_service.services.settings_backup` reports missing or disabled references;
the catalog remains readable and surfaces `secret_ref_unresolved` diagnostics.
See the [backup/recovery contract tests](../../tests/integration/api/test_settings_backup_recovery_contract.py).

The [local-first API smoke test](../../tests/integration/api/test_settings_local_first_smoke.py)
exercises steps 1–5 against a fresh SQLite database and the real
`api_service.main.app`. It checks the API responses, persisted secret,
redacted audits and reset. It supplies an admitted operator dependency, so it
does not establish transport/ingress admission, the dashboard editor journey,
provider credential validation or model-backed workflow completion. It is part
of the `integration_ci` suite (`./tools/test_integration.sh`).

OAuth bootstrap, operator locks and operational commands have their own
procedures in [OAuth Terminal](../ManagedAgents/OAuthTerminal.md) and the
[Settings design](SettingsSystem.md).
