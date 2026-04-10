# Codex CLI via OpenRouter

**Related design documents:** [../Security/ProviderProfiles.md](../Security/ProviderProfiles.md), [SharedManagedAgentAbstractions.md](./SharedManagedAgentAbstractions.md), [../Temporal/ManagedAndExternalAgentExecutionModel.md](../Temporal/ManagedAndExternalAgentExecutionModel.md)

Status: **Proposed Design**  
Owners: MoonMind Engineering  
Last Updated: 2026-04-03

---

## 1. Summary

This document describes how MoonMind should support **Codex CLI** against **OpenRouter** using the existing **Provider Profiles** and **managed agents** architecture, with **`qwen/qwen3.6-plus`** as the example default model.

The key design choice is:

> Treat OpenRouter as a **provider** for the existing `codex_cli` runtime, not as a new runtime.

That means MoonMind should launch the existing managed Codex runtime with a provider profile that owns:

- provider selection (`openrouter`)
- credential source (`secret_ref`)
- runtime materialization (`composite`)
- generated Codex configuration
- launch-time environment injection
- concurrency and cooldown policy

The end-state UX is:

- Mission Control can target `codex_cli` + `openrouter` by exact `execution_profile_ref` or `profile_selector.provider_id`.
- Workers do **not** require a pre-existing user-global `~/.codex/config.toml`.
- The OpenRouter API key is resolved **only at launch time**.
- Codex gets a per-run, provider-specific config bundle that selects OpenRouter and defaults to Qwen 3.6 Plus.
- Existing OpenAI-backed Codex profiles continue to work unchanged.

---

## 2. Goals

1. Add **OpenRouter** as a first-class provider target for the existing `codex_cli` managed runtime.
2. Make the integration fit MoonMind’s **Provider Profile** model rather than introducing ad hoc env-only behavior.
3. Keep OpenRouter credentials out of workflow payloads, artifacts, and repo state.
4. Support **provider-aware routing**, **slot leasing**, and **cooldown** at the provider-profile level.
5. Use **Qwen 3.6 Plus** as the initial example/default model, while allowing other OpenRouter model strings later.
6. Keep the design generic enough that future OpenRouter-backed Codex profiles differ mostly by profile data, not custom code.

---

## 3. Non-Goals

This design does **not** attempt to:

- create a new managed runtime ID such as `codex_openrouter`
- redesign MoonMind’s secret storage system
- require project-local `.codex/config.toml` checked into repos
- solve every Codex configuration feature in one pass
- add OpenRouter-specific traffic proxying in v1
- unify or replace the standalone `codex_worker` package

---

## 4. Why this belongs in Provider Profiles

OpenRouter is not merely “another API key.” It changes:

- the upstream provider identity
- the base URL and wire target used by Codex
- the runtime materialization shape (generated Codex config + env)
- the default model namespace (`qwen/...` on OpenRouter)
- the concurrency/cooldown surface MoonMind should track

That matches the Provider Profiles model exactly.

For MoonMind, the durable unit should be:

> `codex_cli` + `openrouter` + credential binding + materialization bundle + policy

not “Codex with some random environment variables.”

---

## 5. Current MoonMind state

MoonMind already has most of the pieces needed for this integration, but they do not yet line up end-to-end for Codex + OpenRouter.

### 5.1 What already exists

- `codex_cli` is already a supported managed runtime.
- Managed runtime launching already routes through the shared strategy system.
- Provider-profile-aware request contracts already exist (`execution_profile_ref`, `profile_selector.provider_id`).
- The launcher already resolves `secret_refs` just-in-time and runs a materialization pipeline before spawn.
- The Codex strategy already owns Codex-specific command construction and Codex-home env passthrough.

### 5.2 What is still missing or incomplete

#### A. Adapter field plumbing is still legacy-shaped

`ManagedAgentAdapter.start()` currently maps only a **subset** of provider-profile data into `ManagedRuntimeProfile`.

Today it carries through fields like:

- `default_model`
- `model_overrides`
- `secret_refs`
- `clear_env_keys`
- `command_template`
- `env_overrides`

But it does **not** pass through the richer provider-profile fields that OpenRouter-on-Codex needs:

- `credential_source`
- `runtime_materialization_mode`
- `command_behavior`
- `env_template`
- `file_templates`
- `home_path_overrides`

This is the largest functional gap.

#### B. Materializer file support is too shallow for Codex config bundles

`ProviderProfileMaterializer` already supports secret resolution, env template expansion, and generated files, but its current `file_templates` shape is effectively:

```python
Dict[ENV_VAR_NAME, TEMPLATE_STRING]
```

That is fine for “write a temp file and expose the path as an env var,” but it is not sufficient for the desired-state Provider Profiles contract, which expects path-aware file materialization such as:

- write `<support_root>/codex-home/config.toml`
- set `CODEX_HOME` to that home directory
- optionally merge/replace structured config content

Codex is a config-driven runtime, so path-aware materialization is the right primitive.

#### C. Launcher does not yet apply `home_path_overrides`

The provider-profile design expects runtime homes such as `CODEX_HOME` to be applied as part of materialization. The current launcher does not yet apply `home_path_overrides`.

#### D. Codex strategy assumes model selection is only `-m`

`CodexCliStrategy.build_command()` currently always appends `-m <model>` when a model is present.

That works for OpenAI-backed runs, but OpenRouter support needs Codex to know both:

- **which provider** to use (`openrouter`)
- **which model** on that provider to use (`qwen/qwen3.6-plus`)

The provider choice belongs in Codex config, not just in a CLI model string.

---

## 6. Proposed architecture

### 6.1 High-level model

OpenRouter support should be implemented as a **provider profile** on top of the existing `codex_cli` runtime.

Recommended provider profile identity:

- `runtime_id = codex_cli`
- `provider_id = openrouter`
- `credential_source = secret_ref`
- `runtime_materialization_mode = composite`

`composite` is the correct mode because the runtime needs **both**:

1. an environment variable for the provider API key
2. a generated Codex config that points the runtime at OpenRouter

### 6.2 Materialization strategy

Each run gets a private, per-run Codex home and config bundle.

Recommended support layout:

```text
<run_root>/
  workspace/
  .moonmind/
    codex-home/
      config.toml
```

MoonMind should:

1. create a per-run support directory under `.moonmind/`
2. create a dedicated `codex-home/`
3. render `config.toml` into that directory
4. set `CODEX_HOME` to that directory
5. inject `OPENROUTER_API_KEY` into the subprocess env
6. launch `codex exec ...`

### 6.3 Why per-run Codex home instead of project config or user-global config

This avoids three classes of problems:

- **cross-run bleed:** one run’s provider settings should not affect another run
- **worker-global state coupling:** workers should not require pre-baked user config
- **repo mutation/trust issues:** MoonMind should not need to edit project `.codex/config.toml`

This keeps provider shaping in runtime support state, where it belongs.

---

## 7. Provider profile shape

The target provider-profile record for the example OpenRouter + Qwen profile should look like this.

```yaml
profile_id: codex_openrouter_qwen36_plus
runtime_id: codex_cli
provider_id: openrouter
provider_label: "OpenRouter"
credential_source: secret_ref
runtime_materialization_mode: composite
account_label: "team-default"
enabled: true
tags: ["openrouter", "qwen", "codex", "openai-compatible"]
priority: 100

default_model: qwen/qwen3.6-plus
model_overrides: {}

max_parallel_runs: 4
cooldown_after_429_seconds: 300
rate_limit_policy: backoff
max_lease_duration_seconds: 7200

volume_ref: null
volume_mount_path: null

secret_refs:
  provider_api_key:
    secret_id: sec_openrouter_team_default
    backend_type: db_encrypted

clear_env_keys:
  - OPENAI_API_KEY
  - OPENAI_BASE_URL
  - OPENAI_ORG_ID
  - OPENAI_PROJECT
  - OPENROUTER_API_KEY

env_template:
  OPENROUTER_API_KEY:
    from_secret_ref: provider_api_key

file_templates:
  - path: "{{runtime_support_dir}}/codex-home/config.toml"
    format: toml
    merge_strategy: replace
    content_template:
      model_provider: openrouter
      model_reasoning_effort: high
      model: qwen/qwen3.6-plus
      profile: openrouter_qwen36_plus
      model_providers:
        openrouter:
          name: OpenRouter
          base_url: https://openrouter.ai/api/v1
          env_key: OPENROUTER_API_KEY
          wire_api: responses
      profiles:
        openrouter_qwen36_plus:
          model_provider: openrouter
          model: qwen/qwen3.6-plus

home_path_overrides:
  CODEX_HOME: "{{runtime_support_dir}}/codex-home"

command_behavior:
  suppress_default_model_flag: true
```

### Notes on the example

- `provider_id` is **`openrouter`**, not `openai`, because MoonMind should track routing, cooldown, and UI choice against the actual upstream surface.
- `default_model` stays on the provider profile because it expresses the default intent for this provider/runtime pairing.
- `env_template.OPENROUTER_API_KEY` should be created from the resolved secret role rather than persisted directly.
- `file_templates` write a **real Codex config file** into the per-run Codex home.
- `home_path_overrides.CODEX_HOME` points Codex at the generated config bundle.
- `command_behavior.suppress_default_model_flag` is recommended so the strategy does not redundantly append `-m qwen/qwen3.6-plus` when the generated Codex profile already sets the default.

---

## 8. Generated Codex config

The rendered config file should be equivalent to:

```toml
model_provider = "openrouter"
model_reasoning_effort = "high"
model = "qwen/qwen3.6-plus"
profile = "openrouter_qwen36_plus"

[model_providers.openrouter]
name = "OpenRouter"
base_url = "https://openrouter.ai/api/v1"
env_key = "OPENROUTER_API_KEY"
wire_api = "responses"

[profiles.openrouter_qwen36_plus]
model_provider = "openrouter"
model = "qwen/qwen3.6-plus"
```

### Why include both top-level `model_provider` and a named profile?

This gives MoonMind two useful behaviors:

- a stable runtime default profile for normal runs
- a clean place to hang future profile-specific overrides without mutating the global top-level shape

For managed sessions specifically, the generated config should also carry a top-level
`model` (and `model_reasoning_effort` when desired) instead of relying only on the
named profile block. MoonMind launches Codex through the app-server/session plane,
which behaves like the VS Code extension path, and upstream Codex has had bugs in
that path where custom-provider sessions respect the provider but ignore model
selection stored only under `[profiles.<name>]`.

In the first implementation, MoonMind can rely on the generated config’s default profile rather than requiring a special Codex CLI flag.

---

## 9. Selection model

MoonMind should support both exact and dynamic selection.

### 9.1 Exact selection

Use the profile directly:

```json
{
  "agentKind": "managed",
  "agentId": "codex_cli",
  "executionProfileRef": "codex_openrouter_qwen36_plus",
  "correlationId": "...",
  "idempotencyKey": "...",
  "instructionRef": "artifact://instructions/task.md"
}
```

### 9.2 Dynamic selection

Use provider-aware routing:

```json
{
  "agentKind": "managed",
  "agentId": "codex_cli",
  "correlationId": "...",
  "idempotencyKey": "...",
  "instructionRef": "artifact://instructions/task.md",
  "profileSelector": {
    "providerId": "openrouter"
  }
}
```

The provider-profile manager should then choose the highest-priority enabled `codex_cli` + `openrouter` profile that matches policy.

---

## 10. Launch flow

The intended launch flow is:

1. `ManagedAgentAdapter.start()` resolves the selected provider profile.
2. The adapter maps the **full provider-profile launch contract** into `ManagedRuntimeProfile`.
3. `ManagedRuntimeLauncher.launch()` resolves `secret_refs` at launch time.
4. The materializer creates the per-run support directory and renders `config.toml`.
5. The launcher applies `home_path_overrides`, `env_template`, and `clear_env_keys`.
6. The Codex strategy shapes the final environment.
7. The Codex strategy builds the final command.
8. MoonMind launches `codex exec` in the workspace.
9. On completion, generated files are cleaned up.

The important invariant is:

> All provider-specific state is materialized at launch from the provider profile; none of it needs to live in workflow state or repo config.

---

## 11. Required implementation changes

### 11.1 Schema and contract alignment

Update the managed-runtime contract plumbing so the richer provider-profile fields actually flow to launch.

#### Files

- `moonmind/schemas/agent_runtime_models.py`
- `moonmind/workflows/adapters/managed_agent_adapter.py`

#### Changes

1. Continue using `ManagedRuntimeProfile` as the launch-time payload shape, but ensure the adapter populates:
   - `provider_id`
   - `provider_label`
   - `credential_source`
   - `runtime_materialization_mode`
   - `command_behavior`
   - `env_template`
   - `file_templates`
   - `home_path_overrides`
   - `clear_env_keys`
   - `secret_refs`
   - `default_model`
   - `model_overrides`

2. If the persisted provider-profile row is still legacy-shaped in some environments, add the missing fields and migration path needed to reach the Provider Profiles contract in `docs/Security/ProviderProfiles.md`.

### 11.2 Path-aware file materialization

Upgrade `ProviderProfileMaterializer` from “anonymous temp files keyed by env var” to a path-aware materializer suitable for runtime config bundles.

#### Files

- `moonmind/workflows/adapters/materializer.py`

#### Changes

1. Support `RuntimeFileTemplate[]` semantics:
   - `path`
   - `format`
   - `merge_strategy`
   - `content_template`
   - optional `permissions`

2. Add launch-time template variables for runtime support paths, at minimum:
   - `runtime_support_dir`
   - `workspace_path`

3. Render structured TOML content for Codex config.

4. Keep cleanup behavior for generated files.

### 11.3 Home-path application in the launcher

Apply `home_path_overrides` before runtime strategy shaping.

#### Files

- `moonmind/workflows/temporal/runtime/launcher.py`

#### Changes

1. Add a launcher step that applies `profile.home_path_overrides` into the subprocess env.
2. Ensure this happens after secret resolution/materialization and before `strategy.shape_environment()`.
3. Keep the existing rule that profile shaping **layers onto** the base env rather than replacing it.

### 11.4 Codex strategy behavior

Teach the Codex strategy to cooperate with provider-profile-driven config.

#### Files

- `moonmind/workflows/temporal/runtime/strategies/codex_cli.py`

#### Changes

1. Honor `command_behavior.suppress_default_model_flag`.
2. If a request explicitly overrides the model, continue to append `-m <override>`.
3. Otherwise, when the generated config already supplies the default profile/model, omit `-m`.
4. Preserve the existing sandbox/approval flag sanitization.

### 11.5 Seeding and management

Add a convenient bootstrap path for local/dev deployments.

#### Changes

1. If `OPENROUTER_API_KEY` is present at service startup, auto-seed a default provider profile:
   - `profile_id = codex_openrouter_qwen36_plus`
   - `provider_id = openrouter`
   - `default_model = qwen/qwen3.6-plus`
   - `secret_refs.provider_api_key = env://OPENROUTER_API_KEY`

2. Also support creating/editing the profile through Mission Control / REST so production deployments can use `db_encrypted`, `vault://...`, or other secret backends.

3. Keep the auto-seeded profile separate from any OpenAI-backed Codex profile.

---

## 12. Minimal viable implementation vs target design

### 12.1 Minimal viable implementation

The smallest useful implementation is:

1. plumb `env_template`, `file_templates`, `home_path_overrides`, and `command_behavior` through the adapter
2. support path-aware file rendering to `CODEX_HOME/config.toml`
3. inject `OPENROUTER_API_KEY`
4. set `CODEX_HOME`
5. generate the OpenRouter provider block and default profile
6. let Codex run unchanged otherwise

This already produces a working managed OpenRouter profile for Codex.

### 12.2 Target design

The fuller target state is:

- provider profiles persisted in the richer schema from `docs/Security/ProviderProfiles.md`
- first-class Mission Control UI for `runtime_id + provider_id + secret source + model`
- generic path-aware file materialization reusable by other config-bundle runtimes
- uniform provider-aware selection and cooldown behavior across all runtimes

---

## 13. Security considerations

### 13.1 Secret handling

- The OpenRouter key must be stored only as a `SecretRef` (or env-backed ref in local/dev).
- The resolved plaintext key may exist only in the launch-time subprocess env.
- No raw key values in workflow payloads, run metadata, or artifacts.

### 13.2 Config file handling

- Generated Codex config should live in a MoonMind-owned per-run support directory.
- Generated files must be mode `0600` where applicable.
- Cleanup should remove generated config on run completion/best-effort cleanup.

### 13.3 Env clearing

Before materializing the OpenRouter profile, MoonMind should clear competing ambient values that could silently change routing:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_ORG_ID`
- `OPENAI_PROJECT`
- `OPENROUTER_API_KEY`

Then re-inject only the values resolved from the selected provider profile.

### 13.4 Repo isolation

MoonMind should not write `.codex/config.toml` into the checked-out repo for this feature. Provider routing is launch state, not repository state.

---

## 14. Testing plan

### 14.1 Unit tests

Add tests for:

1. **Adapter mapping**
   - provider profile -> managed runtime profile includes `env_template`, `file_templates`, `home_path_overrides`, and `command_behavior`

2. **Materializer**
   - path-aware file rendering
   - TOML rendering
   - secret interpolation
   - cleanup of generated files

3. **Codex strategy**
   - default profile launch omits `-m` when `suppress_default_model_flag=true`
   - explicit request model override still appends `-m`

4. **Launcher**
   - `home_path_overrides` applied before strategy shaping
   - env layering still preserves baseline `PATH`, `HOME`, etc.

### 14.2 Integration tests

1. Launch `codex_cli` with a stub OpenAI-compatible endpoint and verify that:
   - the generated config selects `openrouter`
   - the process gets `OPENROUTER_API_KEY`
   - the request uses the configured base URL

2. Verify dynamic selection with:

```json
{"profileSelector": {"providerId": "openrouter"}}
```

3. Verify cooldown and slot leasing attach to the selected provider profile rather than all Codex runs globally.

### 14.3 Optional live smoke test

When credentials are present in a non-CI environment, run a smoke test against the seeded profile using a simple repository task and assert successful completion.

---

## 15. Rollout plan

### Phase 1 — plumbing and MVP

- plumb rich provider-profile fields through adapter -> launcher
- add path-aware file materialization
- add `CODEX_HOME` support via `home_path_overrides`
- add `codex_openrouter_qwen36_plus` seed path
- verify exact-profile launch works

### Phase 2 — dynamic routing and polish

- enable Mission Control creation/editing for OpenRouter Codex profiles
- verify `profile_selector.provider_id = openrouter`
- add strategy support for suppressing redundant default `-m`
- add integration coverage for cooldown and slot behavior

### Phase 3 — generalization

- treat OpenRouter as the reference implementation for config-bundle Codex providers
- reuse the same pattern for additional OpenRouter-backed model defaults
- align any remaining legacy auth-profile persistence with the provider-profile contract

---

## 16. Open questions

### 16.1 Should OpenRouter be modeled as `provider_id=openrouter` or a generic `openai_compatible` provider?

Recommendation: use **`openrouter`**.

Reason: MoonMind needs provider-aware routing, cooldowns, auditability, and UI semantics tied to the actual upstream surface.

### 16.2 Should the example profile hardcode `qwen/qwen3.6-plus`?

Recommendation: yes for the starter profile.

Reason: it provides a zero-cost/default example and keeps the initial UX simple. Additional profiles can later target preview or paid variants without changing the core integration.

### 16.3 Do we need OpenRouter-specific optional headers in v1?

Recommendation: no.

Start with the required base URL + bearer key path. Optional attribution headers can be added later if MoonMind wants them.

---

## 17. Recommendation

Implement OpenRouter for Codex CLI as a **provider profile on the existing `codex_cli` runtime**, backed by:

- `provider_id = openrouter`
- `credential_source = secret_ref`
- `runtime_materialization_mode = composite`
- a generated per-run `CODEX_HOME/config.toml`
- launch-time `OPENROUTER_API_KEY` injection
- a default example profile using `qwen/qwen3.6-plus`

This gives MoonMind a clean, provider-aware, reusable path for OpenRouter-backed Codex runs while staying aligned with the current Provider Profiles architecture.
