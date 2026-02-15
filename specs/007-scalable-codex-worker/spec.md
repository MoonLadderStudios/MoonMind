# Feature Specification: Scalable Codex Worker (015-Aligned)

**Feature Branch**: `007-scalable-codex-worker`  
**Created**: 2025-11-27  
**Status**: Draft (Updated 2026-02-14 for 015 umbrella alignment)  
**Input**: User description: "Update the 007 spec and implementation to align with the new 015 umbrella spec."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fast Worker Launch with Persistent Codex Auth + Gemini Embedding Readiness (Priority: P1)

As an operator, I want one deterministic startup path for Codex and Gemini workers so Codex auth is persistent and embedding defaults are validated before work is accepted.

**Why this priority**: Startup readiness is a hard dependency for all workflow execution.

**Independent Test**: Authenticate the shared Codex volume once, start `rabbitmq`, `api`, `celery_codex_worker`, and `celery_gemini_worker`, and verify startup logs show queue bindings plus preflight readiness.

**Acceptance Scenarios**:

1. **Given** Codex auth is stored in `codex_auth_volume`, **When** `celery_codex_worker` starts, **Then** Codex preflight passes with no interactive prompt.
2. **Given** both workers start, **When** logs are inspected, **Then** each worker confirms Speckit CLI availability and queue configuration.
3. **Given** `DEFAULT_EMBEDDING_PROVIDER=google`, **When** required embedding credentials are missing, **Then** worker startup fails fast with actionable diagnostics.

---

### User Story 2 - Skills-First Routing with Speckit Always Available (Priority: P1)

As a platform engineer, I want workflow stages to resolve through skills-first policy while preserving Speckit as default and always present on workers.

**Why this priority**: 015 umbrella semantics require skills-based orchestration without losing existing Speckit behavior.

**Independent Test**: Run workflow stages with default skill settings and explicit overrides, then verify task payload metadata records selected skill and execution path.

**Acceptance Scenarios**:

1. **Given** default settings, **When** workflow stages execute, **Then** they record `selectedSkill=speckit` and `executionPath=skill`.
2. **Given** a stage override skill that is allowlisted, **When** stage execution starts, **Then** selection logic uses the override.
3. **Given** skill execution fallback is enabled and adapter execution fails, **When** the stage recovers through direct logic, **Then** metadata records `executionPath=direct_fallback`.

---

### User Story 3 - Scaled Worker Group with Backward-Compatible Queues (Priority: P2)

As a release owner, I want Codex workers to scale while preserving existing queue compatibility (`speckit`, `codex`, `gemini`) during rollout.

**Why this priority**: Capacity changes cannot break existing routing behavior.

**Independent Test**: Scale `celery_codex_worker` replicas and verify runs keep executing while queue topology and API behavior remain backward compatible.

**Acceptance Scenarios**:

1. **Given** `celery_codex_worker` is scaled, **When** tasks are queued, **Then** workers continue consuming the configured `speckit` and `codex` queues.
2. **Given** Codex preflight fails on a run, **When** submission stage executes, **Then** the run fails quickly with persisted remediation context.
3. **Given** existing API consumers for `/api/workflows/speckit`, **When** skills metadata is emitted in task payloads, **Then** previous response fields remain available.

### Edge Cases

- Codex auth volume is missing or mounted at the wrong path.
- `DEFAULT_EMBEDDING_PROVIDER=google` but neither `GOOGLE_API_KEY` nor `GEMINI_API_KEY` is set.
- Speckit CLI is missing in one worker image.
- Skill override requests an identifier outside `SPEC_WORKFLOW_ALLOWED_SKILLS`.
- Multiple codex workers read from shared queue bindings while a run is retried from failed stage state.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST run a dedicated `celery_codex_worker` service that consumes the configured Codex queue and remains compatible with the shared `speckit` queue topology.
- **FR-002**: The system MUST persist Codex authentication in a named Docker volume (`CODEX_VOLUME_NAME`) mounted at `CODEX_VOLUME_PATH`.
- **FR-003**: Worker startup MUST enforce non-interactive Codex policy (`approval_policy = "never"`) and fail fast when policy enforcement fails.
- **FR-004**: Worker startup MUST verify Speckit CLI capability for Codex and Gemini worker entrypoints.
- **FR-005**: When `DEFAULT_EMBEDDING_PROVIDER=google`, worker startup MUST fail fast if neither `GOOGLE_API_KEY` nor `GEMINI_API_KEY` is available.
- **FR-006**: Workflow stage routing MUST remain skills-first with Speckit default selection and allowlisted overrides.
- **FR-007**: Workflow task payloads MUST include stage execution metadata (`selectedSkill`, `executionPath`, `usedSkills`, `usedFallback`, `shadowModeRequested`).
- **FR-008**: Codex preflight failures MUST persist failure metadata and transition runs to failed state without hanging.
- **FR-009**: Compose/runtime docs MUST describe the fastest path for authenticated Codex workers plus Gemini embedding defaults.
- **FR-010**: Implementation deliverables MUST include runtime code changes and validation tests; docs-only updates are insufficient.

### Key Entities *(include if feature involves data)*

- **CodexWorkerStartupProfile**: Captures queue bindings, Codex preflight result, Speckit capability status, and embedding readiness diagnostics.
- **CodexAuthVolumeBinding**: Defines volume name/path contract for persisted Codex credentials across worker restarts.
- **StageExecutionDecision**: Captures selected skill, execution path, fallback policy, and shadow mode for a workflow stage.
- **WorkflowTaskStatePayload**: Serialized task payload including status and skills execution metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Operators can authenticate once and restart `celery_codex_worker` without interactive Codex login prompts.
- **SC-002**: Worker startup logs always include Codex/Gemini CLI checks, Speckit capability checks, and queue binding diagnostics.
- **SC-003**: When Google embeddings are configured without credentials, worker startup fails with actionable error text.
- **SC-004**: Workflow runs include skill selection/execution-path metadata in task payloads for discover, submit, and publish stages.
- **SC-005**: Validation command `./tools/test_unit.sh` passes for finalized implementation.

## Assumptions

- Existing `/api/workflows/speckit` API paths remain stable during umbrella migration.
- Speckit remains installed in worker images and mirrored in skill directories.
- RabbitMQ remains the Celery broker for workflow tasks in this deployment profile.
