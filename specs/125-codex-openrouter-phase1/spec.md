# Feature Specification: Codex CLI OpenRouter Phase 1

**Feature Branch**: `125-codex-openrouter-phase1`  
**Created**: 2026-04-03  
**Status**: Implemented  
**Input**: User description: "Fully implement Phase 1 of docs/ManagedAgents/CodexCliOpenRouter.md"

## Source Document Requirements

- **DOC-REQ-001**: Phase 1 must plumb rich provider-profile fields through adapter to launcher, including provider identity, credential source, materialization mode, env/file templates, home overrides, and command behavior. Source: `docs/ManagedAgents/CodexCliOpenRouter.md` §10, §11.1, §15 Phase 1.
- **DOC-REQ-002**: Phase 1 must add path-aware file materialization so a provider profile can generate a real `CODEX_HOME/config.toml` under a run-scoped support directory. Source: `docs/ManagedAgents/CodexCliOpenRouter.md` §5.2.B, §6.2, §11.2, §15 Phase 1.
- **DOC-REQ-003**: Phase 1 must support `home_path_overrides` for Codex so the generated config bundle is used by the launched process. Source: `docs/ManagedAgents/CodexCliOpenRouter.md` §5.2.C, §6.2, §11.3, §15 Phase 1.
- **DOC-REQ-004**: Phase 1 must seed a default `codex_openrouter_qwen36_plus` provider profile that uses `provider_id=openrouter`, `credential_source=secret_ref`, `runtime_materialization_mode=composite`, `OPENROUTER_API_KEY`, and the generated Codex config. Source: `docs/ManagedAgents/CodexCliOpenRouter.md` §7, §8, §11.5, §15 Phase 1.
- **DOC-REQ-005**: Phase 1 must verify exact-profile launch for the seeded OpenRouter Codex profile through the managed-runtime launch contract. Source: `docs/ManagedAgents/CodexCliOpenRouter.md` §9.1, §14.2, §15 Phase 1.

## Requirements Mapping

- **FR-001**: The managed-runtime launch payload MUST preserve provider-profile launch fields needed for OpenRouter Codex runs. (Maps to DOC-REQ-001)
- **FR-002**: The launch materializer MUST write path-addressed generated files with deterministic permissions and template expansion for runtime support paths. (Maps to DOC-REQ-002)
- **FR-003**: The launch environment MUST expose `CODEX_HOME` from `home_path_overrides` before runtime strategy shaping. (Maps to DOC-REQ-003)
- **FR-004**: Startup auto-seeding MUST add the OpenRouter Codex provider profile when `OPENROUTER_API_KEY` is present, without disturbing existing seeded profiles. (Maps to DOC-REQ-004)
- **FR-005**: The provider-profile activity, adapter, and strategy boundaries MUST carry enough data for an exact-profile OpenRouter Codex launch without ad hoc runtime-specific fallbacks. (Maps to DOC-REQ-001, DOC-REQ-005)

## User Stories & Validation

### User Story 1 - Launch a managed Codex run through an OpenRouter provider profile (P1)

Operators need a normal `codex_cli` managed runtime to launch against OpenRouter using a provider profile instead of a separate runtime.

**Independent Test**: Seed the OpenRouter profile, read it through `provider_profile_list`, pass it through `ManagedAgentAdapter.start()`, and assert the launch payload preserves the OpenRouter materialization contract.

**Acceptance Scenarios**:

1. **Given** an enabled `codex_openrouter_qwen36_plus` profile, **when** a managed run targets that exact profile, **then** the launcher payload includes `provider_id=openrouter`, `runtime_materialization_mode=composite`, `env_template`, `file_templates`, `home_path_overrides`, and `command_behavior`.
2. **Given** the same launch, **when** the materializer renders support files, **then** `config.toml` is written under `{{runtime_support_dir}}/codex-home/` and `CODEX_HOME` points at that directory.

### User Story 2 - Preserve Codex command behavior with provider-driven config defaults (P1)

Codex must omit a redundant default `-m` when the provider profile already defines the default via generated config, but still honor explicit task model overrides.

**Independent Test**: Unit-test `CodexCliStrategy.build_command()` with `command_behavior.suppress_default_model_flag` both with and without an explicit request model.

**Acceptance Scenarios**:

1. **Given** a Codex profile with `suppress_default_model_flag=true`, **when** the request does not override the model, **then** the command omits `-m`.
2. **Given** the same profile, **when** the request explicitly sets a model, **then** the command still includes `-m <override>`.

## Success Criteria

- **SC-001**: Exact-profile OpenRouter Codex launch coverage exists at the provider-profile activity boundary, adapter boundary, materializer boundary, and Codex strategy boundary.
- **SC-002**: The seeded OpenRouter profile persists the generated config contract (`file_templates`, `home_path_overrides`, `command_behavior`) without storing plaintext credentials.
- **SC-003**: Runtime scope validation passes with production runtime file changes and validation tasks recorded in `tasks.md`.
