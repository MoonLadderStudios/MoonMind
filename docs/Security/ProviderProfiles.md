# Provider Profiles

**Implementation tracking:** [`docs/tmp/remaining-work/ManagedAgents-ManagedAgentsAuthentication.md`](../tmp/remaining-work/ManagedAgents-ManagedAgentsAuthentication.md)

Status: **Design Draft**
Owners: MoonMind Engineering
Last Updated: 2026-03-27

> [!NOTE]
> This document replaces the older **Auth Profiles** framing with **Provider Profiles**.
> A provider profile is broader than authentication alone: it defines which provider a runtime targets, how credentials are sourced, how provider-specific configuration is materialized into the runtime environment, which default model should be used for that provider profile, and how concurrency / cooldown policy is enforced.
>
> For tmate session architecture, OAuth session UX, and provider bootstrap behavior, see [TmateArchitecture.md](../ManagedAgents/TmateArchitecture.md). For the shared `TmateSessionManager` abstraction and session lifecycle, see [TmateSessionArchitecture.md](../Temporal/TmateSessionArchitecture.md).

---

## 1. Summary

MoonMind-managed agent runtimes such as Gemini CLI, Claude Code, and Codex CLI do not always map one-to-one to a single upstream company, credential method, or model family.

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

The old **auth profile** concept is too narrow for this reality. It assumes the main question is only:

> Which auth method does this runtime use?

In practice, MoonMind must answer a larger question:

> For this run, which runtime should launch against which provider, using which credential source, materialized in which runtime-specific way, with which default model, and with which concurrency and cooldown policy?

This document defines the **Provider Profile** system that answers that question.

This design makes one boundary explicit:

- **Runtime profiles** define how a runtime is launched in general.
- **Provider profiles** define which provider-specific defaults and configuration are applied to that launch.

That means **default model selection belongs to the provider profile**, not the runtime profile.

This document builds on the execution model defined in [ManagedAndExternalAgentExecutionModel](../Temporal/ManagedAndExternalAgentExecutionModel.md), especially the sections covering managed runtimes, first-class profile selection, and runtime worker topology.

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

7. **Provider-profile-owned model defaults**
   - Example: `claude_code + anthropic` may default to one model while `claude_code + minimax` defaults to another.

8. **No raw credentials in workflow payloads or persistent profile rows**
   - Provider Profiles store references and templates, not secrets.

9. **No duplicated model truth across top-level fields and provider-specific materialization**
   - The semantic default model must be declared once and then materialized by runtime strategy code.

---

## 3. Non-Goals

This design does **not** attempt to:

- Normalize every provider into an identical logical API.
- Store raw access tokens, API keys, or refresh tokens in the profile table.
- Replace runtime-specific strategy code entirely.
- Eliminate the need for per-runtime launch strategies such as Claude, Gemini, or Codex command shaping.
- Solve pricing / billing attribution by itself.
- Require every runtime to expose models in the same way.

Provider Profiles define **selection, defaults, and materialization**, not a universal provider protocol.

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
- default model
- concurrency / cooldown policy
- selection metadata
- runtime-specific launch behavior

into one reusable execution target.

### 4.5 Default Model Ownership

The **default model** is the provider-specific model that MoonMind should use when a run does not specify a model override.

This field belongs to the **Provider Profile**, not the runtime profile.

Why:

- The same runtime may target different providers.
- Different providers expose different model identifiers.
- Different providers may require model materialization in different ways.
- A runtime-global default model is ambiguous once multiple providers exist for that runtime.

Correct examples:

- `claude_code + anthropic` default model: provider-profile field
- `claude_code + minimax` default model: provider-profile field
- `codex_cli + minimax` default model: provider-profile field

Incorrect example:

- one `claude_code` runtime-global default model shared across Anthropic, MiniMax, and Z.AI

### 4.6 Runtime Profile vs Provider Profile

The split of responsibility should be:

**Runtime Profile**
- command template
- workspace mode
- runtime-global timeout defaults
- generic runtime behavior
- generic passthrough behavior
- runtime launch semantics that do not depend on which provider is selected

**Provider Profile**
- provider selection
- credential source
- provider-specific env / file materialization
- model defaults
- provider-specific command behavior
- concurrency and cooldown policy

This boundary avoids duplication and prevents provider concerns from leaking into runtime-global config.

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

  # provider-level defaults
  default_model:                 str | null

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
````

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

#### `default_model`

The provider-profile-owned default model identifier.

This field is the **semantic source of truth** for model defaulting. It is not just a convenience value. Runtime strategies should use it to shape CLI arguments, environment variables, or generated config files.

Rules:

* It is optional.
* It is used only when the run request does not supply a model override.
* It must not be duplicated as a conflicting second truth in runtime-global config.
* It may be materialized into env variables or config files by the runtime strategy.

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

Important rule:

`env_template` is **not** the semantic owner of the default model. If model-related env vars are needed, the runtime strategy should derive them from `default_model` rather than relying on a second manually maintained model string buried inside the template.

#### `file_templates`

Provider-specific config files to generate before launch.

Examples:

* Codex TOML provider stanza
* Runtime-local JSON settings
* Generated config fragments under `.moonmind/`

Important rule:

`file_templates` may contain materialized model values, but the semantic default still comes from `default_model`.

#### `max_lease_duration_seconds`

Maximum time a slot lease is valid. The `ProviderProfileManager` evicts leases exceeding this bound as a safety net for cases where a workflow terminates without explicitly releasing its slot.

#### `priority`

Higher values mean "try this profile first." When multiple profiles match a selector and have available slots, the profile with the highest `priority` value is selected. Ties are broken by most available slots.

Convention:

* `100` = normal default
* `110`–`130` = preferred alternatives
* `50`–`90` = fallback / lower-priority profiles

#### `command_behavior`

Runtime strategy hints that are profile-dependent rather than global.

Examples:

* `suppress_cli_model_flag_when_env_model_present: true`
* `default_codex_profile_name: "m27"`

This field may affect **how** the resolved model is applied, but should not replace `default_model` as the source of truth.

---

## 6. Model Resolution Rules

### 6.1 Resolution Order

The effective model for a run must be resolved in this order:

1. `AgentExecutionRequest.parameters.model`, if explicitly provided
2. `ManagedAgentProviderProfile.default_model`
3. runtime/provider native fallback, if omission is intentionally allowed

This is the required precedence rule.

### 6.2 Why This Order Matters

This gives MoonMind the correct behavior:

* a task step can explicitly ask for a model
* otherwise the provider profile supplies the provider-appropriate default
* otherwise the runtime may fall back to its native behavior, but only intentionally

This keeps the system explicit without forcing every run to specify a model manually.

### 6.3 Materialization Rule

After the effective model is resolved, runtime strategy code is responsible for materializing it correctly.

Examples:

* Claude-compatible providers may need model env vars
* Codex may need a generated profile name or model field in TOML
* Gemini may need a CLI flag or provider-specific config shape

The semantic model decision happens once. Materialization happens later.

### 6.4 Anti-Pattern to Avoid

Do **not** independently hardcode one model in `default_model` and another inside `env_template` or `file_templates`.

That creates configuration drift and makes behavior hard to debug.

---

## 7. Supported Materialization Modes

### 7.1 `oauth_home`

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

### 7.2 `api_key_env`

Use when the provider requires a small number of environment variables, often one key.

Typical behavior:

* Resolve secret refs at launch.
* Inject values into environment.
* Clear competing OAuth / alternative-provider keys when necessary.

### 7.3 `env_bundle`

Use when a provider requires a block of environment variables rather than one API key.

This is the correct model for Anthropic-compatible third-party providers used through Claude Code, such as MiniMax and Z.AI.

### 7.4 `config_bundle`

Use when the runtime expects config files to declare model providers, profiles, or transport details.

This is the correct model for Codex CLI providers declared in config.

### 7.5 `composite`

Use when the runtime requires both generated files and environment injection.

This is the expected model for Codex CLI + MiniMax:

* write provider/profile config
* expose `MINIMAX_API_KEY`

---

## 8. Examples

### 8.1 Gemini CLI + Google OAuth

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
default_model: null
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

### 8.2 Claude Code + Anthropic OAuth

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
default_model: "claude-sonnet-4"
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

### 8.3 Claude Code + Anthropic API Key

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
default_model: "claude-sonnet-4"
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

### 8.4 Claude Code + MiniMax

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
default_model: "MiniMax-M2.7"
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
file_templates: []
home_path_overrides: {}
command_behavior:
  suppress_cli_model_flag_when_env_model_present: true
```

Runtime strategy behavior for this profile should derive model-related Anthropic-compatible variables from `default_model`, rather than storing those model strings as a second manual truth in `env_template`.

### 8.5 Claude Code + Z.AI

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
default_model: "glm-4.6"
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

### 8.6 Codex CLI + OpenAI OAuth

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
default_model: "codex-mini-latest"
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

### 8.7 Codex CLI + MiniMax

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
          model_provider: "minimax"
home_path_overrides: {}
command_behavior:
  default_codex_profile_name: "m27"
```

Runtime strategy behavior for this profile should write the effective model derived from `default_model` or request override into the generated Codex profile content.

---

## 9. Request and Selection Model

### 9.1 Why Runtime-Only Selection Is No Longer Enough

A request that says only:

```json
{ "agentId": "claude_code" }
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
  parameters:
    model: str | null
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
8. Select highest-priority compatible profile.
9. Break ties using the profile with the most free slots.

This behavior is required for correctness.

### 9.4 Default Provider Fallback

When neither `execution_profile_ref` nor `profile_selector.provider_id` is specified, the resolution order above applies across **all** providers for the runtime. This could route a generic `claude_code` request to MiniMax or Z.AI if those profiles happen to have open slots and higher priority.

To prevent unintentional cross-provider routing, one of the following must be true:

1. **Explicit provider in request** — The UI or API caller includes `profile_selector.provider_id`.
2. **Default tag convention** — Only the primary provider's profiles carry the `default` tag, and the request includes `tags_all: ["default"]`.
3. **Priority ordering** — The primary provider's profiles have higher `priority` values than alternative providers, making them win when slots are available.

### 9.5 Model Override vs Profile Default

Profile selection and model selection are separate decisions.

* Provider profile selection chooses the execution target.
* Model resolution chooses the effective model on that target.

This matters because:

* a request may explicitly choose `claude_minimax_m27`
* but still override the model for that one run if the provider/runtime combination supports it

---

## 10. Provider Profile Manager Workflow

### 10.1 Renamed Concept

The older `MoonMind.AuthProfileManager` concept should evolve to **`MoonMind.ProviderProfileManager`**.

The responsibilities remain similar, but the meaning of the managed resource changes:

* before: auth slots
* now: provider-profile slots

### 10.2 Scope

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

### 10.3 Signals

| Signal            | Direction          | Payload                                                                                                                         |
| ----------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `request_slot`    | AgentRun → Manager | `{requester_workflow_id, runtime_id, requested_profile_id?, provider_id?, tags_any?, tags_all?, runtime_materialization_mode?}` |
| `release_slot`    | AgentRun → Manager | `{requester_workflow_id, profile_id}`                                                                                           |
| `report_cooldown` | AgentRun → Manager | `{profile_id, cooldown_seconds}`                                                                                                |
| `sync_profiles`   | System → Manager   | `{profiles: [...]}`                                                                                                             |
| `slot_assigned`   | Manager → AgentRun | `{profile_id}`                                                                                                                  |
| `shutdown`        | System → Manager   | none                                                                                                                            |

### 10.4 Waiting Semantics

If no compatible profile is available, the run waits durably in `awaiting_slot`.

The parent workflow and UI should clearly indicate the reason:

* runtime family
* requested provider, if any
* requested exact profile, if any

Example summary:

> Waiting for provider profile slot on `claude_code` (`provider=minimax`)

### 10.5 Cooldown Behavior

On detected provider 429 or equivalent quota exhaustion:

1. AgentRun signals `report_cooldown(profile_id, duration)`
2. AgentRun releases its current slot
3. AgentRun re-requests a slot using the same selector / exact profile intent

If another compatible profile exists, the run may continue on a different profile. Otherwise, it waits.

---

## 11. Runtime Materialization Pipeline

The launcher must build the final runtime environment in a predictable, layered way.

### 11.1 Required Order

1. Start from a sane base environment.
2. Apply runtime-global defaults.
3. Resolve provider profile selection.
4. Resolve effective model using request override then provider-profile default.
5. Remove or blank `clear_env_keys`.
6. Resolve `secret_refs` into ephemeral launch-only values.
7. Materialize `file_templates`.
8. Apply `env_template`.
9. Apply `home_path_overrides`.
10. Apply runtime strategy shaping.
11. Build command.
12. Launch subprocess.

### 11.2 Critical Rule: Layer, Do Not Replace

Provider Profile materialization must **layer onto** a base environment.

It must **not** replace the subprocess environment wholesale with only the profile env template.

Otherwise, essential variables such as `PATH`, `HOME`, and runtime-specific process context may be lost.

### 11.3 Runtime Strategy Integration

Provider Profiles do not eliminate runtime strategies. Instead:

* Provider Profiles define the data needed to prepare the environment and files.
* Runtime strategies interpret `command_behavior` and runtime-specific launch rules.
* Runtime strategies materialize the already-resolved effective model.

Examples:

* Claude strategy may suppress `--model` when model env variables are present.
* Codex strategy may select a generated named profile and write the effective model into it.
* Gemini strategy may pass the effective model via CLI or config.
* OAuth-mode strategies may clear conflicting API-key variables.

### 11.4 Semantic vs Materialized Model

MoonMind must distinguish between:

* **semantic model**: the resolved effective model for the run
* **materialized model**: the way that model is expressed to the runtime

This distinction is required for correctness, observability, and future UI clarity.

---

## 12. Persistence Model

### 12.1 Table

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

    default_model                     TEXT,

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

### 12.2 Secrets

Secrets are never stored directly in:

* `secret_refs`
* `env_template`
* `file_templates`

Any sensitive value must be represented indirectly by a secret reference resolved at launch time.

### 12.3 Runtime Profile Cleanup

`default_model` must be removed from runtime-profile persistence and runtime-profile contracts.

Runtime profiles should not own provider-specific model defaults.

---

## 13. Security Requirements

### 13.1 No Raw Secrets in Workflow Payloads

Workflows must reference only:

* `profile_id`
* optional provider / tag selectors

They must never carry:

* API keys
* refresh tokens
* OAuth access tokens
* config blobs containing raw secrets

### 13.2 No Raw Secrets in Profile Rows

Provider Profiles store:

* secret references
* file templates
* env templates
* volume references

They do not store secret values.

### 13.3 Redaction

Logs, artifacts, run metadata, and diagnostics must redact:

* secret-like strings
* resolved env values
* generated config files containing provider keys

Generated config files that contain secrets must be treated as sensitive runtime files, not durable artifacts by default.

### 13.4 Volume Isolation

OAuth volumes remain dedicated, named volumes with controlled ownership and permissions.

One runtime should not read another runtime's credential state unless explicitly designed to do so.

### 13.5 Clear Competing Variables

Before launch, conflicting variables must be cleared to avoid accidental provider fallback.

Examples:

* Anthropic OAuth profile clears MiniMax / alternative Anthropic-compatible vars where needed.
* MiniMax Claude profile clears `ANTHROPIC_API_KEY`.
* Codex MiniMax profile clears `OPENAI_API_KEY`.

### 13.6 Launch-Only Secret Resolution

Secret refs are resolved immediately before subprocess launch and should exist only in memory for the minimum possible duration.

---

## 14. Backward Compatibility and Migration

### 14.1 Terminology Migration

The older “Auth Profile” language is deprecated in favor of “Provider Profile.”

Expected renames:

* `ManagedAgentAuthProfile` → `ManagedAgentProviderProfile`
* `MoonMind.AuthProfileManager` → `MoonMind.ProviderProfileManager`
* `managed_agent_auth_profiles` → `managed_agent_provider_profiles`

### 14.2 Default Model Migration

The old location for default model configuration on `ManagedRuntimeProfile` is deprecated.

The new rule is:

* `ManagedRuntimeProfile.default_model` is removed
* `ManagedAgentProviderProfile.default_model` is added
* all effective model resolution must read from request override then provider profile

### 14.3 Migration Strategy

Per Constitution Principle XIII (pre-release project), the migration from Auth Profiles to Provider Profiles is **atomic, not phased**:

* Rename the table (`managed_agent_auth_profiles` → `managed_agent_provider_profiles`) and add new columns in a single Alembic migration.
* Add `default_model` to `managed_agent_provider_profiles`.
* Remove `default_model` from runtime-profile contracts and storage in the same change.
* Update all callers, tests, mocks, and doc references in the same change.
* Do **not** introduce compatibility aliases, translation layers, or dual-read paths. The old names are removed entirely.

### 14.4 Old Profile Mapping

Existing auth profile rows map into the new model as follows:

* OAuth auth profile → `credential_source=oauth_volume`, `runtime_materialization_mode=oauth_home`, `provider_id` inferred from `runtime_id`
* API key auth profile → `credential_source=secret_ref`, `runtime_materialization_mode=api_key_env`, `provider_id` inferred from `runtime_id` or secret naming

Default model migration rules:

* If a runtime has exactly one provider profile, the old runtime default model may be moved directly into that provider profile.
* If a runtime has multiple provider profiles, the migration must not blindly copy one runtime-global model into all provider profiles.
* Any ambiguous case must be left null and filled intentionally by the operator or migration logic that can infer the correct provider-specific model.

This migration is lossy only in config shape, not in intended behavior.

---

## 15. UI and API Implications

### 15.1 Mission Control

Mission Control should allow Provider Profile creation, inspection, cooldown visibility, testing, enable/disable, and default model editing.

The Provider Profile editor should include:

* runtime
* provider
* credential source
* materialization mode
* default model
* concurrency / cooldown settings
* selection tags
* profile-specific launch behavior

### 15.2 Runtime Profile UI

Runtime Profile UI should **not** include a default model field.

Runtime Profile editing should remain focused on runtime-global concerns.

### 15.3 Run Details

Run details should expose:

* requested model override, if any
* provider profile used
* effective model resolved
* runtime/provider materialization notes where useful

This will make operator debugging significantly easier.

### 15.4 API Surface

Any API that returns provider profile details should include `default_model`.

Any API that returns runtime profile details should not include `default_model`.

---

## 16. Evolution Path

The Provider Profile system should be implemented in phases:

### Phase 1 — Rename and Shape Expansion

* Rename Auth Profiles to Provider Profiles in docs and contracts.
* Add `provider_id`, `credential_source`, and `runtime_materialization_mode`.
* Add `default_model`.
* Add `clear_env_keys`, `env_template`, `file_templates`, `command_behavior`.

### Phase 2 — Selector-Based Resolution

* Extend `AgentExecutionRequest` to include provider-aware selectors.
* Update the profile manager to route by runtime + provider intent, not runtime alone.

### Phase 3 — Model Resolution Unification

* Remove runtime-profile-owned default model behavior.
* Resolve model as:

  * request override
  * provider-profile default
  * runtime/provider native fallback
* Make runtime strategies responsible for materializing the resolved model.

### Phase 4 — Runtime Materialization Engine

* Add shared materialization pipeline for env and file templates.
* Ensure secrets resolve at launch only.
* Layer environment rather than replacing it.
* Ensure model materialization uses one semantic source of truth.

### Phase 5 — Provider-Specific Profiles

* Add first-class profile presets for:

  * Claude + Anthropic OAuth
  * Claude + Anthropic API key
  * Claude + MiniMax
  * Claude + Z.AI
  * Codex + OpenAI
  * Codex + MiniMax
  * Gemini + Google OAuth

### Phase 6 — UI and Mission Control

* Allow Provider Profile creation, inspection, cooldown visibility, testing, and enable/disable from Mission Control.
* Show which provider profile each run used.
* Show the effective model for each run.

---

## 17. Summary

Provider Profiles replace the narrower Auth Profile model with a provider-aware execution contract.

A Provider Profile answers all of the following for a managed runtime launch:

* which runtime is being launched
* which upstream provider it should talk to
* where credentials come from
* how provider-specific configuration is materialized
* which default model should be used when the run does not override one
* which concurrency and cooldown policy applies
* how compatible profiles are selected when no exact profile is specified

This model is required for MoonMind to support modern runtime/provider combinations such as:

* Claude Code with Anthropic OAuth
* Claude Code with Anthropic API key
* Claude Code with MiniMax
* Claude Code with Z.AI
* Codex CLI with OpenAI
* Codex CLI with MiniMax
* Gemini CLI with Google OAuth

The key architectural rule is simple:

> Runtime profiles define how to launch a runtime.
> Provider profiles define which provider-specific defaults and configuration that launch should use.

With that rule in place, model ownership becomes unambiguous, provider-specific behavior becomes easier to reason about, and MoonMind's managed runtime system becomes more correct, more explicit, and more extensible.
