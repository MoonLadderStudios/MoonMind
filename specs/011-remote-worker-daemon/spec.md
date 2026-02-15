# Feature Specification: Remote Worker Daemon (015-Aligned)

**Feature Branch**: `011-remote-worker-daemon`  
**Created**: 2026-02-13  
**Status**: Draft (Updated 2026-02-14 for 015 umbrella alignment)  
**Input**: User description: "Update the 011 spec and implementation to align with the new 015 umbrella spec."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Deterministic Worker Startup with Speckit + Embedding Readiness (Priority: P1)

As an operator, I want the standalone remote worker daemon to fail fast when prerequisites are missing so queue jobs are never claimed by an unready worker.

**Why this priority**: 015 umbrella requires Speckit always available and actionable startup diagnostics for Codex/Gemini embedding profiles.

**Independent Test**: Start `moonmind-codex-worker` with/without required CLIs and embedding credentials; verify startup exits clearly when prerequisites are missing.

**Acceptance Scenarios**:

1. **Given** Codex and Speckit CLIs are installed, **When** preflight runs, **Then** startup validates `codex login status` and `speckit --version` before daemon loop starts.
2. **Given** `DEFAULT_EMBEDDING_PROVIDER=google`, **When** neither `GOOGLE_API_KEY` nor `GEMINI_API_KEY` is set, **Then** startup fails with an actionable error.
3. **Given** required prerequisites are present, **When** daemon starts, **Then** it enters polling without preflight crashes.

---

### User Story 2 - Skills-First Queue Execution with Codex Compatibility Fallback (Priority: P1)

As a queue operator, I want remote jobs to support skills-based execution semantics while preserving existing `codex_exec` behavior.

**Why this priority**: 015 umbrella moves workers toward skills-first workflows without breaking existing runtime paths.

**Independent Test**: Queue both `codex_exec` and `codex_skill` jobs and verify execution metadata includes selected skill and path (`skill`, `direct_fallback`, or `direct_only`).

**Acceptance Scenarios**:

1. **Given** a `codex_exec` job, **When** worker executes it, **Then** it follows direct execution and emits metadata with default selected skill.
2. **Given** a `codex_skill` job with `skillId=speckit`, **When** worker executes it, **Then** it uses the skills path and records `executionPath=skill`.
3. **Given** a `codex_skill` job with an allowlisted non-Speckit skill, **When** worker executes it, **Then** it runs through compatibility fallback and records `executionPath=direct_fallback`.
4. **Given** a `codex_skill` job with a non-allowlisted skill, **When** claim processing runs, **Then** worker fails the job without invoking handler execution.
5. **Given** task payload `codex.model` and/or `codex.effort` values are present, **When** execution starts, **Then** worker applies those settings to `codex exec` for that task.
6. **Given** task payload omits `codex.model` and/or `codex.effort`, **When** execution starts, **Then** worker falls back to worker-default model/effort settings.

---

### User Story 3 - Artifact and Lease Robustness Remains Backward Compatible (Priority: P2)

As a platform owner, I want heartbeats, artifact upload, and terminal status handling to remain robust during skills-first adoption.

**Why this priority**: Migration is only safe if existing resilience behavior remains intact.

**Independent Test**: Run successful and failing jobs while heartbeats are active; verify artifact uploads and terminal transitions still occur.

**Acceptance Scenarios**:

1. **Given** a running job, **When** execution exceeds one heartbeat interval, **Then** lease heartbeats are sent at approximately `leaseSeconds/3`.
2. **Given** execution succeeds with artifacts, **When** handler completes, **Then** artifacts upload and job transitions to `succeeded`.
3. **Given** execution fails, **When** worker handles the exception, **Then** job transitions to `failed` with error details and best-effort event publication.

### Edge Cases

- `codex` CLI is present but `speckit` CLI is missing.
- Google embedding profile is selected but no embedding credential key is present.
- Claimed job type is unsupported.
- `codex_skill` payload omits repository context.
- `codex_skill` uses non-allowlisted `skillId`.
- `codex` payload override is non-object or malformed.
- Heartbeat errors occur while terminal transitions still need to complete.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a standalone `moonmind-codex-worker` daemon entrypoint independent of Celery bootstrap.
- **FR-002**: Worker startup MUST verify Codex and Speckit CLI availability and MUST fail fast when checks fail.
- **FR-003**: Worker startup MUST enforce embedding readiness checks such that Google embedding profiles require `GOOGLE_API_KEY` or `GEMINI_API_KEY`.
- **FR-004**: Worker claim policy MUST support `codex_exec` and `codex_skill` types and forward capabilities to claim endpoints.
- **FR-005**: `codex_exec` jobs MUST execute through the existing checkout + `codex exec` + patch/log artifact pipeline.
- **FR-006**: `codex_skill` jobs MUST execute through skills-first semantics with allowlist enforcement and compatibility fallback support.
- **FR-007**: Worker events for claimed/started/completed/failed jobs MUST include skill execution metadata (`selectedSkill`, `executionPath`, `usedSkills`, `usedFallback`, `shadowModeRequested`).
- **FR-008**: Worker MUST preserve heartbeat cadence and terminal status behavior for crash/reclaim compatibility.
- **FR-009**: Runtime deliverables MUST include production code and validation tests; docs-only updates are insufficient.
- **FR-010**: Worker MUST support per-task Codex runtime overrides via payload `codex.model` and `codex.effort` for `codex_exec`/`codex_skill`, with precedence `task override -> worker default -> Codex CLI default`.

### Key Entities *(include if feature involves data)*

- **RemoteWorkerStartupProfile**: Preflight result for Codex/Speckit availability and embedding profile readiness.
- **RemoteWorkerSkillPolicy**: Worker config values controlling `default_skill`, `allowed_skills`, and allowed queue job types.
- **QueueExecutionMetadata**: Execution metadata attached to emitted worker events.
- **CodexSkillCompatibilityRequest**: Normalized `codex_skill` request mapped to `codex_exec` fallback payload when required.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `moonmind-codex-worker` exits quickly with clear messaging when Codex/Speckit CLI or required embedding credentials are missing.
- **SC-002**: Worker processes both `codex_exec` and `codex_skill` jobs, with deterministic execution-path metadata in emitted events.
- **SC-003**: Unit tests cover preflight checks, skills allowlist enforcement, and `codex_skill` compatibility mapping.
- **SC-004**: Heartbeat and terminal state behaviors remain covered by automated tests.
- **SC-005**: `./tools/test_unit.sh` passes for finalized implementation.
- **SC-006**: Unit tests verify codex command construction applies task `codex.model`/`codex.effort` overrides and fallback defaults deterministically.

## Assumptions

- Existing queue API endpoints (`/api/queue/jobs/*`) remain unchanged during this umbrella alignment.
- Skill definitions for remote execution may be expanded later; current compatibility flow maps `codex_skill` requests into deterministic `codex_exec` execution.
