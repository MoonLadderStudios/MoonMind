# Feature Specification: Skills-First Spec Automation Pipeline

**Feature Branch**: `002-document-speckit-automation`  
**Created**: 2025-11-03  
**Status**: Draft (Updated 2026-02-14 for 015 umbrella alignment)  
**Input**: User description: "Update the 002 spec and implementation to align with the new 015 umbrella spec."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fast Worker Launch for Automation (Priority: P1)

As an operator, I want one deterministic startup path for Spec Automation workers so Codex authentication and Google Gemini embedding defaults are ready before automation runs are triggered.

**Why this priority**: Every automation run depends on worker readiness, credential validation, and embedding defaults.

**Independent Test**: From a clean `.env`, perform one-time Codex auth on the shared volume, start `rabbitmq`, `api`, `celery_codex_worker`, and `celery_gemini_worker`, then verify preflight and embedding defaults in logs/runtime.

**Acceptance Scenarios**:

1. **Given** `GOOGLE_API_KEY`, `DEFAULT_EMBEDDING_PROVIDER=google`, and `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`, **When** workers and API start, **Then** runtime embedding resolution uses Google Gemini defaults.
2. **Given** Codex auth was completed once on the configured volume, **When** automation workers restart, **Then** startup preflight succeeds without interactive login prompts.
3. **Given** worker startup checks run, **When** logs are inspected, **Then** Speckit capability is confirmed for both Codex and Gemini automation workers.

---

### User Story 2 - Skills-First Automation Stages (Priority: P1)

As a platform engineer, I want automation phase telemetry and contracts to be skills-first so stage execution semantics are not hard-coded to a unique Speckit workflow while preserving Speckit default behavior.

**Why this priority**: The 015 umbrella requires a skills-first model with Speckit always available and backward compatibility.

**Independent Test**: Serialize representative automation phase state payloads and verify selected skill/execution path metadata is present and normalized for legacy Speckit phases.

**Acceptance Scenarios**:

1. **Given** a legacy phase (`speckit_specify`, `speckit_plan`, `speckit_tasks`), **When** run detail is serialized, **Then** response metadata defaults to `selected_skill=speckit` and `execution_path=skill`.
2. **Given** explicit skills metadata in phase state payload (`selectedSkill`, `executionPath`), **When** run detail is serialized, **Then** API responses expose the explicit values without loss.
3. **Given** new stage aliases for analyze and implement, **When** phase contracts are documented, **Then** they remain backward compatible with existing persisted phase values.

---

### User Story 3 - Review Automation Outputs and Isolation Controls (Priority: P2)

As an operator, I want run detail and artifact endpoints to expose phase-level metadata and security-safe context so failures can be diagnosed without shell access.

**Why this priority**: Operators must be able to audit execution path, artifacts, and environment behavior through API outputs.

**Independent Test**: Trigger API serialization tests and verify run detail includes phase timeline, artifacts, and skills execution metadata while preserving existing endpoint behavior.

**Acceptance Scenarios**:

1. **Given** a completed run, **When** `/api/spec-automation/runs/{id}` is requested, **Then** per-phase metadata includes skill path fields alongside existing status and artifact references.
2. **Given** artifacts are requested, **When** `/api/spec-automation/runs/{id}/artifacts/{artifact_id}` is called, **Then** existing artifact detail and download behavior remains unchanged.

### Edge Cases

- Scope validation script is unavailable in the repository while orchestration requires tasks/diff scope gates.
- Persisted legacy phase entries have no explicit skills metadata.
- Explicit non-Speckit skill metadata is present but execution path fields are partially missing.
- Worker startup succeeds for Codex but fails for Speckit capability checks.
- Google embeddings are configured without required API credentials.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Worker startup MUST verify Speckit capability for automation worker processes, independent of selected stage skill.
- **FR-002**: Automation phase contracts MUST support skills-first metadata (`selectedSkill`, `executionPath`, fallback/shadow flags) while preserving backward compatibility with legacy phase values.
- **FR-003**: Legacy Speckit phases MUST normalize to default skills metadata (`selectedSkill=speckit`, `executionPath=skill`) when explicit metadata is absent.
- **FR-004**: API run detail responses for automation runs MUST expose normalized skills metadata fields per phase.
- **FR-005**: Existing automation API routes and artifact download behavior MUST remain backward compatible.
- **FR-006**: Automation data model and contracts MUST include stage aliases for analyze and implement to align with umbrella stage coverage goals.
- **FR-007**: Quickstart and operator docs MUST include the fastest path for Codex-authenticated workers and Google Gemini embedding defaults.
- **FR-008**: Startup checks MUST fail fast with actionable diagnostics when Speckit capability, Codex auth, or Google embedding credentials are missing.
- **FR-009**: Runtime deliverables MUST include production code updates and validation tests; docs-only updates are insufficient.

### Key Entities *(include if feature involves data)*

- **SpecAutomationRun**: Lifecycle record for one automation run, including status, branch/PR references, and artifact links.
- **SpecAutomationTaskState**: Per-phase execution record including status, attempt, logs, and normalized skills metadata.
- **SpecAutomationArtifact**: Persisted run outputs with source phase and retention metadata.
- **SpecAutomationAgentConfiguration**: Agent backend/version snapshot for run-level auditability.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Run detail responses expose phase-level skills metadata for 100% of serialized phase records.
- **SC-002**: Legacy phase records without explicit skills metadata serialize with deterministic defaults (`speckit` / `skill`) in 100% of cases.
- **SC-003**: Existing artifact metadata/detail/download API behavior remains unchanged for current clients.
- **SC-004**: Fast-path worker startup guidance for Codex auth + Gemini embeddings is documented and testable.
- **SC-005**: Validation command `./tools/test_unit.sh` is executed and reported for this implementation cycle.

## Assumptions & Dependencies

- Existing `spec_automation_*` persistence tables remain in use; this alignment does not require destructive schema changes.
- Legacy `speckit_*` phase values remain valid and should continue to deserialize in API responses.
- Speckit skills are installed and exposed via shared adapters in `.agents/skills` and `.gemini/skills` (legacy `.codex/skills` may be retained as fallback).
- Existing `/api/spec-automation/*` endpoints remain the compatibility surface during this migration.

### Scope Boundaries

- This update does not redesign unrelated queueing systems or the broader agent queue MVP.
- This update does not require replacing legacy persisted phase values.
- This update does not introduce mandatory external observability dependencies beyond existing optional metrics hooks.
