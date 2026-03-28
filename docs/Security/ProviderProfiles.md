# Provider Profiles

**Implementation tracking:** [`docs/tmp/remaining-work/ManagedAgents-ManagedAgentsAuthentication.md`](../tmp/remaining-work/ManagedAgents-ManagedAgentsAuthentication.md)

Status: **Design Draft**
Owners: MoonMind Engineering

> [!NOTE]
> This document replaces the older **Auth Profiles** framing with **Provider Profiles**.
> A provider profile is broader than authentication alone: it defines which provider a runtime targets, how credentials are sourced, how provider-specific configuration is materialized into the runtime environment, and how concurrency / cooldown policy is enforced.
>
> For tmate session architecture, OAuth session UX, and provider bootstrap behavior, see [TmateArchitecture.md](../ManagedAgents/TmateArchitecture.md). For the shared `TmateSessionManager` abstraction and session lifecycle, see [TmateSessionArchitecture.md](../Temporal/TmateSessionArchitecture.md).

---

## 1. Summary

MoonMind-managed agent runtimes such as Gemini CLI, Claude Code, and Codex CLI do not always map one-to-one to a single upstream company or authentication model.

Examples:

- `claude_code` may run against:
  - Anthropic via OAuth
  - Anthropic via API key
  - MiniMax via Anthropic-compatible environment variables
  - Z.AI via Anthropic-compatible environment variables
- `codex_cli` may run against:
  - OpenAI via OAuth
  - OpenAI via API key
  - MiniMax via config-file + environment configuration

The old **auth profile** concept is too narrow for this reality. It assumes the main question is “which auth method does this runtime use?” In practice, MoonMind must answer a larger question:

> For this run, which runtime should launch against which provider, using which credential source, materialized in which runtime-specific way, with which concurrency and cooldown policy?

This document defines the **Provider Profile** system that answers that question.

This document builds on the execution model defined in [ManagedAndExternalAgentExecutionModel](../Temporal/ManagedAndExternalAgentExecutionModel.md), specifically the sections covering managed runtimes, first-class profile selection, and runtime worker topology.

---

## 2. Goals

The Provider Profile system must support all of the following:

1. **More than one profile per runtime**
   - Example: multiple `claude_code` profiles with different providers or identities.

2. **More than one provider per runtime**
   - Example: `claude_code` with Anthropic, MiniMax, or Z.AI.

3. **More than one credential source per provider**
   - Example: Anthropic OAuth vs Anthropic API key.

4. **More than one runtime materialization strategy**
   - Example:
     - OAuth home directory
     - Single API key env var
     - Multi-variable env bundle
     - Generated config file
     - Composite file + env materialization

5. **Profile-level concurrency and cooldown**
   - Example: one MiniMax profile allows 4 parallel runs and 10-minute cooldown after provider 429s.

6. **Explicit or selector-based routing**
   - A run may request an exact profile or select from compatible profiles using provider / tags / policy constraints.

7. **No raw credentials in workflow payloads or persistent profile rows**
   - Provider Profiles store references and templates, not secrets.

---

## 3. Non-Goals

This design does **not** attempt to:

- Normalize every provider into an identical logical API.
- Store raw access tokens, API keys, or refresh tokens in the profile table.
- Replace runtime-specific strategy code entirely.
- Eliminate the need for per-runtime launch strategies such as Claude, Gemini, or Codex command shaping.
- Solve pricing / billing attribution by itself.

Provider Profiles define **selection and materialization**, not a universal provider protocol.

---

## 4. Key Concepts

### 4.1 Runtime vs Provider

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

### 4.2 Credential Source

A profile may source credentials from one of the following:

- `oauth_volume`
- `secret_ref`
- `none`

Examples:

- Claude Code using Anthropic OAuth: `oauth_volume`
- Claude Code using Anthropic API key: `secret_ref`
- Claude Code using MiniMax env bundle: `secret_ref`
- Codex CLI using MiniMax config + env key: `secret_ref`

### 4.3 Runtime Materialization Mode

A profile may materialize provider access into the runtime in one or more ways:

- `oauth_home`
- `api_key_env`
- `env_bundle`
- `config_bundle`
- `composite`

These modes describe **how** MoonMind prepares the runtime environment, not where the credentials ultimately come from.

### 4.4 Proxy-First Execution vs Escape-Hatch Runtimes

Runtimes are broadly classified into two execution models regarding credential handling:

- **Proxy-First Runtimes**: Agent environments that communicate with LLM providers exclusively through MoonMind's internal API proxy (`/api/v1/proxy/`).
  - These runtimes never receive raw database-encrypted provider credentials. Instead, MoonMind injects a symmetric synthetic proxy token (`MOONMIND_PROXY_TOKEN`) and heavily shapes the environment variables to route provider traffic (e.g. `ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`) inward. 
  - The proxy service securely correlates the incoming proxy token, unwraps the true underlying secret reference, decodes it remotely using the `MasterSecretResolver`, and performs a pass-through request upstream.
  - Proxy-first provides the strictest security, defending against credential exfiltration in the case of unauthorized prompt injection or malicious dependencies.
  
- **Escape-Hatch Runtimes**: Agent environments that require direct access to provider APIs and cannot be reliably intercepted (often due to limitations in the underlying runtime like hardcoded endpoints or complex web request structures).
  - These runtimes receive direct, plain-text provider credentials (resolved on-the-fly at runtime boundary via `MasterSecretResolver`).
  - While MoonMind attempts to isolate the environment and strictly scopes the lifetime of the credentials to the run's duration, these runtimes carry higher inherent risk of credential leakage.
  - Transitioning an escape-hatch runtime to proxy-first is highly desirable and generally mediated by tagging a profile with `proxy-first`.

Examples:

- OAuth volume mounted into CLI home directory → `oauth_home`
- One provider key exposed as `ANTHROPIC_API_KEY` → `api_key_env`
- MiniMax Anthropic-compatible variables for Claude Code → `env_bundle`
- Codex CLI provider config written into TOML → `config_bundle`
- Codex CLI provider config + env key → `composite`

### 4.4 Provider Profile

A **Provider Profile** is a named, persistent record that binds:

- runtime
- provider
- credential source
- materialization strategy
- concurrency / cooldown policy
- selection metadata
- runtime-specific launch behavior

into one reusable execution target.

---

## 5. Provider Profile Model

### 5.1 Canonical Contract

```yaml
ManagedAgentProviderProfile:
  profile_id:                    str
  runtime_id:                    str            # gemini_cli | claude_code | codex_cli
  provider_id:                   str            # google | anthropic | openai | minimax | zai
  provider_label:                str | null
  credential_source:             str            # oauth_volume | secret_ref | none
  runtime_materialization_mode:  str            # oauth_home | api_key_env | env_bundle | config_bundle | composite

  account_label:                 str | null
  enabled:                       bool
  tags:                          [str]
  priority:                      int

  # concurrency / rate limiting
  max_parallel_runs:             int
  cooldown_after_429_seconds:    int
  rate_limit_policy:             str            # backoff | queue | fail_fast
  max_lease_duration_seconds:    int

  # credential source material
  volume_ref:                    str | null
  volume_mount_path:             str | null
  secret_refs:                   dict[str, str]

  # runtime materialization
  clear_env_keys:                [str]
  env_template:                  dict[str, object]
  file_templates:                [RuntimeFileTemplate]
  home_path_overrides:           dict[str, str]
  command_behavior:              dict[str, object]

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

### 5.2 Important Semantics

#### `profile_id`

Stable identifier referenced by workflows and APIs.

Examples:

* `gemini_google_oauth_nsticco`
* `claude_anthropic_oauth_nsticco`
* `claude_minimax_m27`
* `claude_zai_default`
* `codex_openai_oauth_team`
* `codex_minimax_m27`

#### `provider_id`

Required. This is what makes the model provider-aware rather than just runtime-aware.

#### `credential_source`

Defines where sensitive material comes from:

* `oauth_volume`: credentials live in a mounted volume managed outside the profile row.
* `secret_ref`: credentials are resolved from MoonMind secret storage at launch time.
* `none`: no provider secret required.

#### `runtime_materialization_mode`

Defines how the runtime is prepared:

* `oauth_home`: set runtime home variables and mount CLI state.
* `api_key_env`: inject one or more environment variables containing secrets.
* `env_bundle`: inject a provider-specific environment block.
* `config_bundle`: generate provider-specific config file(s).
* `composite`: combine multiple techniques.

#### `clear_env_keys`

Keys that must be removed or blanked before launch to prevent accidental fallback to another provider or auth path.

This is required for correctness and security.

#### `env_template`

Structured environment declaration. Values are literals or secret placeholders, never raw secrets stored in the profile record.

Example:

```yaml
env_template:
  ANTHROPIC_BASE_URL: "https://api.minimax.io/anthropic"
  ANTHROPIC_AUTH_TOKEN:
    secret_ref: provider_api_key
  API_TIMEOUT_MS: "3000000"
```

#### `file_templates`

Provider-specific config files to generate before launch.

Example:

* Codex TOML provider stanza
* Runtime-local JSON settings
* Generated config fragments under `.moonmind/`

#### `max_lease_duration_seconds`

Maximum time a slot lease is valid. The `ProviderProfileManager` evicts leases exceeding this bound as a safety net for cases where a workflow terminates without explicitly releasing its slot. See [TaskCancellation.md §6](../Tasks/TaskCancellation.md) for the full defense-in-depth model.

#### `priority`

Higher values mean "try this profile first." When multiple profiles match a selector and have available slots, the profile with the highest `priority` value is selected. Ties are broken by most available slots.

Convention: `100` = default, `110`–`130` = preferred alternatives, `50`–`90` = fallback / lower-priority profiles.

#### `command_behavior`

Runtime strategy hints that are profile-dependent rather than global.

Examples:

* `suppress_cli_model_flag_when_env_model_present: true`
* `default_codex_profile_name: "m27"`

---

## 6. Supported Materialization Modes

### 6.1 `oauth_home`

Use when the runtime reads OAuth/session state from a home directory or config directory.

Typical behavior:

* Mount or expose the profile's auth volume.
* Set home-path environment variables.
* Clear competing API-key variables.

Example:

```python
env = {
    "CLAUDE_HOME": "/home/app/.claude",
    "ANTHROPIC_API_KEY": None,
    "ANTHROPIC_AUTH_TOKEN": None,
    "ANTHROPIC_BASE_URL": None,
}
```

### 6.2 `api_key_env`

Use when the provider requires a small number of environment variables, often one key.

Typical behavior:

* Resolve secret refs at launch.
* Inject values into environment.
* Clear competing OAuth / alternative-provider keys when necessary.

### 6.3 `env_bundle`

Use when a provider requires a block of environment variables rather than one API key.

This is the correct model for Anthropic-compatible third-party providers used through Claude Code, such as MiniMax and Z.AI.

### 6.4 `config_bundle`

Use when the runtime expects config files to declare model providers, profiles, or transport details.

This is the correct model for Codex CLI providers declared in config.

### 6.5 `composite`

Use when the runtime requires both generated files and environment injection.

This is the expected model for Codex CLI + MiniMax:

* write provider/profile config
* expose `MINIMAX_API_KEY`

---

## 7. Examples

### 7.1 Gemini CLI + Google OAuth

The simplest possible profile — a single OAuth volume with no provider-specific env overrides.

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

### 7.2 Claude Code + Anthropic OAuth

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

> [!NOTE]
> `ANTHROPIC_BASE_URL` is **not** in `clear_env_keys` for the OAuth profile. Transport-layer variables should only be cleared when they would cause the runtime to reach the wrong provider. If the OAuth profile should never use a non-default base URL, the runtime strategy can enforce that separately.

### 7.3 Claude Code + Anthropic API Key

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
max_parallel_runs: 4
cooldown_after_429_seconds: 300
rate_limit_policy: backoff
max_lease_duration_seconds: 7200
volume_ref: null
volume_mount_path: null
secret_refs:
  anthropic_api_key: db://providers/anthropic/team-default
clear_env_keys:
  - ANTHROPIC_AUTH_TOKEN
  - ANTHROPIC_BASE_URL
env_template:
  ANTHROPIC_API_KEY:
    secret_ref: anthropic_api_key
file_templates: []
home_path_overrides: {}
command_behavior: {}
```

### 7.4 Claude Code + MiniMax

MiniMax exposes Anthropic-compatible configuration for Claude Code through environment variables. This is not well-modeled as “just API key mode”; it is a provider-specific `env_bundle`.

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
max_parallel_runs: 4
cooldown_after_429_seconds: 600
rate_limit_policy: backoff
max_lease_duration_seconds: 7200
volume_ref: null
volume_mount_path: null
secret_refs:
  provider_api_key: db://providers/minimax/m27
clear_env_keys:
  - ANTHROPIC_API_KEY
env_template:
  ANTHROPIC_BASE_URL: "https://api.minimax.io/anthropic"
  ANTHROPIC_AUTH_TOKEN:
    secret_ref: provider_api_key
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

### 7.5 Claude Code + Z.AI

Z.AI is another Claude-compatible provider materialized through an environment bundle.

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
max_parallel_runs: 4
cooldown_after_429_seconds: 600
rate_limit_policy: backoff
max_lease_duration_seconds: 7200
volume_ref: null
volume_mount_path: null
secret_refs:
  provider_api_key: db://providers/zai/default
clear_env_keys:
  - ANTHROPIC_API_KEY
env_template:
  ANTHROPIC_AUTH_TOKEN:
    secret_ref: provider_api_key
  ANTHROPIC_BASE_URL: "https://api.z.ai/api/anthropic"
  API_TIMEOUT_MS: "3000000"
file_templates: []
home_path_overrides: {}
command_behavior: {}
```

### 7.6 Codex CLI + OpenAI OAuth

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

### 7.7 Codex CLI + MiniMax

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
max_parallel_runs: 4
cooldown_after_429_seconds: 600
rate_limit_policy: backoff
max_lease_duration_seconds: 7200
volume_ref: null
volume_mount_path: null
secret_refs:
  provider_api_key: db://providers/minimax/m27
clear_env_keys:
  - OPENAI_API_KEY
env_template:
  MINIMAX_API_KEY:
    secret_ref: provider_api_key
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

## 8. Request and Selection Model

### 8.1 Why Runtime-Only Selection Is No Longer Enough

A request that says only:

```json
{ "agentId": "claude_code" }
```

is ambiguous once multiple providers exist for `claude_code`.

MoonMind must not route a generic Claude request to MiniMax, Z.AI, or Anthropic arbitrarily just because one profile currently has an open slot.

Selection must become provider-aware.

### 8.2 Request Contract

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

### 8.3 Resolution Order

Provider Profile resolution must follow this order:

1. If `execution_profile_ref` is present, resolve that exact profile.
2. Otherwise, filter by `runtime_id == agent_id`.
3. If `profile_selector.provider_id` is present, filter by provider.
4. Apply tag filters.
5. Exclude disabled profiles.
6. Exclude profiles currently in cooldown.
7. Exclude profiles with no available slots.
8. Select highest-priority compatible profile.
9. Break ties using the profile with the most free slots.

This behavior is required for correctness.

### 8.4 Default Provider Fallback

When neither `execution_profile_ref` nor `profile_selector.provider_id` is specified, the resolution order above applies across **all** providers for the runtime. This could route a generic `claude_code` request to MiniMax or Z.AI if those profiles happen to have open slots and higher priority.

To prevent unintentional cross-provider routing, one of the following must be true:

1. **Explicit provider in request** — The UI or API caller includes `profile_selector.provider_id` (recommended default behavior for the Task Dashboard).
2. **Default tag convention** — Only the primary provider's profiles carry the `default` tag, and the request includes `tags_all: ["default"]`.
3. **Priority ordering** — The primary provider's profiles have higher `priority` values than alternative providers, making them always win when slots are available.

Option 3 is the lowest-friction approach and works out of the box with the priority convention defined in §5.2. However, operators managing multiple providers should be aware that during periods when all primary-provider slots are in cooldown, runs **will** fall through to lower-priority alternative providers if any are available and compatible.

---

## 9. Provider Profile Manager Workflow

### 9.1 Renamed Concept

The older `MoonMind.AuthProfileManager` concept should evolve to **`MoonMind.ProviderProfileManager`**.

The responsibilities remain similar, but the meaning of the managed resource changes:

* before: auth slots
* now: provider-profile slots

### 9.2 Scope

Each runtime family gets one singleton manager workflow:

* `provider-profile-manager:gemini_cli`
* `provider-profile-manager:claude_code`
* `provider-profile-manager:codex_cli`

Per-runtime singletons are preferred over a single global manager because they keep workflow history growth independent per runtime, allow each manager to hit its Continue-As-New threshold without disrupting other runtimes, and simplify reasoning about concurrent slot assignment within a single runtime family.

The manager remains the single source of truth for:

* active leases
* per-profile slot capacity
* cooldown windows
* queued requests
* assignment decisions

### 9.3 Signals

| Signal            | Direction          | Payload                                                                                                                         |
| ----------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `request_slot`    | AgentRun → Manager | `{requester_workflow_id, runtime_id, requested_profile_id?, provider_id?, tags_any?, tags_all?, runtime_materialization_mode?}` |
| `release_slot`    | AgentRun → Manager | `{requester_workflow_id, profile_id}`                                                                                           |
| `report_cooldown` | AgentRun → Manager | `{profile_id, cooldown_seconds}`                                                                                                |
| `sync_profiles`   | System → Manager   | `{profiles: [...]}`                                                                                                             |
| `slot_assigned`   | Manager → AgentRun | `{profile_id}`                                                                                                                  |
| `shutdown`        | System → Manager   | none                                                                                                                            |

### 9.4 Waiting Semantics

If no compatible profile is available, the run waits durably in `awaiting_slot`.

The parent workflow and UI should clearly indicate the reason:

* runtime family
* requested provider, if any
* requested exact profile, if any

Example summary:

> Waiting for provider profile slot on `claude_code` (`provider=minimax`)

### 9.5 Cooldown Behavior

On detected provider 429 or equivalent quota exhaustion:

1. AgentRun signals `report_cooldown(profile_id, duration)`
2. AgentRun releases its current slot
3. AgentRun re-requests a slot using the same selector / exact profile intent

If another compatible profile exists, the run may continue on a different profile. Otherwise, it waits.

---

## 10. Runtime Materialization Pipeline

The launcher must build the final runtime environment in a predictable, layered way.

### 10.1 Required Order

1. Start from a sane base environment.
2. Apply runtime-global defaults.
3. Remove or blank `clear_env_keys`.
4. Resolve `secret_refs` into ephemeral launch-only values.
5. Materialize `file_templates`.
6. Apply `env_template`.
7. Apply `home_path_overrides`.
8. Apply runtime strategy shaping.
9. Build command.
10. Launch subprocess.

### 10.2 Critical Rule: Layer, Do Not Replace

Provider Profile materialization must **layer onto** a base environment.

It must **not** replace the subprocess environment wholesale with only the profile env template.

Otherwise, essential variables such as `PATH`, `HOME`, and runtime-specific process context may be lost.

### 10.3 Runtime Strategy Integration

Provider Profiles do not eliminate runtime strategies. Instead:

* Provider Profiles define the data needed to prepare the environment and files.
* Runtime strategies interpret `command_behavior` and runtime-specific launch rules.

Examples:

* Claude strategy may suppress `--model` when model env variables are present.
* Codex strategy may select a generated named profile.
* Gemini strategy may clear conflicting keys in OAuth mode.

---

## 11. Persistence Model

### 11.1 Table

The provider-aware registry supersedes `managed_agent_auth_profiles` with `managed_agent_provider_profiles`.

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

### 11.2 Secrets

Secrets are never stored directly in:

* `secret_refs`
* `env_template`
* `file_templates`

Any sensitive value must be represented indirectly by a secret reference resolved at launch time.

---

## 12. Security Requirements

### 12.1 No Raw Secrets in Workflow Payloads

Workflows must reference only:

* `profile_id`
* optional provider / tag selectors

They must never carry:

* API keys
* refresh tokens
* OAuth access tokens
* config blobs containing raw secrets

### 12.2 No Raw Secrets in Profile Rows

Provider Profiles store:

* secret references
* file templates
* env templates
* volume references

They do not store secret values.

### 12.3 Redaction

Logs, artifacts, run metadata, and diagnostics must redact:

* secret-like strings
* resolved env values
* generated config files containing provider keys

Generated config files that contain secrets must be treated as sensitive runtime files, not durable artifacts by default.

### 12.4 Volume Isolation

OAuth volumes remain dedicated, named volumes with controlled ownership and permissions.

One runtime should not read another runtime's credential state unless explicitly designed to do so.

### 12.5 Clear Competing Variables

Before launch, conflicting variables must be cleared to avoid accidental provider fallback.

Examples:

* Anthropic OAuth profile clears MiniMax / alternative Anthropic-compatible vars where needed.
* MiniMax Claude profile clears `ANTHROPIC_API_KEY`.
* Codex MiniMax profile clears `OPENAI_API_KEY`.

### 12.6 Launch-Only Secret Resolution

Secret refs are resolved immediately before subprocess launch and should exist only in memory for the minimum possible duration.

---

## 13. Backward Compatibility and Migration

### 13.1 Terminology Migration

The older “Auth Profile” language is deprecated in favor of “Provider Profile.”

Expected renames:

* `ManagedAgentAuthProfile` → `ManagedAgentProviderProfile`
* `MoonMind.AuthProfileManager` → `MoonMind.ProviderProfileManager`
* `managed_agent_auth_profiles` → `managed_agent_provider_profiles`

### 13.2 Migration Strategy

Per Constitution Principle XIII (pre-release project), the migration from Auth Profiles to Provider Profiles is **atomic, not phased**:

* Rename the table (`managed_agent_auth_profiles` → `managed_agent_provider_profiles`) and add new columns in a single Alembic migration.
* Update all callers, tests, mocks, and doc references in the same change.
* Do **not** introduce compatibility aliases, translation layers, or dual-read paths. The old names are removed entirely.

### 13.3 Old Profile Mapping

Existing auth profile rows map into the new model as follows:

* OAuth auth profile → `credential_source=oauth_volume`, `runtime_materialization_mode=oauth_home`, `provider_id` inferred from `runtime_id`.
* API key auth profile → `credential_source=secret_ref`, `runtime_materialization_mode=api_key_env`, `provider_id` inferred from `runtime_id` or `api_key_ref` naming.

This migration is lossy only in name, not in behavior.

---

## 14. Evolution Path

The Provider Profile system should be implemented in phases:

### Phase 1 — Rename and Shape Expansion

* Rename Auth Profiles to Provider Profiles in docs and contracts.
* Add `provider_id`, `credential_source`, and `runtime_materialization_mode`.
* Add `clear_env_keys`, `env_template`, `file_templates`, `command_behavior`.

### Phase 2 — Selector-Based Resolution

* Extend `AgentExecutionRequest` to include provider-aware selectors.
* Update the profile manager to route by runtime + provider intent, not runtime alone.

### Phase 3 — Runtime Materialization Engine

* Add shared materialization pipeline for env and file templates.
* Ensure secrets resolve at launch only.
* Layer environment rather than replacing it.

### Phase 4 — Provider-Specific Profiles

* Add first-class profile presets for:

  * Claude + Anthropic OAuth
  * Claude + Anthropic API key
  * Claude + MiniMax
  * Claude + Z.AI
  * Codex + OpenAI
  * Codex + MiniMax

### Phase 5 — UI and Mission Control

* Allow Provider Profile creation, inspection, cooldown visibility, testing, and enable/disable from Mission Control.
* Show which provider profile each run used.

---

## 15. Summary

Provider Profiles replace the narrower Auth Profile model with a provider-aware execution contract.

A Provider Profile answers all of the following for a managed runtime launch:

* which runtime is being launched
* which upstream provider it should talk to
* where credentials come from
* how provider-specific configuration is materialized
* which concurrency and cooldown policy applies
* how compatible profiles are selected when no exact profile is specified

This model is required for MoonMind to support modern runtime/provider combinations such as:

* Claude Code with Anthropic OAuth
* Claude Code with Anthropic API key
* Claude Code with MiniMax
* Claude Code with Z.AI
* Codex CLI with OpenAI
* Codex CLI with MiniMax

The result is a system that is more explicit, more correct, more extensible, and better aligned with how real managed runtimes work in practice.
