# Feature Specification: Task Proposal Targeting Policy

**Feature Branch**: `034-task-proposal-update`  
**Created**: 2026-02-20  
**Status**: Draft  
**Input**: User description: "Implement the updated Task Proposals system as described in docs/TaskProposalQueue.md"
**Scope Guard**: Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Source Document Requirements

| ID | Source Section | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | 2. Existing Behavior to Preserve | Proposals must continue to store the canonical `taskCreateRequest`, and promotion executes against `taskCreateRequest.payload.repository`. |
| DOC-REQ-002 | 2. Existing Behavior to Preserve | Deduplication stays keyed by `(repository + normalized title)`, notifications keep repository + priority, and human review stays mandatory prior to promotion. |
| DOC-REQ-003 | 3.1 Global policy knobs | Add env config `MOONMIND_PROPOSAL_TARGETS=project|moonmind|both` to drive default proposal targets. |
| DOC-REQ-004 | 3.1 Global policy knobs | Add env config `MOONMIND_CI_REPOSITORY` (default `MoonLadderStudios/MoonMind`) and ensure MoonMind proposals target it. |
| DOC-REQ-005 | 3.2 Per-task override | Extend canonical task payload with optional `task.proposalPolicy.targets`, `maxItems`, and `minSeverityForMoonMind` to override defaults. |
| DOC-REQ-006 | 4. MoonMind CI Proposal Normalization | MoonMind proposals must use category `run_quality` (alias `moonmind_ci` transitional), require ≥1 signal tag from the approved set, and follow the `[run_quality] ...` title format. |
| DOC-REQ-007 | 5. Priority Routing for CI Proposals | Server derives `reviewPriority` for MoonMind proposals from signal severity, overriding caller-supplied values when conflicts occur. |
| DOC-REQ-008 | 6. Origin Metadata Requirements | MoonMind proposals must include origin metadata with `triggerRepo`, `triggerJobId`, `signal`, and optional `triggerStepId` plus detector data. |
| DOC-REQ-009 | 7. Proposal Creation Rules | Worker logic must determine effective targets, enforce `maxItems` per target, gate MoonMind targets by severity, and set repository per target. |
| DOC-REQ-010 | 8. API and Schema Delta | Update schemas and APIs to accept `task.proposalPolicy` and normalized CI metadata without altering promotion semantics. |
| DOC-REQ-011 | 9. Migration and Rollout | Update dashboard filters for repository + `run_quality` category/tag visibility. |
| DOC-REQ-012 | 10. Acceptance Criteria | MoonMind-targeted proposals must always include required metadata, tags, and automatic priority elevation, while project proposals continue to behave unchanged. |
| DOC-REQ-013 | 3.2 Per-task override + Edge Cases | When no override exists, workers/services must fall back to documented default per-target `maxItems` values and a defined severity vocabulary/threshold for MoonMind proposals.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Workers select proposal targets dynamically (Priority: P1)

MoonMind worker services need to emit follow-up proposals to either the originating project repo or the MoonMind CI repo depending on task policy and runtime signals.

**Why this priority**: Target routing drives every other behavior change; without it, workers cannot generate the correct proposal set.

**Independent Test**: Execute a task run with `proposalPolicy.targets=["project"]` and confirm that only project proposals are queued; repeat with `["project","moonmind"]` and confirm both repositories receive proposals respecting `maxItems`.

**Acceptance Scenarios**:

1. **Given** a task run with `proposalPolicy.targets=["project"]`, **When** the worker proposes follow-up work, **Then** only the task repository receives proposals and dedup behavior matches existing rules.
2. **Given** `MOONMIND_PROPOSAL_TARGETS=both` and severity above `minSeverityForMoonMind`, **When** detectors fire, **Then** the worker enqueues MoonMind CI proposals capped by `maxItems.moonmind`.

---

### User Story 2 - Reviewers triage MoonMind CI improvements (Priority: P2)

MoonMind reviewers open the proposal dashboard to inspect CI/run-quality proposals with consistent categories, tags, and metadata for faster triage.

**Why this priority**: Without normalized metadata, MoonMind cannot prioritize CI regressions or understand triggering context.

**Independent Test**: Create sample MoonMind proposal payloads and verify category `run_quality`, tag set, origin metadata, and auto-derived priority surface in the dashboard filters and API responses.

**Acceptance Scenarios**:

1. **Given** a MoonMind-targeted proposal, **When** it is persisted, **Then** reviewers see category `run_quality`, at least one approved signal tag, and origin metadata with triggering repo/job/step.
2. **Given** detectors mark severity as high (e.g., loop detected), **When** the proposal is processed, **Then** the system overrides any lower client-specified priority to `HIGH` before reviewers are notified.

---

### User Story 3 - Platform engineers audit schema/config updates (Priority: P3)

Platform engineers manage the API schema, config knobs, and dashboard filters to ensure compatibility with the new policy layer.

**Why this priority**: Schema/config drift would block deployments and break compatibility across workers and services.

**Independent Test**: Apply migrations introducing `proposalPolicy` fields, add env variables, and ensure both server validation and UI filters run through automated tests.

**Acceptance Scenarios**:

1. **Given** the API schema update, **When** validation runs, **Then** payloads containing `task.proposalPolicy` and CI metadata pass while existing payloads continue to validate.
2. **Given** dashboard filter updates, **When** users filter by repository + `run_quality` + signal tags, **Then** the UI returns the expected subset of proposals without regressions.

---

## Policy Defaults and Dedup Semantics

To satisfy DOC-REQ-013, the system needs deterministic fallbacks whenever a task omits `proposalPolicy`:

- **Default targets + slots**: Treat `MOONMIND_PROPOSAL_TARGETS` as authoritative, defaulting to `project`. When no per-target `maxItems` are provided, clamp to `project=3` and `moonmind=2`. Inputs of `0`, negative values, or `null` revert to these defaults rather than disabling limits.
- **Severity vocabulary**: Only accept `low`, `medium`, `high`, or `critical`. MoonMind gating uses `minSeverityForMoonMind`, which defaults to `high` in absence of overrides. Workers must log when submissions are skipped because severity < floor.
- **Dedup normalization**: The primary dedup key stays `(repository + normalized title)`, but the normalized title must append a deterministic slug of sorted signal tags for MoonMind targets: e.g., `[run_quality] Reduce duplicate output (tags: duplicate_output+loop_detected)`. Project proposals omit the suffix. This keeps existing dedup behavior while preventing collisions when multiple detectors fire.
- **Priority override provenance**: Whenever the server elevates `reviewPriority`, persist a `priority_override_reason` (detector + rule name) alongside the proposal payload so dashboards and auditors can trace auto-escalations.
- **Dashboard surfacing**: Derived priority badges (HIGH/NORMAL/LOW) must display in the UI next to category/tag chips to make auto-derived severity actionable during reviews.

These constants and behaviors require regression tests spanning config parsing, worker policy merging, API validation, and dashboard rendering.

---

### Edge Cases

- How does the system respond when `proposalPolicy.targets` includes `moonmind` but severity never meets `minSeverityForMoonMind`? Workers must skip MoonMind targets without error spam while logging the reason.
- What happens if `maxItems.project` or `maxItems.moonmind` is zero or missing? Apply the documented defaults (`project=3`, `moonmind=2`) to prevent unlimited proposal floods.
- How are duplicate MoonMind proposals avoided when multiple detectors emit the same signal set within one run? The normalized title must include the sorted signal-tag slug so the dedup key differentiates unique tag combinations.
- How does the server behave if callers attempt to force `reviewPriority=LOW` while detectors indicate a `HIGH` severity signal? Server-side derivation overrides client input, records the override reason, and the dashboard badge reflects the new priority.
- What occurs when MoonMind CI metadata lacks `triggerJobId`? Proposal creation should fail validation with actionable errors rather than producing partially attributed proposals.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001 (DOC-REQ-001, DOC-REQ-002)**: Proposal persistence must retain `taskCreateRequest` as the canonical payload, ensure promotion executes against `taskCreateRequest.payload.repository`, keep dedup keys `repository + normalized title`, continue repository-aware notifications, and affirm mandatory human review workflow.
- **FR-002 (DOC-REQ-003)**: Configuration must expose `MOONMIND_PROPOSAL_TARGETS` with allowed values `project`, `moonmind`, or `both`, defaulting to `project` when unset, and workers/servers must read it when no per-task override exists.
- **FR-003 (DOC-REQ-004)**: Proposals targeting MoonMind must always set `repository=MOONMIND_CI_REPOSITORY`, with `MOONMIND_CI_REPOSITORY` defaulting to `MoonLadderStudios/MoonMind` yet overridable via env/config.
- **FR-004 (DOC-REQ-005)**: The canonical task payload schema must accept optional `task.proposalPolicy.targets`, `maxItems.project/moonmind`, and `minSeverityForMoonMind`; worker execution resolves effective policy by merging overrides with global defaults.
- **FR-005 (DOC-REQ-009)**: Worker proposal generation must: (a) read effective targets, (b) emit project proposals using the task repository, (c) gate MoonMind proposals by severity and `minSeverityForMoonMind`, and (d) enforce per-target `maxItems` while logging skipped emissions.
- **FR-006 (DOC-REQ-006)**: MoonMind-targeted proposals must set `category=run_quality` (accepting alias `moonmind_ci` only during migration), ensure titles follow `[run_quality] <summary>` pattern, and include ≥1 tag from `{retry, duplicate_output, missing_ref, conflicting_instructions, flaky_test, loop_detected, artifact_gap}`.
- **FR-007 (DOC-REQ-008)**: MoonMind proposals must populate `origin_metadata.triggerRepo`, `origin_metadata.triggerJobId`, and `origin_metadata.signal`, optionally `origin_metadata.triggerStepId` plus detector-specific metrics (duplicate ratios, missing paths, conflict classes) for auditor context.
- **FR-008 (DOC-REQ-007)**: Server-side proposal creation must derive `reviewPriority` based on severity rules (HIGH for loop detection, retry exhaustion, missing required references, conflicting instructions; NORMAL for moderate duplication; LOW for informational), overriding caller values while persisting override provenance.
- **FR-009 (DOC-REQ-010)**: API schema + validation must recognize new `proposalPolicy` inputs and normalized MoonMind metadata without altering `POST /api/proposals` or promotion endpoints; backward compatibility tests must cover old payloads.
- **FR-010 (DOC-REQ-011)**: Dashboard/front-end search must support filtering proposals by repository, category `run_quality`, and signal tags; results must display derived priority and origin metadata fields.
- **FR-011 (DOC-REQ-012)**: Acceptance tests must prove project-only, MoonMind-only, and dual-target runs behave per policy, MoonMind proposals always carry required metadata/tags/priority, and legacy project proposals behave identically to pre-change behavior.
- **FR-012**: Add automated validation rejecting proposals that attempt MoonMind targets without providing any signal tags or origin metadata, preventing silent data corruption.
- **FR-013 (DOC-REQ-013)**: When overrides are absent, enforce the documented defaults (`MOONMIND_PROPOSAL_TARGETS` fallback, `maxItems` of 3/2, severity vocabulary with `high` floor), append sorted signal-tag slugs to normalized titles for MoonMind proposals, persist `priority_override_reason`, and expose derived priority badges/logs so reviewers see why escalations happened.

### Key Entities

- **ProposalPolicy**: Optional task payload object containing `targets` (ordered array of `project` and/or `moonmind`), `maxItems` dictionary keyed by target, and `minSeverityForMoonMind`; stored alongside the run for auditing effective rules.
- **TaskCreateRequest**: Canonical payload representing the promoted task; unchanged fields include `payload.repository`, request metadata, and existing dedup identifiers.
- **Proposal**: Existing entity extended with normalized `category`, derived `reviewPriority`, `origin_metadata`, signal tags, and target repository details.
- **SignalMetadata**: Structured data capturing detector outputs such as retry counts, duplicate ratios, missing references, and conflict classes; used for severity gating and reviewer context.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Worker integration tests demonstrate 100% compliance with policy selection matrix across project-only, MoonMind-only, and dual-target scenarios with ≤1% false-positive MoonMind proposals.
- **SC-002**: CI proposals tagged as `run_quality` with required origin metadata reach reviewer dashboards in under 2 minutes on average, with 0 validation errors logged per 1,000 proposals.
- **SC-003**: Auto-derived priority matches expected severity mapping in ≥95% of simulated MoonMind proposal cases, verified via unit tests covering each detector scenario.
- **SC-004**: Proposal dashboard filters for repository/category/tag combinations return correct datasets with <1% variance from reference query results and include derived metadata fields for every MoonMind proposal.
- **SC-005**: Derived priority badges render in the dashboard for all MoonMind proposals, match the persisted `reviewPriority`, and display the override reason when escalation occurred.
