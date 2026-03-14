# Feature Specification: Skills Workflow Alignment Refresh

**Feature Branch**: `015-skills-workflow`  
**Created**: 2026-03-02  
**Status**: Draft  
**Input**: User description: "Update specs/015-skills-workflow to make it align with the current state and strategy of the MoonMind project. Implement all of the updated tasks when done. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Canonical Stage Contract and Phase Metadata (Priority: P1)

As an operator, I want workflow stage contracts and surfaced metadata to match the live runtime behavior so API responses and artifacts are reliable during triage.

**Why this priority**: Stage contract drift causes incorrect expectations in automation runs and slows incident response.

**Independent Test**: Run Spec Automation phase serialization tests and verify phase payloads expose canonical skill metadata for current stage execution.

**Acceptance Scenarios**:

1. **Given** a stage execution metadata payload containing selected skill details, **When** phase state is serialized, **Then** `selected_skill`, `adapter_id`, and `execution_path` are all available.
2. **Given** legacy phase metadata without explicit adapter data, **When** the phase belongs to Agentkit stages, **Then** defaults resolve to `selected_skill=agentkit` and `adapter_id=agentkit`.
3. **Given** stage contracts are reviewed in this feature, **When** stage names are referenced, **Then** they match current runtime stages (`discover_next_phase`, `submit_codex_job`, `apply_and_publish`).

---

### User Story 2 - Shared Skills Runtime and Fast-Path Documentation Parity (Priority: P1)

As a platform maintainer, I want `015` design artifacts to match shared-skills runtime behavior and current worker startup practices so onboarding and operations follow the real system.

**Why this priority**: `015` is a historical umbrella spec and must not contradict current runbooks.

**Independent Test**: Validate that the refreshed quickstart and contracts match current service names, auth scripts, and shared skills workspace conventions.

**Acceptance Scenarios**:

1. **Given** worker auth prerequisites, **When** quickstart steps are followed, **Then** they use `./tools/auth-codex-volume.sh` and `./tools/auth-gemini-volume.sh` before startup.
2. **Given** run workspace skill wiring, **When** contracts are inspected, **Then** they describe `.agents/skills` and `.gemini/skills` linking to one `skills_active` directory.
3. **Given** worker startup checks, **When** stage skills do not require Agentkit, **Then** documentation reflects conditional Agentkit verification instead of unconditional global requirements.

---

### User Story 3 - Runtime Validation and Backward-Compatible Safety (Priority: P2)

As a release owner, I want refreshed `015` tasks to require runtime code changes plus tests so this update is implementation-valid and not docs-only.

**Why this priority**: Runtime intent requires enforceable implementation deltas and verification.

**Independent Test**: Confirm updated tasks include runtime file changes under `moonmind/` and validation via `./tools/test_unit.sh`.

**Acceptance Scenarios**:

1. **Given** updated `tasks.md`, **When** runtime scope is reviewed, **Then** at least one production runtime task and one validation task exist.
2. **Given** implementation is complete, **When** tests run, **Then** `./tools/test_unit.sh` passes for the modified runtime and API metadata coverage.

### Edge Cases

- Legacy phase metadata is missing `adapterId` while consumers expect adapter-level observability.
- Agentkit is configured as the default stage skill but local mirror roots do not contain a Agentkit skill directory.
- Strict mirror validation is disabled, so startup checks rely on runtime materialization and selection diagnostics.
- Queue consumers parse historical payloads that may not include newer skills metadata keys.

## Requirements *(mandatory)*

### Documentation-Backed Requirements

- **DOC-REQ-001** (Source: `moonmind/workflows/agentkit_celery/tasks.py` task constants, `docs/ops-runbook.md` codex queue logging): Canonical runtime stages for this workflow are `discover_next_phase`, `submit_codex_job`, and `apply_and_publish`. *(Maps: FR-001)*
- **DOC-REQ-002** (Source: `docs/TaskQueueSystem.md` required prepare behavior §6.2): Run workspace contracts must materialize `.agents/skills` and `.gemini/skills` links to a shared `skills_active` directory. *(Maps: FR-005)*
- **DOC-REQ-003** (Source: `docs/AgentKitAutomation.md` operational runbook §8, `docs/ops-runbook.md` auth volume guidance): Fast-path operator docs must use `./tools/auth-codex-volume.sh` and `./tools/auth-gemini-volume.sh` before worker startup. *(Maps: FR-005)*
- **DOC-REQ-004** (Source: `docs/AgentKitAutomation.md` health checks for bundled CLIs): Agentkit CLI verification should run only when configured stage skills require the Agentkit adapter. *(Maps: FR-006)*
- **DOC-REQ-005** (Source: `.specify/memory/constitution.md` non-negotiable observability constraints): Workflow surfaces must emit structured stage metadata so operators can diagnose what happened without raw worker internals. *(Maps: FR-002, FR-004)*
- **DOC-REQ-006** (Source: `.specify/memory/constitution.md` compatibility and migration constraints): Metadata normalization must preserve backward-compatible behavior for legacy persisted payloads where required fields may be absent. *(Maps: FR-003)*
- **DOC-REQ-007** (Source: feature input for this refresh): Deliverables for this feature must include production runtime code updates, not docs-only edits. *(Maps: FR-007)*
- **DOC-REQ-008** (Source: `AGENTS.md` testing instructions): Unit verification must run via `./tools/test_unit.sh` as the canonical test entrypoint. *(Maps: FR-008)*

### Functional Requirements

- **FR-001** (`DOC-REQ-001`): `specs/015-skills-workflow` artifacts MUST reference current runtime stage names (`discover_next_phase`, `submit_codex_job`, `apply_and_publish`).
- **FR-002** (`DOC-REQ-005`): Spec Automation phase metadata normalization MUST expose `selectedSkill`, `adapterId`, and `executionPath` when available.
- **FR-003** (`DOC-REQ-006`): Legacy Agentkit phase metadata without explicit skill fields MUST default to `selectedSkill=agentkit`, `adapterId=agentkit`, and `executionPath=skill`.
- **FR-004** (`DOC-REQ-005`): API response schemas for Spec Automation phase details MUST include adapter metadata for skills-first observability.
- **FR-005** (`DOC-REQ-002`, `DOC-REQ-003`): Documentation contracts in `015` MUST reflect shared skills runtime layout (`skills_active`, `.agents/skills`, `.gemini/skills`) and current auth startup path.
- **FR-006** (`DOC-REQ-004`): `015` runtime assumptions MUST align with current strategy where Agentkit verification is conditioned on configured stage skills.
- **FR-007** (`DOC-REQ-007`): Updated implementation tasks MUST include production runtime file changes and validation tests.
- **FR-008** (`DOC-REQ-008`): Implementation validation MUST run via `./tools/test_unit.sh`.

### Key Entities *(include if feature involves data)*

- **StageExecutionMetadata**: Normalized phase metadata containing selected skill id, adapter id, execution path, and fallback flags.
- **WorkflowStageContract**: Canonical definition of the three runtime stage operations (`discover_next_phase`, `submit_codex_job`, `apply_and_publish`).
- **SharedSkillsWorkspace**: Run-scoped skills materialization output linking `.agents/skills` and `.gemini/skills` to `skills_active`.
- **WorkerFastPathProfile**: Startup/auth configuration profile for codex and gemini workers used by operator quickstart.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of refreshed `015` artifacts use current stage names and shared-skills runtime terminology.
- **SC-002**: Spec Automation phase responses include `adapter_id` whenever metadata is present, with Agentkit defaults for legacy stage records.
- **SC-003**: Unit tests covering metadata normalization and API serialization pass through `./tools/test_unit.sh`.
- **SC-004**: Updated `tasks.md` contains explicit runtime implementation and validation tasks, and all are completed.

## Assumptions

- Agentkit remains the default configured stage skill in current deployments, even as policy mode may be permissive or allowlist.
- Existing database structures and legacy workflow naming compatibility remain unchanged by this feature refresh.
- This refresh updates `015` artifacts and targeted runtime metadata surfacing only; broader architecture evolution remains in newer specs.
