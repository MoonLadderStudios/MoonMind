# Provider Profiles

**Related design documents:** [SecretsSystem.md](./SecretsSystem.md), [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md), [ManagedAndExternalAgentExecutionModel.md](../Temporal/ManagedAndExternalAgentExecutionModel.md)

Status: **Desired-State Design**
Owners: MoonMind Engineering
Last Updated: 2026-03-28

> [!NOTE]
> This document replaces the older **Auth Profiles** framing with **Provider Profiles**.
>
> A Provider Profile is broader than authentication alone. It defines:
>
> - which runtime MoonMind launches,
> - which upstream provider that runtime should target,
> - which credential source class is used,
> - which secret references or OAuth volume back the launch,
> - how provider-specific configuration is materialized into the runtime environment, and
> - which concurrency and cooldown policy applies.
>
> This document does **not** define secret storage, encryption, backend taxonomy, or secret-resolution internals. Those belong to [SecretsSystem.md](./SecretsSystem.md).
>
> This document also does **not** define browser-terminal OAuth transport. That belongs to [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md).

---

## 1. Summary

MoonMind-managed runtimes such as Gemini CLI, Claude Code, and Codex CLI do not map one-to-one to a single upstream company or a single authentication method.

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
- `gemini_cli` may run against:
  - Google via OAuth
  - Google via API key, where supported by the runtime and policy

The old question, “which auth method does this runtime use?”, is too narrow.

MoonMind instead needs to answer:

> For this run, which runtime should launch against which provider, using which credential source, materialized in which runtime-specific way, with which concurrency and cooldown policy?

The Provider Profile system is the durable execution contract that answers that question.

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

---

## 4. Non-Goals

This design does **not** attempt to:

- normalize every provider into an identical logical API,
- store raw access tokens, API keys, or refresh tokens in the profile row,
- replace runtime-specific strategy code entirely,
- eliminate the need for per-runtime launch shaping such as Claude, Gemini, or Codex command construction,
- define the browser-terminal OAuth UX,
- redefine secret backends, encryption, or rotation semantics,
- solve pricing or billing attribution by itself.

Provider Profiles define **selection and materialization**, not a universal provider protocol and not a general-purpose secret-management system.

---

## 5. Key Concepts

### 5.1 Runtime vs Provider

A **runtime** is the executable MoonMind launches.

Examples:

- `gemini_cli`
- `claude_code`
- `codex_cli`

A **provider** is the upstream service the runtime talks to.

Examples:

- `google`
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

The `secret_refs` map binds those roles to `SecretRef` values.

Example:

```yaml
secret_refs:
  provider_api_key:
    secret_id: sec_minimax_m27
    backend_type: db_encrypted
````

The exact persisted `SecretRef` schema is owned by the Secrets System. The examples in this document are illustrative.

### 5.5 Default Model Ownership

Provider Profiles are the correct place to express the default model intent for a runtime/provider combination.

Examples:

* Claude Code + MiniMax defaulting to `MiniMax-M2.7`
* Codex CLI + MiniMax defaulting to profile `m27`
* Gemini CLI + Google defaulting to a chosen Gemini model family

How that model intent gets translated into environment variables, config files, or CLI flags is runtime-specific launch shaping.

### 5.6 Provider Profile

A **Provider Profile** is a named, persistent record that binds:

* runtime
* provider
* default model intent
* credential source class
* secret references and/or OAuth volume reference
* runtime materialization strategy
* concurrency and cooldown policy
* routing metadata
* runtime-specific launch behavior

into one reusable execution target.

---

## 6. Provider Profile Model

### 6.1 Canonical Contract

```yaml
ManagedAgentProviderProfile:
  profile_id:                    str
  runtime_id:                    str            # gemini_cli | claude_code | codex_cli | ...
  provider_id:                   str            # google | anthropic | openai | minimax | zai | ...
  provider_label:                str | null

  credential_source:             str            # oauth_volume | secret_ref | none
  runtime_materialization_mode:  str            # oauth_home | api_key_env | env_bundle | config_bundle | composite

  account_label:                 str | null
  enabled:                       bool
  tags:                          [str]
  priority:                      int

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

* `gemini_google_oauth_nsticco`
* `claude_anthropic_oauth_nsticco`
* `claude_anthropic_api_team`
* `claude_minimax_m27`
* `claude_zai_default`
* `codex_openai_oauth_team`
* `codex_minimax_m27`

#### `provider_id`

Required. This is what makes the model provider-aware rather than runtime-only.

#### `credential_source`

Defines the credential source class:

* `oauth_volume`: credentials live in a mounted auth volume managed outside the profile row
* `secret_ref`: credentials resolve from the Secrets System at launch time
* `none`: no provider secret is required

#### `runtime_materialization_mode`

Defines how the runtime is prepared:

* `oauth_home`: mount auth volume and set runtime home variables
* `api_key_env`: inject a small number of environment variables containing resolved secrets
* `env_bundle`: inject a provider-specific environment block
* `config_bundle`: generate provider-specific config file(s)
* `composite`: combine multiple techniques

#### `default_model`

The provider-profile-level default model intent.

This field exists so that “default model for this provider profile” is explicit rather than being hidden only inside ad hoc environment variables.

#### `model_overrides`

Optional runtime-specific model defaults beyond the primary model.

Examples:

* `small_fast`
* `opus_equivalent`
* `haiku_equivalent`
* `codex_profile_name`

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

* Codex TOML provider stanza
* runtime-local JSON settings
* generated config fragments under `.moonmind/`

#### `command_behavior`

Runtime strategy hints that are profile-dependent rather than global.

Examples:

* `suppress_cli_model_flag_when_env_model_present: true`
* `default_codex_profile_name: "m27"`

#### `max_lease_duration_seconds`

Maximum time a slot lease is valid. The `ProviderProfileManager` may evict leases exceeding this bound as a safety net when a workflow terminates without explicitly releasing its slot.

#### `priority`

Higher values mean “prefer this profile first.”

When multiple profiles match a selector and have available slots, the highest-priority compatible profile is selected. Ties are broken by most available slots.

Recommended convention:

* `100` = normal default
* `110`–`130` = preferred alternatives
* `50`–`90` = fallback or lower-priority profiles

---

## 7. Supported Materialization Modes

### 7.1 `oauth_home`

Use when the runtime reads OAuth or session state from a home directory or config directory.

Typical behavior:

* mount or expose the profile’s auth volume
* set home-path environment variables
* clear competing API-key variables

### 7.2 `api_key_env`

Use when the provider requires a small number of environment variables, often one key.

Typical behavior:

* resolve `SecretRef` values at launch
* inject them into environment variables
* clear competing OAuth or alternative-provider variables when necessary

### 7.3 `env_bundle`

Use when a provider requires a block of environment variables rather than a single key.

This is the correct model for Anthropic-compatible third-party providers used through Claude Code, such as MiniMax and Z.AI.

### 7.4 `config_bundle`

Use when the runtime expects config files to declare model providers, profiles, or transport details.

This is the correct model for Codex CLI providers declared in config.

### 7.5 `composite`

Use when the runtime requires both generated files and environment injection.

This is the expected model for cases such as Codex CLI + MiniMax:

* write provider/profile config
* expose provider credential environment variable
* apply profile-specific command shaping

---

## 8. OAuth-Backed Provider Profiles

### 8.1 OAuth-backed profiles are profile rows, not terminal-session rows

For OAuth-backed runtimes, the browser-interactive authentication flow is owned by [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md). The Provider Profile stores the resulting durable launch target.

The Provider Profile may contain:

* `credential_source = oauth_volume`
* `volume_ref`
* `volume_mount_path`
* `account_label`
* concurrency/cooldown policy
* routing metadata

The Provider Profile must **not** contain:

* PTY bridge identifiers
* WebSocket URLs
* terminal session ids
* browser session status
* transport-specific fields

Those belong to the OAuth session subsystem.

### 8.2 OAuth registration flow

At a high level, an OAuth-backed profile is created or updated through the following flow:

1. Mission Control starts an OAuth session.
2. MoonMind creates a short-lived auth container and mounts the target auth volume.
3. Mission Control attaches through the MoonMind PTY/WebSocket bridge.
4. The runtime CLI drives the interactive login flow.
5. MoonMind verifies that valid credential state now exists in the mounted auth volume.
6. MoonMind creates or updates the Provider Profile.
7. MoonMind tears down the auth container and terminal session.

The resulting Provider Profile is transport-neutral and reusable long after the browser terminal session has ended.

### 8.3 OAuth verification and profile identity

The OAuth session workflow may set or update fields such as:

* `profile_id`
* `runtime_id`
* `provider_id`
* `account_label`
* `volume_ref`
* `volume_mount_path`
* `enabled`
* profile policy defaults

This preserves a clean separation between:

* transient interactive auth session state, and
* durable provider-profile launch state

---

## 9. Request and Selection Model

### 9.1 Why runtime-only selection is no longer enough

A request that says only:

```json
{ "agent_id": "claude_code" }
```

is ambiguous once multiple providers exist for `claude_code`.

MoonMind must not route a generic Claude request to MiniMax, Z.AI, or Anthropic arbitrarily just because one profile currently has an open slot.

Selection must become provider-aware.

### 9.2 Request Contract

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

### 9.3 Resolution Order

Provider Profile resolution must follow this order:

1. If `execution_profile_ref` is present, resolve that exact profile.
2. Otherwise, filter by `runtime_id == agent_id`.
3. If `profile_selector.provider_id` is present, filter by provider.
4. Apply tag filters.
5. Exclude disabled profiles.
6. Exclude profiles currently in cooldown.
7. Exclude profiles with no available slots.
8. Select the highest-priority compatible profile.
9. Break ties using the profile with the most free slots.

This behavior is required for correctness.

### 9.4 Default provider fallback

When neither `execution_profile_ref` nor `profile_selector.provider_id` is specified, resolution happens across all providers for the runtime.

This can route a generic request to an alternative provider if:

* the alternative profile is compatible,
* higher priority, or
* the primary profile is unavailable due to cooldown or slot exhaustion.

To prevent unintentional cross-provider routing, one or more of the following should be true:

1. **Explicit provider in request**

   * Recommended default for Mission Control UI flows.

2. **Default tag convention**

   * Only the primary provider’s profiles carry `default`, and the request includes `tags_all: ["default"]`.

3. **Priority ordering**

   * The intended primary provider has higher priority than alternatives.

---

## 10. Provider Profile Manager Workflow

### 10.1 Concept

MoonMind should treat Provider Profile slot assignment as a first-class orchestration concern.

The singleton workflow responsible for profile-capacity coordination is:

* `MoonMind.ProviderProfileManager`

### 10.2 Scope

Each runtime family gets one singleton manager workflow:

* `provider-profile-manager:gemini_cli`
* `provider-profile-manager:claude_code`
* `provider-profile-manager:codex_cli`

Per-runtime singletons are preferred over one global manager because they:

* keep workflow history growth independent per runtime,
* allow each manager to Continue-As-New independently,
* simplify concurrent slot assignment within a runtime family.

### 10.3 Responsibilities

The manager is the source of truth for:

* active profile leases
* per-profile slot capacity
* cooldown windows
* queued requests
* assignment decisions

### 10.4 Signals

| Signal            | Direction          | Payload                                                                                                                         |
| ----------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `request_slot`    | AgentRun → Manager | `{requester_workflow_id, runtime_id, requested_profile_id?, provider_id?, tags_any?, tags_all?, runtime_materialization_mode?}` |
| `release_slot`    | AgentRun → Manager | `{requester_workflow_id, profile_id}`                                                                                           |
| `report_cooldown` | AgentRun → Manager | `{profile_id, cooldown_seconds}`                                                                                                |
| `sync_profiles`   | System → Manager   | `{profiles: [...]}`                                                                                                             |
| `slot_assigned`   | Manager → AgentRun | `{profile_id}`                                                                                                                  |
| `shutdown`        | System → Manager   | none                                                                                                                            |

### 10.5 Waiting semantics

If no compatible profile is available, the run waits durably in `awaiting_slot`.

The UI and parent workflow should clearly indicate:

* runtime family
* requested provider, if any
* requested exact profile, if any

Example summary:

> Waiting for provider profile slot on `claude_code` (`provider=minimax`)

### 10.6 Cooldown behavior

On provider 429 or equivalent quota exhaustion:

1. AgentRun signals `report_cooldown(profile_id, duration)`.
2. AgentRun releases its current slot.
3. AgentRun re-requests a slot using the same selector or exact profile intent.

If another compatible profile exists, the run may continue on a different profile. Otherwise, it waits.

Claude Code and Codex CLI rate limits must be reported against the selected
provider profile whenever the failure can be attributed to that profile. The
`AgentRun` should release the current slot, report cooldown, and retry through
the same profile selector unless the request required an exact profile. If no
compatible profile is available, the run waits in `awaiting_slot`.

---

## 11. Runtime Materialization Pipeline

The launcher must build the final runtime environment in a predictable, layered way.

### 11.1 Required order

1. Start from a sane base environment.
2. Apply runtime-global defaults.
3. Remove or blank `clear_env_keys`.
4. Resolve `secret_refs` into ephemeral launch-only values where needed.
5. Materialize `file_templates`.
6. Apply `env_template`.
7. Apply `home_path_overrides`.
8. Apply runtime strategy shaping.
9. Build command.
10. Launch subprocess.

### 11.2 Critical rule: layer, do not replace

Provider Profile materialization must **layer onto** a base environment.

It must **not** replace the subprocess environment wholesale with only the profile’s env template.

Otherwise, essential variables such as `PATH`, `HOME`, and runtime process context may be lost.

### 11.3 Runtime strategy integration

Provider Profiles do not eliminate runtime strategies. Instead:

* Provider Profiles define the data needed to prepare environment variables and files.
* Runtime strategies interpret `command_behavior`, `default_model`, and runtime-specific launch rules.

Examples:

* Claude strategy may suppress `--model` when model env variables are already present.
* Codex strategy may select a generated named profile from config.
* Gemini strategy may clear conflicting keys in OAuth mode.
* A proxy-first runtime strategy may shape provider URLs toward MoonMind-owned proxy endpoints instead of direct upstream credentials.

---

## 12. Persistence Model

### 12.1 Table

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
    enabled                           BOOLEAN NOT NULL DEFAULT TRUE,
    tags                              JSONB NOT NULL DEFAULT '[]'::jsonb,
    priority                          INTEGER NOT NULL DEFAULT 100,

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
    updated_at                        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_provider_profiles_runtime
    ON managed_agent_provider_profiles(runtime_id);

CREATE INDEX ix_provider_profiles_runtime_provider
    ON managed_agent_provider_profiles(runtime_id, provider_id);

CREATE INDEX ix_provider_profiles_enabled
    ON managed_agent_provider_profiles(enabled);
```

### 12.2 Persistence rules

Secrets are never stored directly in:

* `secret_refs`
* `env_template`
* `file_templates`

Any sensitive value must be represented indirectly by a `SecretRef` or by an OAuth volume reference.

---

## 13. Security Requirements

This section states Provider-Profile-specific security rules. Secret encryption, backend behavior, and audit semantics are owned by [SecretsSystem.md](./SecretsSystem.md).

### 13.1 No raw secrets in workflow payloads

Workflows must reference only:

* `profile_id`
* optional provider or tag selectors

They must never carry:

* API keys
* refresh tokens
* OAuth access tokens
* config blobs containing raw secrets

### 13.2 No raw secrets in profile rows

Provider Profiles may store:

* `SecretRef` values
* file templates
* environment templates
* OAuth volume references

They must not store raw secret values.

### 13.3 Launch-only secret resolution

`SecretRef` values are resolved only at controlled execution boundaries and only for the minimum duration needed to launch or proxy the runtime correctly.

### 13.4 Redaction and artifact hygiene

Logs, artifacts, run metadata, and diagnostics must redact:

* secret-like strings
* resolved environment values
* generated config files containing provider credentials

Generated config files that contain secrets are sensitive runtime files, not durable artifacts by default.

### 13.5 Volume isolation

OAuth volumes remain dedicated named volumes with controlled ownership and permissions.

One runtime should not read another runtime’s credential state unless that sharing is explicitly designed and documented.

### 13.6 Clear competing variables

Before launch, conflicting variables must be cleared to avoid accidental provider fallback.

Examples:

* Anthropic OAuth profile clears competing Anthropic API-key env vars where needed.
* MiniMax Claude profile clears `ANTHROPIC_API_KEY`.
* Codex MiniMax profile clears `OPENAI_API_KEY`.

### 13.7 Proxy-first preference

When MoonMind owns the outbound provider call path, proxy-first execution is preferred.

Provider Profiles may still describe escape-hatch materialization for third-party runtimes that genuinely require direct credentials, but the system should prefer proxy-first designs whenever the runtime allows it.

---

## 14. Examples

> [!NOTE]
> The `SecretRef` objects below are illustrative. The exact serialized shape is owned by [SecretsSystem.md](./SecretsSystem.md).

### 14.1 Gemini CLI + Google OAuth

The simplest possible profile: a single OAuth volume with no provider-specific environment overrides.

```yaml
profile_id: gemini_google_oauth_nsticco
runtime_id: gemini_cli
provider_id: google
provider_label: "Google"

credential_source: oauth_volume
runtime_materialization_mode: oauth_home

account_label: "nsticco@gmail.com (Ultra)"
enabled: true
tags: ["default", "oauth"]
priority: 100

default_model: null
model_overrides: {}

max_parallel_runs: 1
cooldown_after_429_seconds: 300
rate_limit_policy: backoff
max_lease_duration_seconds: 7200

volume_ref: gemini_auth_vol_nsticco
volume_mount_path: /var/lib/gemini-auth
secret_refs: {}

clear_env_keys:
  - GEMINI_API_KEY
  - GOOGLE_API_KEY

env_template: {}
file_templates: []
home_path_overrides:
  GEMINI_HOME: /var/lib/gemini-auth
  GEMINI_CLI_HOME: /var/lib/gemini-auth

command_behavior: {}
```

### 14.2 Claude Code + Anthropic OAuth

This profile would normally be created or updated by the OAuth session workflow after terminal-based login verification succeeds.

```yaml
profile_id: claude_anthropic_oauth_nsticco
runtime_id: claude_code
provider_id: anthropic
provider_label: "Anthropic"

credential_source: oauth_volume
runtime_materialization_mode: oauth_home

account_label: "nsticco@gmail.com"
enabled: true
tags: ["default", "oauth"]
priority: 100

default_model: null
model_overrides: {}

max_parallel_runs: 1
cooldown_after_429_seconds: 300
rate_limit_policy: backoff
max_lease_duration_seconds: 7200

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

command_behavior: {}
```

### 14.3 Claude Code + Anthropic API Key

```yaml
profile_id: claude_anthropic_api_team
runtime_id: claude_code
provider_id: anthropic
provider_label: "Anthropic"

credential_source: secret_ref
runtime_materialization_mode: api_key_env

account_label: "team-default"
enabled: true
tags: ["api-key", "team"]
priority: 100

default_model: null
model_overrides: {}

max_parallel_runs: 4
cooldown_after_429_seconds: 300
rate_limit_policy: backoff
max_lease_duration_seconds: 7200

volume_ref: null
volume_mount_path: null
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

command_behavior: {}
```

### 14.4 Claude Code + MiniMax

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

volume_ref: null
volume_mount_path: null
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

### 14.5 Claude Code + Z.AI

```yaml
profile_id: claude_zai_default
runtime_id: claude_code
provider_id: zai
provider_label: "Z.AI"

credential_source: secret_ref
runtime_materialization_mode: env_bundle

account_label: "Z.AI Default"
enabled: true
tags: ["zai"]
priority: 110

default_model: null
model_overrides: {}

max_parallel_runs: 4
cooldown_after_429_seconds: 600
rate_limit_policy: backoff
max_lease_duration_seconds: 7200

volume_ref: null
volume_mount_path: null
secret_refs:
  provider_api_key:
    secret_id: sec_zai_default
    backend_type: db_encrypted

clear_env_keys:
  - ANTHROPIC_API_KEY

env_template:
  ANTHROPIC_AUTH_TOKEN:
    from_secret_ref: provider_api_key
  ANTHROPIC_BASE_URL: "https://api.z.ai/api/anthropic"
  API_TIMEOUT_MS: "3000000"

file_templates: []
home_path_overrides: {}

command_behavior: {}
```

### 14.6 Codex CLI + OpenAI OAuth

This profile would typically be produced by the OAuth session workflow rather than hand-authored.

```yaml
profile_id: codex_openai_oauth_team
runtime_id: codex_cli
provider_id: openai
provider_label: "OpenAI"

credential_source: oauth_volume
runtime_materialization_mode: oauth_home

account_label: "team-oauth"
enabled: true
tags: ["default", "oauth"]
priority: 100

default_model: null
model_overrides: {}

max_parallel_runs: 1
cooldown_after_429_seconds: 300
rate_limit_policy: backoff
max_lease_duration_seconds: 7200

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

command_behavior: {}
```

### 14.7 Codex CLI + MiniMax

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
tags: ["minimax", "m27"]
priority: 120

default_model: "codex-MiniMax-M2.7"
model_overrides:
  codex_profile_name: "m27"

max_parallel_runs: 4
cooldown_after_429_seconds: 600
rate_limit_policy: backoff
max_lease_duration_seconds: 7200

volume_ref: null
volume_mount_path: null
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

## 15. Terminology

The older **Auth Profile** terminology is deprecated in favor of **Provider Profile**.

Expected names across docs and code:

* `ManagedAgentAuthProfile` → `ManagedAgentProviderProfile`
* `MoonMind.AuthProfileManager` → `MoonMind.ProviderProfileManager`
* `managed_agent_auth_profiles` → `managed_agent_provider_profiles`

The new terminology is required because the object now represents more than authentication alone.

---

## 16. Summary

Provider Profiles replace the narrower Auth Profile concept with a provider-aware execution contract.

A Provider Profile answers all of the following for a managed runtime launch:

* which runtime is being launched,
* which upstream provider it should talk to,
* which default model intent applies,
* where credentials come from,
* how provider-specific configuration is materialized,
* which concurrency and cooldown policy applies,
* how compatible profiles are selected when no exact profile is specified.

This model is required for MoonMind to correctly support modern runtime/provider combinations such as:

* Claude Code with Anthropic OAuth
* Claude Code with Anthropic API key
* Claude Code with MiniMax
* Claude Code with Z.AI
* Codex CLI with OpenAI
* Codex CLI with MiniMax
* Gemini CLI with Google OAuth

The result is a system that is more explicit, more correct, more extensible, and better aligned with how real managed runtimes work in practice.

Most importantly, it now cleanly fits alongside the newer MoonMind architecture:

* [SecretsSystem.md](./SecretsSystem.md) owns secret references, storage, and resolution
* [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md) owns interactive OAuth session transport
* `ProviderProfiles.md` owns the durable runtime/provider launch contract
