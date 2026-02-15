# Feature Specification: Skills-First Workflow Umbrella

**Feature Branch**: `015-skills-workflow`  
**Created**: 2026-02-14  
**Status**: Draft  
**Input**: User description: "Move workers to always-on Speckit capability and skills-based workflow orchestration (including non-Speckit skills), with the fastest operator path for authenticated Codex workers and Gemini embeddings for vectors."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fast Worker Launch with Authenticated Codex + Gemini Embeddings (Priority: P1)

As an operator, I want one clear startup path that brings up Celery workers with persistent Codex authentication and Google Gemini embeddings configured for vector workflows.

**Why this priority**: If startup and auth are not deterministic, all higher-level workflow changes are blocked.

**Independent Test**: From a fresh `.env`, complete one-time Codex volume auth, start the documented services, and verify workers start without interactive prompts while embedding defaults resolve to Google Gemini.

**Acceptance Scenarios**:

1. **Given** `GOOGLE_API_KEY`, `DEFAULT_EMBEDDING_PROVIDER=google`, and `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`, **When** the stack starts, **Then** embedding configuration resolves to Google Gemini defaults.
2. **Given** Codex auth is completed once on the named volume, **When** `celery_codex_worker` restarts, **Then** Codex preflight succeeds without manual login.
3. **Given** the documented quickstart command is run, **When** logs are inspected, **Then** the Codex worker reports queue bindings and a successful preflight before processing jobs.

---

### User Story 2 - Skills-First Execution with Speckit Always Available (Priority: P1)

As a platform engineer, I want workflow stages to execute through a skills abstraction first, while keeping Speckit installed and available on every worker by default.

**Why this priority**: This removes hard-coded "Speckit workflow" semantics while preserving existing behavior as the default path.

**Independent Test**: Run representative workflow stages with default policy and explicit skill overrides, verify skill execution path is used first, and confirm direct fallback works when a selected skill fails.

**Acceptance Scenarios**:

1. **Given** worker startup, **When** capability checks run, **Then** Speckit skills are verified as available regardless of selected workflow policy.
2. **Given** a workflow stage request with no override, **When** execution begins, **Then** the stage resolves to the configured default skill (Speckit for parity).
3. **Given** a workflow stage request with a non-Speckit skill override, **When** the skill is allowlisted and healthy, **Then** the stage runs through that skill contract.
4. **Given** a skill invocation fails, **When** fallback is enabled, **Then** the direct stage implementation executes and records fallback metadata.

---

### User Story 3 - Progressive Rollout with Parity and Drift Controls (Priority: P2)

As a release owner, I want staged rollout controls and parity checks so skills-first adoption can expand safely without regressions.

**Why this priority**: Skills-first migration must be observable and reversible during rollout.

**Independent Test**: Enable shadow and canary flags, run fixture workflows, compare skill and direct outputs, and verify metrics/logs identify execution path and timing.

**Acceptance Scenarios**:

1. **Given** shadow mode is enabled, **When** a stage runs, **Then** both paths can execute while only the primary path mutates state.
2. **Given** canary percentage is configured, **When** multiple runs are triggered, **Then** only the configured subset uses skills-first as primary.
3. **Given** parity fixtures exist, **When** regression checks run, **Then** output drift beyond defined tolerance is reported as a failure.

### Edge Cases

- Speckit skill files are missing from one runtime mirror (`.codex` vs `.agents`) even though workers expect Speckit always available.
- A requested skill is not allowlisted for a stage.
- Codex auth volume is missing/mis-mounted when worker preflight runs.
- `DEFAULT_EMBEDDING_PROVIDER=google` is set without `GOOGLE_API_KEY`.
- Skill path and direct fallback both fail for the same stage.
- Queue pressure causes long-running fallback phases; observability must still indicate path and latency.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Worker startup MUST verify Speckit capability is available for all automation worker processes, independent of selected workflow policy.
- **FR-002**: Workflow execution MUST use a stage-based skill contract (`specify`, `plan`, `tasks`, `analyze`, `implement`) as the primary orchestration mechanism.
- **FR-003**: The system MUST support allowlisted non-Speckit skills per stage while preserving Speckit as the default stage mapping.
- **FR-004**: The system MUST provide a per-stage direct fallback path when skill execution fails or is explicitly bypassed.
- **FR-005**: Workflow metadata and logs MUST record selected skill, execution path (`skill` vs `direct_fallback`), and stage timing.
- **FR-006**: Existing queue bindings (`speckit`, `codex`, `gemini`) and existing workflow API behavior MUST remain backward compatible during migration.
- **FR-007**: Docker Compose and environment defaults MUST document the fastest path for authenticated Codex workers and Gemini embeddings.
- **FR-008**: Quickstart guidance MUST include one-time Codex auth volume setup, startup commands, and verification checks for worker readiness and embedding defaults.
- **FR-009**: Startup checks MUST fail fast with actionable diagnostics when required prerequisites are missing (Speckit capability, Codex auth, or Google embedding credentials when Google is configured).
- **FR-010**: Rollout controls MUST support global and per-stage toggles, including shadow mode and canary mode for skills-first execution.
- **FR-011**: Test coverage MUST include skill-vs-direct parity/regression checks and fallback behavior validation.
- **FR-012**: Implementation phase deliverables MUST include production runtime code changes and validation via `./tools/test_unit.sh`.

### Key Entities *(include if feature involves data)*

- **SkillCatalogEntry**: Declares skill identifier, supported stages, allowlist status, and health/preflight requirements.
- **WorkflowStageContract**: Canonical input/output envelope for each stage execution with `run_id`, `feature_id`, `stage`, and artifact references.
- **WorkflowExecutionPathRecord**: Captures selected skill, primary path, fallback path usage, durations, and status per stage.
- **WorkerStartupProfile**: Captures startup checks for Speckit availability, Codex auth status, queue bindings, and readiness state.
- **EmbeddingRuntimeProfile**: Captures resolved embedding provider/model and required credential availability at startup/runtime.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Following quickstart from a clean environment produces a ready Codex worker and Gemini worker without interactive prompts.
- **SC-002**: 100% of supported workflow stages can be invoked through the stage-based skill contract, with fallback available where configured.
- **SC-003**: Parity regression suite reports no unresolved critical drift between skill-driven and direct execution for defined fixtures.
- **SC-004**: Logs/metrics for stage execution always include stage name, selected skill id, execution path, and duration.
- **SC-005**: Validation command `./tools/test_unit.sh` passes for the finalized implementation.

## Assumptions

- Speckit skills remain installed and mirrored in both `.codex/skills` and `.agents/skills`.
- Existing `/api/workflows/speckit` routes remain in place during this umbrella migration; naming generalization can be layered without breaking clients.
- Google Gemini (`gemini-embedding-001`) remains the default embedding model for the fastest-path deployment profile.
