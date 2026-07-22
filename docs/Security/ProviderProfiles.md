# Provider Profiles

**Related design documents:** [SecretsSystem.md](./SecretsSystem.md), [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md), [ManagedAndExternalAgentExecutionModel.md](../Temporal/ManagedAndExternalAgentExecutionModel.md), [Codex via Omnigent Create-to-host contract](../Omnigent/CodexCreateToHostContract.md)

Status: **Desired-State Design**
Owners: MoonMind Engineering
Last Updated: 2026-06-23

> [!NOTE]
> This document replaces the older **Auth Profiles** framing with **Provider Profiles**.
>
> A Provider Profile is broader than authentication alone. It defines:
>
> - which runtime MoonMind launches,
> - which upstream provider that runtime should target,
> - which credential source class is used,
> - which secret references or OAuth volume back the launch,
> - how provider-specific configuration is materialized into the runtime environment,
> - which concurrency and cooldown policy applies, and
> - whether the profile is launchable after Settings-driven activation.
>
> This document does **not** define secret storage, encryption, backend taxonomy, or secret-resolution internals. Those belong to [SecretsSystem.md](./SecretsSystem.md).
>
> This document also does **not** define browser-terminal OAuth transport. That belongs to [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md).

---

## 1. Summary

MoonMind-managed runtimes such as Claude Code and Codex CLI do not map one-to-one to a single upstream company or a single authentication method.

Examples:

- `claude_code` may run against:
  - Anthropic via OAuth
  - Anthropic via API key
  - MiniMax via Anthropic-compatible environment shaping
  - Z.AI via Anthropic-compatible environment shaping
- `codex_cli` may run against:
  - OpenAI via OAuth
  - OpenAI via API key
  - MiniMax via config + environment materialization

The old question, “which auth method does this runtime use?”, is too narrow.

MoonMind instead needs to answer:

> For this run, which runtime should launch against which provider, using which credential source, materialized in which runtime-specific way, with which concurrency and cooldown policy?

The Provider Profile system is the durable execution contract that answers that question.

The Settings experience has one additional product rule:

> Claude and Codex provider profiles should be easy to find and configure in Settings. OAuth profiles must default to not launchable until the user successfully authenticates OAuth. API-key profiles seeded from configured environment secrets are connected and enabled by default.

This means “disabled by default” is a safety state for unconfigured providers. It is not an extra manual step after setup succeeds.

On a fresh installation with no provider credentials configured, the persisted
profile set contains exactly `claude_anthropic_oauth` and
`codex_openai_oauth`. Both profiles are disabled and not configured so Settings
can offer OAuth or API-key setup without implying launch readiness. Credential-
backed API and alternative-provider profiles are added and enabled only when
their corresponding credentials are configured.

---

## 2. Document Boundaries

Provider Profiles depend on, but do not replace, other MoonMind systems.

### 2.1 What Provider Profiles own

Provider Profiles are the semantic owner of:

- runtime selection
- provider selection
- profile-level routing metadata
- default model intent for the runtime/provider combination
- credential source class
- runtime materialization strategy
- launch shaping
- profile-level concurrency and cooldown policy
- launchable state (`enabled`) and activation metadata (`auth_state`, `disabled_reason`)

### 2.2 What the Secrets System owns

The Secrets System is the semantic owner of:

- `SecretRef` semantics
- supported secret backend classes
- encryption-at-rest behavior
- root key custody
- launch-time resolution semantics
- audit and rotation behavior
- proxy-first secret handling rules

Provider Profiles may reference secrets, but they do not define what a `SecretRef` means beyond “this field points to one.”

### 2.3 What OAuth Terminal owns

OAuth Terminal is the semantic owner of:

- browser terminal transport
- PTY/WebSocket session architecture
- OAuth session lifecycle
- auth container behavior
- terminal session metadata
- verification and profile registration flow for OAuth-backed profiles

Provider Profiles may be created or updated by the OAuth session workflow, but they do not store terminal-session transport details.

### 2.4 What Settings owns

Settings is the product surface for provider discovery and setup.

Settings should expose first-party provider cards for:

- Claude Code / Anthropic
- Codex CLI / OpenAI

Those cards may be backed by disabled setup-stub Provider Profiles or by a separate provider-offerings catalog, but the user experience must be the same:

1. The provider is visible before credentials exist.
2. The user can connect OAuth or add an API key from Settings.
3. Successful user-initiated setup makes the profile connected and enabled by default.
4. Failed setup leaves the profile disabled with clear readiness diagnostics.

---

## 3. Goals

The Provider Profile system must support all of the following:

1. **More than one profile per runtime**
   - Example: multiple `claude_code` profiles with different providers, accounts, or policies.

2. **More than one provider per runtime**
   - Example: `claude_code` with Anthropic, MiniMax, or Z.AI.

3. **More than one credential source class per provider**
   - Example: Anthropic OAuth vs Anthropic API key.

4. **More than one runtime materialization strategy**
   - Example:
     - OAuth home directory
     - single API key environment variable
     - multi-variable environment bundle
     - generated config file
     - composite file + env materialization

5. **Profile-level concurrency and cooldown**
   - Example: one MiniMax profile allows 4 parallel runs and a 10-minute cooldown after provider 429s.

6. **Explicit or selector-based routing**
   - A run may request an exact profile or select from compatible profiles using provider, tags, or materialization constraints.

7. **No raw credentials in workflow payloads or persistent profile rows**
   - Provider Profiles store references, templates, and volume metadata, not raw secret values.

8. **Correct separation between profile semantics and secret semantics**
   - Provider Profiles decide *which* secret role is needed.
   - Secrets System decides *how* the secret is stored and resolved.

9. **Correct separation between OAuth transport and OAuth-backed profile state**
   - Provider Profiles store the resulting OAuth-backed profile metadata.
   - OAuth session rows store terminal/session transport metadata.

10. **Safe Settings-first activation**
    - First-party Claude and Codex providers are discoverable in Settings.
    - They are not launchable until first successful OAuth or API-key setup.
    - A successful Settings setup action enables the profile by default.
    - Manual user/admin disables are not silently undone by passive background validation.

---

## 4. Non-Goals

This design does **not** attempt to:

- normalize every provider into an identical logical API,
- store raw access tokens, API keys, or refresh tokens in the profile row,
- replace runtime-specific strategy code entirely,
- eliminate the need for per-runtime launch shaping such as Claude or Codex command construction,
- define the browser-terminal OAuth UX,
- redefine secret backends, encryption, or rotation semantics,
- solve pricing or billing attribution by itself,
- make an unconfigured first-party provider launchable just because its runtime is installed.

Provider Profiles define **selection, activation, and materialization**, not a universal provider protocol and not a general-purpose secret-management system.

---

## 5. Key Concepts

### 5.1 Runtime vs Provider

A **runtime** is the executable MoonMind launches.

Examples:

- `claude_code`
- `codex_cli`

A **provider** is the upstream service the runtime talks to.

Examples:

- `anthropic`
- `openai`
- `minimax`
- `zai`

A runtime is not the same thing as a provider.

Examples:

- `claude_code` + `anthropic`
- `claude_code` + `minimax`
- `claude_code` + `zai`
- `codex_cli` + `openai`
- `codex_cli` + `minimax`

This distinction is foundational.

### 5.2 Credential Source Class

A Provider Profile may source credentials from one of the following classes:

- `oauth_volume`
- `secret_ref`
- `none`

Examples:

- Claude Code using Anthropic OAuth: `oauth_volume`
- Claude Code using Anthropic API key: `secret_ref`
- Claude Code using MiniMax env bundle: `secret_ref`
- Codex CLI using MiniMax config + env key: `secret_ref`

The credential source class is intentionally high-level. It does **not** encode the concrete secret backend. For `secret_ref`, the actual backend may be `env`, `db_encrypted`, `exec`, or another supported backend as defined by [SecretsSystem.md](./SecretsSystem.md).

### 5.3 Runtime Materialization Mode

A Provider Profile may materialize provider access into the runtime in one or more ways:

- `oauth_home`
- `api_key_env`
- `env_bundle`
- `config_bundle`
- `composite`

These modes describe **how** MoonMind prepares the runtime, not where the credential ultimately comes from.

### 5.4 Secret Roles vs Secret References

A Provider Profile may define one or more **secret roles** needed for launch, such as:

- `anthropic_api_key`
- `provider_api_key`
- `openai_api_key`
- `google_api_key`

The `secret_refs` map binds those roles to `SecretRef` values.

Example:

```yaml
secret_refs:
  provider_api_key:
    secret_id: sec_minimax_m27
    backend_type: db_encrypted
```

The exact persisted `SecretRef` schema is owned by the Secrets System. The examples in this document are illustrative.

### 5.5 Default Model Ownership

Provider Profiles are the correct place to express the default model intent for a runtime/provider combination.

Examples:

- Claude Code + MiniMax defaulting to `MiniMax-M2.7`
- Codex CLI + MiniMax defaulting to profile `m27`

How that model intent gets translated into environment variables, config files, or CLI flags is runtime-specific launch shaping.

### 5.6 Enabled vs Launch Ready

`enabled` is the durable operator/user intent that a profile may be used for launches.

`launch_ready` is the computed execution predicate.

A profile is launch ready only when all of the following are true:

```text
launch_ready =
  enabled == true
  AND auth_state == connected
  AND credential bindings are valid
  AND OAuth volume metadata is present when OAuth is required
  AND SecretRefs resolve to active secrets when SecretRefs are required
  AND provider-specific validation does not block launch
  AND workspace policy allows the profile
```

For legacy or non-authenticated profiles where `auth_state` is absent, readiness may temporarily fall back to the existing credential-source checks during migration. New first-party profiles must use explicit activation state.

### 5.7 Provider Profile

A **Provider Profile** is a named, persistent record that binds:

- runtime
- provider
- default model intent
- credential source class
- secret references and/or OAuth volume reference
- runtime materialization strategy
- concurrency and cooldown policy
- routing metadata
- runtime-specific launch behavior
- activation state and launchability

into one reusable execution target.

---

## 6. Provider Profile Model

### 6.1 Canonical Contract

```yaml
ManagedAgentProviderProfile:
  profile_id:                    str
  runtime_id:                    str            # claude_code | codex_cli | ...
  provider_id:                   str            # anthropic | openai | minimax | zai | ...
  provider_label:                str | null

  credential_source:             str            # oauth_volume | secret_ref | none
  runtime_materialization_mode:  str            # oauth_home | api_key_env | env_bundle | config_bundle | composite

  account_label:                 str | null
  enabled:                       bool
  is_default:                    bool
  tags:                          [str]
  priority:                      int

  # Settings / activation state
  auth_state:                    str            # not_configured | oauth_pending | api_key_pending | connected | validation_failed | disconnected
  disabled_reason:               str | null     # missing_credentials | auth_invalid | user_disabled | policy_disabled | disconnected | null
  first_authenticated_at:        timestamp | null
  last_validated_at:             timestamp | null
  last_auth_method:              str | null     # oauth_volume | secret_ref | manual | null

  # default runtime/provider intent
  default_model:                 str | null
  model_overrides:               dict[str, str] # runtime-specific named model defaults

  # concurrency / rate limiting
  max_parallel_runs:             int
  cooldown_after_429_seconds:    int
  rate_limit_policy:             str            # backoff | queue | fail_fast
  max_lease_duration_seconds:    int

  # credential source bindings
  volume_ref:                    str | null
  volume_mount_path:             str | null
  secret_refs:                   dict[str, SecretRef]

  # runtime materialization
  clear_env_keys:                [str]
  env_template:                  dict[str, object]
  file_templates:                [RuntimeFileTemplate]
  home_path_overrides:           dict[str, str]
  command_behavior:              dict[str, object]

  # ownership / audit-friendly metadata
  owner_user_id:                 str | null
  created_at:                    timestamp
  updated_at:                    timestamp

RuntimeFileTemplate:
  path:                          str
  format:                        str            # json | toml | yaml | text
  merge_strategy:                str            # replace | deep_merge | append
  content_template:              object
  permissions:                   str | null
```

### 6.2 Important Semantics

#### `profile_id`

Stable identifier referenced by workflows, APIs, and UI.

Examples:

- `claude_anthropic_oauth_nsticco`
- `claude_anthropic_api_team`
- `claude_minimax_m27`
- `claude_zai_default`
- `codex_openai_oauth_team`
- `codex_openai_api_team`
- `codex_minimax_m27`
- `claude_anthropic_oauth`
- `codex_openai_oauth`

#### `provider_id`

Required. This is what makes the model provider-aware rather than runtime-only.

#### `credential_source`

Defines the credential source class:

- `oauth_volume`: credentials live in a mounted auth volume managed outside the profile row
- `secret_ref`: credentials resolve from the Secrets System at launch time
- `none`: no provider secret is required

First-party setup stubs for Claude and Codex should use `credential_source = none` until the user successfully completes OAuth or adds a validated API key.

#### `runtime_materialization_mode`

Defines how the runtime is prepared:

- `oauth_home`: mount auth volume and set runtime home variables
- `api_key_env`: inject a small number of environment variables containing resolved secrets
- `env_bundle`: inject a provider-specific environment block
- `config_bundle`: generate provider-specific config file(s)
- `composite`: combine multiple techniques

#### `enabled`

`enabled` means “eligible for launch selection if readiness checks pass.”

For first-party Claude and Codex setup stubs, the default must be:

```yaml
enabled: false
auth_state: not_configured
disabled_reason: missing_credentials
```

When a user successfully completes OAuth or adds a validated API key in Settings, MoonMind should set:

```yaml
enabled: true
auth_state: connected
disabled_reason: null
```

unless the profile is policy-blocked or the requested action is passive/background validation of a profile that was explicitly disabled by the user or an admin.

#### `is_default`

`is_default` marks the default profile for a runtime.

A disabled or not-launch-ready setup stub must not become the runtime default. If no launch-ready profile exists for a runtime, default normalization should clear the runtime default instead of choosing a disabled setup stub.

#### `auth_state`

`auth_state` describes credential activation state:

- `not_configured`: no successful OAuth or API key has been recorded
- `oauth_pending`: an OAuth session is active but not finalized
- `api_key_pending`: an API key setup flow is active but not validated
- `connected`: credentials have been verified and the profile may be enabled
- `validation_failed`: the last attempted credential validation failed
- `disconnected`: credentials were explicitly disconnected or removed

#### `disabled_reason`

`disabled_reason` explains why `enabled` is false:

- `missing_credentials`: default state before setup
- `auth_invalid`: credentials failed validation or became invalid
- `user_disabled`: user intentionally disabled a connected profile
- `policy_disabled`: workspace/admin policy blocks launch
- `disconnected`: user disconnected OAuth or removed credentials

This field protects user intent. Background repair, migration, or passive validation must not convert `user_disabled` to enabled. A direct user-initiated Settings action such as **Connect OAuth**, **Reconnect OAuth**, or **Add API key** may enable the profile by default because that action expresses setup intent.

#### `secret_refs`

Maps secret roles to `SecretRef` values.

The Provider Profile does not own the exact serialized `SecretRef` shape; it only stores and names the references it needs.

#### `clear_env_keys`

Keys that must be removed or blanked before launch to prevent accidental fallback to another provider or auth path.

This is required for correctness and security.

#### `env_template`

Structured environment declaration. Values are literals or secret placeholders, never raw secrets stored in the profile row.

Example:

```yaml
env_template:
  ANTHROPIC_BASE_URL: "https://api.minimax.io/anthropic"
  ANTHROPIC_AUTH_TOKEN:
    from_secret_ref: provider_api_key
  API_TIMEOUT_MS: "3000000"
```

`from_secret_ref` refers to a key in the profile’s `secret_refs` map.

#### `file_templates`

Provider-specific config files to generate before launch.

Examples:

- Codex TOML provider stanza
- runtime-local JSON settings
- generated config fragments under `.moonmind/`

#### `command_behavior`

Runtime strategy hints that are profile-dependent rather than global.

Examples:

- `suppress_cli_model_flag_when_env_model_present: true`
- `default_codex_profile_name: "m27"`
- `auth_actions: ["connect_oauth", "use_api_key"]`
- `auth_readiness: {"connected": false, "launch_ready": false}`

#### `max_lease_duration_seconds`

Maximum time a slot lease is valid. The `ProviderProfileManager` may evict leases exceeding this bound as a safety net when a workflow terminates without explicitly releasing its slot.

#### `priority`

Higher values mean “prefer this profile first.”

When multiple profiles match a selector and have available slots, the highest-priority compatible profile is selected. Ties are broken by most available slots.

Recommended convention:

- `100` = normal default
- `110`–`130` = preferred alternatives
- `50`–`90` = fallback or lower-priority profiles

---

## 7. Settings-First Activation Model

### 7.1 Principle

The Settings page should make first-party Claude and Codex providers easy to configure:

- Claude Code / Anthropic
- Codex CLI / OpenAI

These providers should be visible even before credentials exist. They should not be launchable until credentials are verified.

The expected user experience is:

| User action in Settings | Result |
| --- | --- |
| Fresh install / no credentials | Claude and Codex OAuth provider cards are visible, disabled, and marked setup required. |
| User completes OAuth | Profile becomes connected and enabled by default. |
| User adds a valid API key | Profile becomes connected and enabled by default. |
| User manually disables a connected profile | Profile stays disabled until the user explicitly enables it again. |
| OAuth or API-key validation fails | Profile stays disabled with a clear readiness error. |
| Workspace policy blocks the provider | Profile may be connected, but launch remains blocked by policy. |

### 7.2 First-party seeded profiles

MoonMind seeds disabled OAuth Provider Profiles for first-party Claude and Codex. These OAuth profiles are Settings affordances, not launch targets until OAuth setup succeeds.

Always-seeded OAuth profiles:

| Profile ID | Runtime | Provider | Initial state |
| --- | --- | --- | --- |
| `claude_anthropic_oauth` | `claude_code` | `anthropic` | disabled, setup required |
| `codex_openai_oauth` | `codex_cli` | `openai` | disabled, setup required |

When `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is configured at startup, MoonMind also seeds a corresponding API-key profile and enables it by default:

| Profile ID | Runtime | Provider | Initial state |
| --- | --- | --- | --- |
| `claude_anthropic_api` | `claude_code` | `anthropic` | enabled, connected |
| `codex_openai_api` | `codex_cli` | `openai` | enabled, connected |

An OAuth setup profile should look like:

```yaml
profile_id: claude_anthropic_oauth
runtime_id: claude_code
provider_id: anthropic
provider_label: Anthropic

credential_source: none
runtime_materialization_mode: api_key_env

enabled: false
is_default: false
auth_state: not_configured
disabled_reason: missing_credentials
tags: ["oauth", "first-party"]
priority: 100

secret_refs: {}
volume_ref: null
volume_mount_path: null

command_behavior:
  supported_auth_methods: ["oauth_volume", "secret_ref"]
  auth_actions:
    - connect_oauth
    - use_api_key
  auth_status_label: "Not connected"
  auth_readiness:
    connected: false
    launch_ready: false
```

A separate provider-offerings catalog may replace setup stubs later, but the launch semantics must remain the same: no successful credential setup means no launchable profile.

### 7.3 User-initiated activation

A user-initiated Settings activation is any of:

- **Connect OAuth**
- **Reconnect OAuth**
- **Add API key**
- **Rotate API key** where the new key validates successfully

When one of these succeeds, MoonMind should enable the profile by default:

```yaml
auth_state: connected
disabled_reason: null
enabled: true
first_authenticated_at: <preserve existing value or set now>
last_validated_at: <now>
last_auth_method: oauth_volume | secret_ref
```

This is intentionally different from passive validation. The user is actively configuring the provider so that it can be used.

### 7.4 Passive validation and user-disabled guard

Passive validation includes:

- background credential checks
- migrations
- admin repair jobs
- readiness refreshes
- provider health probes

Passive validation may update `auth_state`, `last_validated_at`, and diagnostics, but it must not silently re-enable a profile with:

```yaml
disabled_reason: user_disabled
```

Only an explicit user/admin enable action or a direct setup action in Settings may clear `user_disabled`.

### 7.5 Manual disable and disconnect

Manual disable:

```yaml
enabled: false
disabled_reason: user_disabled
auth_state: connected
```

Disconnect OAuth or remove credentials:

```yaml
enabled: false
disabled_reason: disconnected
auth_state: disconnected
volume_ref: null      # for OAuth-backed profiles
volume_mount_path: null
secret_refs: {}       # for API-key-backed profiles, if credential removal is requested
```

Validation failure:

```yaml
enabled: false
disabled_reason: auth_invalid
auth_state: validation_failed
```

Policy block:

```yaml
enabled: false
disabled_reason: policy_disabled
```

or, if product needs to preserve the user’s enabled preference while showing a launch block:

```yaml
enabled: true
disabled_reason: null
# computed launch_ready remains false because policy blocks launch
```

The second pattern is preferable when the policy may be temporary, because it keeps user intent separate from policy enforcement.

### 7.6 Settings UI states

Before setup:

```text
Claude Code
Status: Not connected
Enabled: Off / unavailable until connected
Actions: Connect OAuth · Add API key
```

After successful OAuth:

```text
Claude Code
Status: OAuth connected
Enabled: On
Actions: Disable · Reconnect OAuth · Add API key
```

After successful API key:

```text
Claude Code
Status: API key connected
Enabled: On
Actions: Disable · Rotate key · Switch to OAuth
```

When manually disabled:

```text
Claude Code
Status: Connected, disabled by user
Enabled: Off
Actions: Enable · Reconnect OAuth · Rotate key
```

When policy-blocked:

```text
Claude Code
Status: Connected, blocked by workspace policy
Enabled: On or policy-locked Off, depending on policy model
Actions: View policy · Contact admin
```

### 7.7 API behavior

Provider profile create/update endpoints must not trust client-provided `enabled=true` for first-party setup stubs unless credentials are verified.

Recommended behavior:

```python
def may_enable_profile(profile, *, action, readiness, policy) -> bool:
    if policy.blocks_launch(profile):
        return False
    if action in {"connect_oauth", "reconnect_oauth", "use_api_key", "rotate_api_key"}:
        return readiness.credentials_verified
    if profile.disabled_reason == "user_disabled":
        return False
    return readiness.launch_ready
```

Patch requests that set `enabled=true` should fail with a validation error when readiness blockers remain:

```text
Provider profile cannot be enabled until OAuth or API-key credentials are verified.
```

---

## 8. OAuth-Backed Provider Profiles

### 8.1 OAuth-backed profiles are profile rows, not terminal-session rows

For OAuth-backed runtimes, the browser-interactive authentication flow is owned by [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md). The Provider Profile stores the resulting durable launch target.

The Provider Profile may contain:

- `credential_source = oauth_volume`
- `runtime_materialization_mode = oauth_home`
- `volume_ref`
- `volume_mount_path`
- `account_label`
- `auth_state = connected`
- `enabled = true`
- concurrency/cooldown policy
- routing metadata

The Provider Profile must **not** contain:

- PTY bridge identifiers
- WebSocket URLs
- terminal session ids
- browser session status
- OAuth access tokens
- refresh tokens

Those belong to the OAuth session subsystem or to provider-owned credential storage in the mounted auth volume.

### 8.2 OAuth registration flow

At a high level, an OAuth-backed profile is created or updated through the following flow:

1. The dashboard starts an OAuth session from Settings.
2. MoonMind creates a short-lived auth container and mounts the target auth volume.
3. The dashboard attaches through the MoonMind PTY/WebSocket bridge, where required.
4. The runtime CLI drives the interactive login flow.
5. MoonMind verifies that valid credential state now exists in the mounted auth volume.
6. MoonMind creates or updates the Provider Profile.
7. MoonMind marks the profile connected and enabled by default.
8. MoonMind tears down the auth container and terminal session.

The resulting Provider Profile is transport-neutral and reusable long after the browser terminal session has ended.

### 8.3 OAuth success behavior

After successful OAuth verification:

```python
profile.credential_source = ProviderCredentialSource.OAUTH_VOLUME
profile.runtime_materialization_mode = RuntimeMaterializationMode.OAUTH_HOME
profile.volume_ref = session.volume_ref
profile.volume_mount_path = session.volume_mount_path
profile.auth_state = "connected"
profile.disabled_reason = None
profile.first_authenticated_at = profile.first_authenticated_at or now
profile.last_validated_at = now
profile.last_auth_method = "oauth_volume"
profile.enabled = True
```

This auto-enable behavior is correct because OAuth finalization is a direct user-initiated setup action.

### 8.4 OAuth failure behavior

After failed OAuth verification:

```python
profile.auth_state = "validation_failed"
profile.disabled_reason = "auth_invalid"
profile.enabled = False
```

The profile should remain visible in Settings with diagnostics and a retry action.

### 8.5 OAuth disconnect behavior

When a user disconnects OAuth:

```python
profile.auth_state = "disconnected"
profile.disabled_reason = "disconnected"
profile.enabled = False
profile.volume_ref = None
profile.volume_mount_path = None
```

If the profile also has a separate API-key credential, product may offer “switch to API key” rather than fully disconnecting all credential methods.

---

## 9. API-Key-Backed Provider Profiles

### 9.1 API-key setup flow

API keys should be added through Settings or a dedicated provider profile credentials endpoint. The raw key must never be stored directly in the Provider Profile row.

Recommended endpoint shape:

```http
POST /provider-profiles/{profile_id}/credentials/api-key
```

Request:

```json
{
  "api_key": "raw key supplied by user",
  "account_label": "optional label",
  "make_default": false,
  "enable_after_validation": true
}
```

Server behavior:

1. Check `provider_profiles.write` permission.
2. Validate the API key using provider-specific validation.
3. Store the key in the Secrets System.
4. Write only a `SecretRef` into the Provider Profile.
5. Populate `env_template`, `clear_env_keys`, and any runtime-specific command behavior.
6. Mark the profile connected.
7. Enable the profile by default because this is a direct user-initiated setup action.
8. Return readiness metadata, never the raw key.

### 9.2 API-key success behavior

After successful API-key validation:

```python
profile.credential_source = ProviderCredentialSource.SECRET_REF
profile.runtime_materialization_mode = RuntimeMaterializationMode.API_KEY_ENV
profile.secret_refs[role] = secret_ref
profile.auth_state = "connected"
profile.disabled_reason = None
profile.first_authenticated_at = profile.first_authenticated_at or now
profile.last_validated_at = now
profile.last_auth_method = "secret_ref"
profile.enabled = True
```

### 9.3 API-key failure behavior

After failed API-key validation:

```python
profile.auth_state = "validation_failed"
profile.disabled_reason = "auth_invalid"
profile.enabled = False
```

The raw candidate key must not be persisted in workflow payloads, profile rows, diagnostics, audit rows, or artifacts.

### 9.4 Recommended first-party API-key mappings

```yaml
claude_code + anthropic:
  secret_role: anthropic_api_key
  env_template:
    ANTHROPIC_API_KEY:
      from_secret_ref: anthropic_api_key
  clear_env_keys:
    - ANTHROPIC_AUTH_TOKEN
    - ANTHROPIC_BASE_URL

codex_cli + openai:
  secret_role: openai_api_key
  env_template:
    OPENAI_API_KEY:
      from_secret_ref: openai_api_key
  clear_env_keys:
    - MINIMAX_API_KEY
```

Runtime-specific strategies may adjust these defaults when a CLI requires a different key name, config file, or home directory behavior.

---

## 10. Request and Selection Model

### 10.1 Why runtime-only selection is no longer enough

A request that says only:

```json
{ "agent_id": "claude_code" }
```

is ambiguous once multiple providers exist for `claude_code`.

MoonMind must not route a generic Claude request to MiniMax, Z.AI, or Anthropic arbitrarily just because one profile currently has an open slot.

Selection must become provider-aware and readiness-aware.

### 10.2 Request Contract

```yaml
AgentExecutionRequest:
  agent_id: str
  execution_profile_ref: str | null
  profile_selector:
    provider_id: str | null
    tags_any: [str]
    tags_all: [str]
    runtime_materialization_mode: str | null
```

### 10.3 Resolution Order

Provider Profile resolution must follow this order:

1. If `execution_profile_ref` is present, resolve that exact profile.
2. Otherwise, filter by `runtime_id == agent_id`.
3. If `profile_selector.provider_id` is present, filter by provider.
4. Apply tag filters.
5. Exclude disabled profiles.
6. Exclude profiles that are not launch ready.
7. Exclude profiles currently in cooldown.
8. Exclude profiles with no available slots.
9. Select the highest-priority compatible profile.
10. Break ties using the profile with the most free slots.

This behavior is required for correctness.

### 10.4 Default provider fallback

When neither `execution_profile_ref` nor `profile_selector.provider_id` is specified, resolution happens across all launch-ready providers for the runtime.

This can route a generic request to an alternative provider if:

- the alternative profile is compatible,
- the alternative profile is launch ready,
- the alternative has higher priority, or
- the primary profile is unavailable due to cooldown or slot exhaustion.

To prevent unintentional cross-provider routing, one or more of the following should be true:

1. **Explicit provider in request**
   - Recommended default for MoonMind dashboard flows.

2. **Default tag convention**
   - Only the primary provider’s launch-ready profiles carry `default`, and the request includes `tags_all: ["default"]`.

3. **Priority ordering**
   - The intended primary provider has higher priority than alternatives.

Disabled setup stubs must never participate in default provider fallback.

---

## 11. Provider Profile Manager Workflow

### 11.1 Concept

MoonMind should treat Provider Profile slot assignment as a first-class orchestration concern.

The singleton workflow responsible for profile-capacity coordination is:

- `MoonMind.ProviderProfileManager`

### 11.2 Scope

Each runtime family gets one singleton manager workflow:

- `provider-profile-manager:claude_code`
- `provider-profile-manager:codex_cli`

Per-runtime singletons are preferred over one global manager because they:

- keep workflow history growth independent per runtime,
- allow each manager to Continue-As-New independently,
- simplify concurrent slot assignment within a runtime family.

### 11.3 Responsibilities

The manager is the source of truth for:

- active profile leases
- per-profile slot capacity
- cooldown windows
- queued requests
- assignment decisions

### 11.4 Manager sync payload

The profile manager should receive only profiles that are both enabled and launch ready.

```python
profiles_payload = [
    profile
    for profile in provider_profiles
    if profile.enabled and provider_profile_launch_ready(profile)
]
```

This prevents disabled setup stubs from becoming runnable simply because they exist in the database.

### 11.5 Signals

| Signal | Direction | Payload |
| --- | --- | --- |
| `request_slot` | AgentRun → Manager | `{requester_workflow_id, runtime_id, priority?, requested_profile_id?, provider_id?, tags_any?, tags_all?, runtime_materialization_mode?}` |
| `release_slot` | AgentRun → Manager | `{requester_workflow_id, profile_id}` |
| `report_cooldown` | AgentRun → Manager | `{profile_id, cooldown_seconds}` |
| `sync_profiles` | System → Manager | `{profiles: [...]}` |
| `slot_assigned` | Manager → AgentRun | `{profile_id}` |
| `shutdown` | System → Manager | none |

### 11.6 Waiting semantics

If no compatible launch-ready profile is available, the run waits durably in `awaiting_slot` or fails fast according to the request policy.

The UI and parent workflow should clearly indicate:

- runtime family
- requested provider, if any
- requested exact profile, if any
- whether the missing condition is capacity, cooldown, policy, or provider setup

Example summary:

> Waiting for provider profile slot on `claude_code` (`provider=minimax`)

Example setup-required summary:

> Claude Code is not connected. Connect OAuth or add an API key in Settings.

### 11.7 Cooldown behavior

On provider 429 or equivalent quota exhaustion:

1. AgentRun signals `report_cooldown(profile_id, duration)`.
2. AgentRun releases its current slot.
3. AgentRun re-requests a slot using the same selector or exact profile intent.

If another compatible launch-ready profile exists, the run may continue on a different profile. Otherwise, it waits.

Claude Code and Codex CLI rate limits must be reported against the selected provider profile whenever the failure can be attributed to that profile. The `AgentRun` should release the current slot, report cooldown, and retry through the same profile selector unless the request required an exact profile. If no compatible profile is available, the run waits in `awaiting_slot`.

The slot-release + cooldown path is gated by provider error classification. Runtime strategies must classify provider rate-limit signals as `failure_class="integration_error"` with `provider_error_code="429"`, or an equivalent retry recommendation that asks the orchestration layer to wait for provider cooldown instead of retrying immediately.

---

## 12. Runtime Materialization Pipeline

The launcher must build the final runtime environment in a predictable, layered way.

### 12.1 Required order

1. Start from a sane base environment.
2. Apply runtime-global defaults.
3. Load the selected Provider Profile.
4. Re-check launch readiness at the launch boundary.
5. Remove or blank `clear_env_keys`.
6. Resolve `secret_refs` into ephemeral launch-only values where needed.
7. Materialize `file_templates`.
8. Apply `env_template`.
9. Apply `home_path_overrides`.
10. Apply runtime strategy shaping.
11. Build command.
12. Launch subprocess.

### 12.2 Critical rule: layer, do not replace

Provider Profile materialization must **layer onto** a base environment.

It must **not** replace the subprocess environment wholesale with only the profile’s env template.

Otherwise, essential variables such as `PATH`, `HOME`, and runtime process context may be lost.

### 12.3 Runtime strategy integration

Provider Profiles do not eliminate runtime strategies. Instead:

- Provider Profiles define the data needed to prepare environment variables and files.
- Runtime strategies interpret `command_behavior`, `default_model`, and runtime-specific launch rules.

Examples:

- Claude strategy may suppress `--model` when model env variables are already present.
- Codex strategy may select a generated named profile from config.
- A proxy-first runtime strategy may shape provider URLs toward MoonMind-owned proxy endpoints instead of direct upstream credentials.

---

## 13. Persistence Model

### 13.1 Table

The provider-aware registry uses `managed_agent_provider_profiles`.

```sql
CREATE TABLE managed_agent_provider_profiles (
    profile_id                        TEXT PRIMARY KEY,
    runtime_id                        TEXT NOT NULL,
    provider_id                       TEXT NOT NULL,
    provider_label                    TEXT,

    credential_source                 TEXT NOT NULL,   -- oauth_volume | secret_ref | none
    runtime_materialization_mode      TEXT NOT NULL,   -- oauth_home | api_key_env | env_bundle | config_bundle | composite

    account_label                     TEXT,
    enabled                           BOOLEAN NOT NULL DEFAULT FALSE,
    is_default                        BOOLEAN NOT NULL DEFAULT FALSE,
    tags                              JSONB NOT NULL DEFAULT '[]'::jsonb,
    priority                          INTEGER NOT NULL DEFAULT 100,

    auth_state                        TEXT NOT NULL DEFAULT 'not_configured',
    disabled_reason                   TEXT,
    first_authenticated_at            TIMESTAMPTZ,
    last_validated_at                 TIMESTAMPTZ,
    last_auth_method                  TEXT,

    default_model                     TEXT,
    model_overrides                   JSONB NOT NULL DEFAULT '{}'::jsonb,

    volume_ref                        TEXT,
    volume_mount_path                 TEXT,
    secret_refs                       JSONB NOT NULL DEFAULT '{}'::jsonb,

    clear_env_keys                    JSONB NOT NULL DEFAULT '[]'::jsonb,
    env_template                      JSONB NOT NULL DEFAULT '{}'::jsonb,
    file_templates                    JSONB NOT NULL DEFAULT '[]'::jsonb,
    home_path_overrides               JSONB NOT NULL DEFAULT '{}'::jsonb,
    command_behavior                  JSONB NOT NULL DEFAULT '{}'::jsonb,

    max_parallel_runs                 INTEGER NOT NULL DEFAULT 1,
    cooldown_after_429_seconds        INTEGER NOT NULL DEFAULT 300,
    rate_limit_policy                 TEXT NOT NULL DEFAULT 'backoff',
    max_lease_duration_seconds        INTEGER NOT NULL DEFAULT 7200,

    owner_user_id                     UUID NULL,
    created_at                        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_provider_profiles_auth_state CHECK (
        auth_state IN (
            'not_configured',
            'oauth_pending',
            'api_key_pending',
            'connected',
            'validation_failed',
            'disconnected'
        )
    ),
    CONSTRAINT ck_provider_profiles_disabled_reason CHECK (
        disabled_reason IS NULL OR disabled_reason IN (
            'missing_credentials',
            'auth_invalid',
            'user_disabled',
            'policy_disabled',
            'disconnected'
        )
    )
);

CREATE INDEX ix_provider_profiles_runtime
    ON managed_agent_provider_profiles(runtime_id);

CREATE INDEX ix_provider_profiles_runtime_provider
    ON managed_agent_provider_profiles(runtime_id, provider_id);

CREATE INDEX ix_provider_profiles_enabled
    ON managed_agent_provider_profiles(enabled);

CREATE INDEX ix_provider_profiles_auth_state
    ON managed_agent_provider_profiles(auth_state);
```

### 13.2 Persistence rules

Secrets are never stored directly in:

- `secret_refs`
- `env_template`
- `file_templates`
- `command_behavior`

Any sensitive value must be represented indirectly by a `SecretRef` or by an OAuth volume reference.

### 13.3 Default values

New unconfigured first-party setup stubs should default to:

```yaml
enabled: false
auth_state: not_configured
disabled_reason: missing_credentials
credential_source: none
```

New custom profiles should also be created disabled unless the create request is part of a verified setup flow. This makes the safe state the default across all provider profile creation paths.

---

## 14. Security Requirements

This section states Provider-Profile-specific security rules. Secret encryption, backend behavior, and audit semantics are owned by [SecretsSystem.md](./SecretsSystem.md).

### 14.1 No raw secrets in workflow payloads

Workflows must reference only:

- `profile_id`
- optional provider or tag selectors

They must never carry:

- API keys
- refresh tokens
- OAuth access tokens
- config blobs containing raw secrets

### 14.2 No raw secrets in profile rows

Provider Profiles may store:

- `SecretRef` values
- file templates
- environment templates
- OAuth volume references
- redacted provider readiness metadata

They must not store raw secret values.

### 14.3 Launch-only secret resolution

`SecretRef` values are resolved only at controlled execution boundaries and only for the minimum duration needed to launch or proxy the runtime correctly.

### 14.4 Redaction and artifact hygiene

Logs, artifacts, run metadata, diagnostics, and audit rows must redact:

- secret-like strings
- resolved environment values
- generated config files containing provider credentials
- terminal output that may contain OAuth codes or credential paths

Generated config files that contain secrets are sensitive runtime files, not durable artifacts by default.

### 14.5 Volume isolation

OAuth volumes remain dedicated named volumes with controlled ownership and permissions.

One runtime should not read another runtime’s credential state unless that sharing is explicitly designed and documented.

### 14.6 Clear competing variables

Before launch, conflicting variables must be cleared to avoid accidental provider fallback.

Examples:

- Anthropic OAuth profile clears competing Anthropic API-key env vars where needed.
- MiniMax Claude profile clears `ANTHROPIC_API_KEY`.
- Codex MiniMax profile clears `OPENAI_API_KEY`.

### 14.7 Proxy-first preference

When MoonMind owns the outbound provider call path, proxy-first execution is preferred.

Provider Profiles may still describe escape-hatch materialization for third-party runtimes that genuinely require direct credentials, but the system should prefer proxy-first designs whenever the runtime allows it.

### 14.8 No accidental auto-enable

A profile may be auto-enabled only after successful user-initiated credential setup.

These events may auto-enable:

- OAuth finalize from Settings
- API-key add from Settings
- API-key rotation from Settings, after successful validation

These events must not auto-enable a user-disabled profile:

- background validation
- migration
- readiness diagnostics
- provider health checks
- manager sync

---

## 15. Examples

> [!NOTE]
> The `SecretRef` objects below are illustrative. The exact serialized shape is owned by [SecretsSystem.md](./SecretsSystem.md).

### 15.1 Claude Code + Anthropic OAuth setup profile

```yaml
profile_id: claude_anthropic_oauth
runtime_id: claude_code
provider_id: anthropic
provider_label: "Anthropic"

credential_source: none
runtime_materialization_mode: api_key_env

account_label: null
enabled: false
is_default: false
auth_state: not_configured
disabled_reason: missing_credentials
tags: ["oauth", "first-party"]
priority: 100

secret_refs: {}
volume_ref: null
volume_mount_path: null

clear_env_keys:
  - ANTHROPIC_API_KEY
  - ANTHROPIC_AUTH_TOKEN
  - ANTHROPIC_BASE_URL

env_template: {}
file_templates: []
home_path_overrides: {}

command_behavior:
  supported_auth_methods: ["oauth_volume"]
  auth_actions: ["connect_oauth"]
  auth_status_label: "Not connected"
  auth_readiness:
    connected: false
    launch_ready: false
```

### 15.2 Claude Code + Anthropic OAuth after setup

This profile is created or updated by the OAuth session workflow after terminal-based login verification succeeds.

```yaml
profile_id: claude_anthropic_oauth
runtime_id: claude_code
provider_id: anthropic
provider_label: "Anthropic"

credential_source: oauth_volume
runtime_materialization_mode: oauth_home

account_label: "nsticco@gmail.com"
enabled: true
is_default: true
auth_state: connected
disabled_reason: null
last_auth_method: oauth_volume
tags: ["default", "oauth", "first-party"]
priority: 100

volume_ref: claude_auth_vol_nsticco
volume_mount_path: /home/app/.claude
secret_refs: {}

clear_env_keys:
  - ANTHROPIC_API_KEY
  - ANTHROPIC_AUTH_TOKEN

env_template: {}
file_templates: []
home_path_overrides:
  CLAUDE_HOME: /home/app/.claude

command_behavior:
  auth_status_label: "Claude OAuth ready"
  auth_readiness:
    connected: true
    launch_ready: true
```

### 15.3 Claude Code + Anthropic API key after setup

```yaml
profile_id: claude_anthropic_api
runtime_id: claude_code
provider_id: anthropic
provider_label: "Anthropic"

credential_source: secret_ref
runtime_materialization_mode: api_key_env

account_label: "team-default"
enabled: true
is_default: true
auth_state: connected
disabled_reason: null
last_auth_method: secret_ref
tags: ["default", "api-key", "first-party"]
priority: 100

secret_refs:
  anthropic_api_key:
    secret_id: sec_anthropic_team_default
    backend_type: db_encrypted

clear_env_keys:
  - ANTHROPIC_AUTH_TOKEN
  - ANTHROPIC_BASE_URL

env_template:
  ANTHROPIC_API_KEY:
    from_secret_ref: anthropic_api_key

file_templates: []
home_path_overrides: {}

command_behavior:
  auth_status_label: "Anthropic API key ready"
  auth_readiness:
    connected: true
    launch_ready: true
```

### 15.4 Codex CLI + OpenAI OAuth after setup

```yaml
profile_id: codex_openai_oauth
runtime_id: codex_cli
provider_id: openai
provider_label: "OpenAI"

credential_source: oauth_volume
runtime_materialization_mode: oauth_home

account_label: "team-oauth"
enabled: true
is_default: true
auth_state: connected
disabled_reason: null
last_auth_method: oauth_volume
tags: ["default", "oauth", "first-party"]
priority: 100

volume_ref: codex_auth_vol_team
volume_mount_path: /home/app/.codex
secret_refs: {}

clear_env_keys:
  - OPENAI_API_KEY
  - MINIMAX_API_KEY

env_template: {}
file_templates: []
home_path_overrides:
  CODEX_HOME: /home/app/.codex

command_behavior:
  auth_status_label: "Codex OAuth ready"
  auth_readiness:
    connected: true
    launch_ready: true
```

### 15.5 Codex CLI + OpenAI API key after setup

```yaml
profile_id: codex_openai_api
runtime_id: codex_cli
provider_id: openai
provider_label: "OpenAI"

credential_source: secret_ref
runtime_materialization_mode: api_key_env

account_label: "team-default"
enabled: true
is_default: true
auth_state: connected
disabled_reason: null
last_auth_method: secret_ref
tags: ["default", "api-key", "first-party"]
priority: 100

secret_refs:
  openai_api_key:
    secret_id: sec_openai_team_default
    backend_type: db_encrypted

clear_env_keys:
  - OPENAI_BASE_URL
  - OPENAI_ORG_ID
  - OPENAI_PROJECT
  - MINIMAX_API_KEY

env_template:
  OPENAI_API_KEY:
    from_secret_ref: openai_api_key
file_templates: []
home_path_overrides: {}

command_behavior:
  auth_status_label: "OpenAI API key ready"
  auth_readiness:
    connected: true
    launch_ready: true
```

### 15.6 Claude Code + MiniMax

MiniMax exposes Anthropic-compatible configuration for Claude Code through environment variables. This is a provider-specific `env_bundle`, not merely “generic API key mode.”

```yaml
profile_id: claude_minimax_m27
runtime_id: claude_code
provider_id: minimax
provider_label: "MiniMax"

credential_source: secret_ref
runtime_materialization_mode: env_bundle

account_label: "MiniMax M2.7"
enabled: true
auth_state: connected
disabled_reason: null
tags: ["minimax", "m27"]
priority: 120

default_model: "MiniMax-M2.7"
model_overrides:
  small_fast: "MiniMax-M2.7"
  sonnet_equivalent: "MiniMax-M2.7"
  opus_equivalent: "MiniMax-M2.7"
  haiku_equivalent: "MiniMax-M2.7"

max_parallel_runs: 4
cooldown_after_429_seconds: 600
rate_limit_policy: backoff
max_lease_duration_seconds: 7200

secret_refs:
  provider_api_key:
    secret_id: sec_minimax_m27
    backend_type: db_encrypted

clear_env_keys:
  - ANTHROPIC_API_KEY

env_template:
  ANTHROPIC_BASE_URL: "https://api.minimax.io/anthropic"
  ANTHROPIC_AUTH_TOKEN:
    from_secret_ref: provider_api_key
  API_TIMEOUT_MS: "3000000"
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1"
  ANTHROPIC_MODEL: "MiniMax-M2.7"
  ANTHROPIC_SMALL_FAST_MODEL: "MiniMax-M2.7"
  ANTHROPIC_DEFAULT_SONNET_MODEL: "MiniMax-M2.7"
  ANTHROPIC_DEFAULT_OPUS_MODEL: "MiniMax-M2.7"
  ANTHROPIC_DEFAULT_HAIKU_MODEL: "MiniMax-M2.7"

file_templates: []
home_path_overrides: {}

command_behavior:
  suppress_cli_model_flag_when_env_model_present: true
```

### 15.7 Codex CLI + MiniMax

Codex CLI uses a provider config entry plus a profile entry, backed by an environment variable containing the provider key. This is a `composite` profile.

```yaml
profile_id: codex_minimax_m27
runtime_id: codex_cli
provider_id: minimax
provider_label: "MiniMax"

credential_source: secret_ref
runtime_materialization_mode: composite

account_label: "MiniMax M2.7"
enabled: true
auth_state: connected
disabled_reason: null
tags: ["minimax", "m27"]
priority: 120

default_model: "codex-MiniMax-M2.7"
model_overrides:
  codex_profile_name: "m27"

max_parallel_runs: 4
cooldown_after_429_seconds: 600
rate_limit_policy: backoff
max_lease_duration_seconds: 7200

secret_refs:
  provider_api_key:
    secret_id: sec_minimax_m27
    backend_type: db_encrypted

clear_env_keys:
  - OPENAI_API_KEY

env_template:
  MINIMAX_API_KEY:
    from_secret_ref: provider_api_key

file_templates:
  - path: "~/.codex/config.toml"
    format: toml
    merge_strategy: deep_merge
    permissions: "0600"
    content_template:
      model_providers:
        minimax:
          name: "MiniMax Chat Completions API"
          base_url: "https://api.minimax.io/v1"
          env_key: "MINIMAX_API_KEY"
          wire_api: "chat"
          requires_openai_auth: false
          request_max_retries: 4
          stream_max_retries: 10
          stream_idle_timeout_ms: 300000
      profiles:
        m27:
          model: "codex-MiniMax-M2.7"
          model_provider: "minimax"

home_path_overrides: {}

command_behavior:
  default_codex_profile_name: "m27"
```

---

## 16. Migration Plan

1. Add activation columns:
   - `auth_state`
   - `disabled_reason`
   - `first_authenticated_at`
   - `last_validated_at`
   - `last_auth_method`

2. Change model/API defaults:
   - `enabled` defaults to `false` for new profiles unless creation occurs inside a verified setup flow.
   - first-party setup stubs default to `auth_state=not_configured` and `disabled_reason=missing_credentials`.

3. Seed or backfill first-party profiles for:
   - `claude_anthropic_oauth`
   - `codex_openai_oauth`
   - `claude_anthropic_api` when `ANTHROPIC_API_KEY` is configured
   - `codex_openai_api` when `OPENAI_API_KEY` is configured

4. Backfill existing launch-ready OAuth profiles:
   - `auth_state=connected`
   - `last_auth_method=oauth_volume`
   - `enabled=true` if they are currently enabled and not policy-blocked

5. Backfill existing launch-ready SecretRef profiles:
   - `auth_state=connected`
   - `last_auth_method=secret_ref`
   - `enabled=true` if they are currently enabled and not policy-blocked

6. Disable profiles with missing credentials:
   - `enabled=false`
   - `auth_state=not_configured` or `validation_failed`
   - `disabled_reason=missing_credentials` or `auth_invalid`

7. Preserve explicit user/admin disables:
   - `enabled=false`
   - `disabled_reason=user_disabled`

8. Update default normalization:
   - choose only launch-ready profiles
   - clear default when no launch-ready profile exists

9. Update ProviderProfileManager sync:
   - sync only enabled and launch-ready profiles

10. Update Settings UI:
    - show first-party setup cards
    - expose Connect OAuth and Add API key actions
    - auto-enable after successful user-initiated setup

---

## 17. Acceptance Tests

The implementation should include tests for the following behavior:

```text
New Claude/Codex provider setup stubs are disabled by default.
ProviderProfileCreate does not default first-party managed providers to enabled.
PATCH enabled=true fails when OAuth/API-key readiness is missing.
Settings Connect OAuth finalizes only after volume verification succeeds.
Successful OAuth setup sets auth_state=connected and enabled=true.
Successful API-key setup stores a SecretRef and sets enabled=true.
Failed OAuth setup leaves the profile disabled with validation diagnostics.
Failed API-key setup does not persist the raw key and leaves the profile disabled.
Manual user disable is not overwritten by background validation.
Direct user-initiated reconnect or add-key may clear user_disabled and enable the profile.
ProviderProfileManager sync excludes disabled and not-ready profiles.
Default provider selection does not choose disabled setup stubs.
workflow.default_provider_profile_ref rejects disabled or not-ready profiles.
Settings provider cards show setup actions for Claude and Codex.
```

---

## 18. Terminology

The older **Auth Profile** terminology is deprecated in favor of **Provider Profile**.

Expected names across docs and code:

- `ManagedAgentAuthProfile` → `ManagedAgentProviderProfile`
- `MoonMind.AuthProfileManager` → `MoonMind.ProviderProfileManager`
- `managed_agent_auth_profiles` → `managed_agent_provider_profiles`

The new terminology is required because the object now represents more than authentication alone.

---

## 19. Summary

Provider Profiles replace the narrower Auth Profile concept with a provider-aware execution contract.

A Provider Profile answers all of the following for a managed runtime launch:

- which runtime is being launched,
- which upstream provider it should talk to,
- which default model intent applies,
- where credentials come from,
- how provider-specific configuration is materialized,
- which concurrency and cooldown policy applies,
- how compatible profiles are selected when no exact profile is specified,
- whether Settings has activated the profile for launch.

This model is required for MoonMind to correctly support modern runtime/provider combinations such as:

- Claude Code with Anthropic OAuth
- Claude Code with Anthropic API key
- Claude Code with MiniMax
- Claude Code with Z.AI
- Codex CLI with OpenAI
- Codex CLI with MiniMax

The Settings activation rule is intentionally simple:

> First-party OAuth provider profiles are visible but not launchable until OAuth setup. API-key profiles are seeded and enabled when their configured environment secret exists. Successful user-initiated setup enables the profile by default. Manual disables and policy blocks are respected.

The result is a system that is explicit, secure by default, easy to configure, and aligned with how real managed runtimes work in practice.

Most importantly, it cleanly fits alongside the newer MoonMind architecture:

- [SecretsSystem.md](./SecretsSystem.md) owns secret references, storage, and resolution
- [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md) owns interactive OAuth session transport
- `ProviderProfiles.md` owns the durable runtime/provider launch contract and Settings activation semantics
