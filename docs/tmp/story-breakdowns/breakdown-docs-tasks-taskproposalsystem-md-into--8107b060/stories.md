# Task Proposal System Story Breakdown

- Source design: `docs/Tasks/TaskProposalSystem.md`
- Story extraction date: 2026-04-14T22:51:43Z
- Coverage gate: PASS - every major design point is owned by at least one story.

## Design Summary

TaskProposalSystem.md defines proposals as reviewable follow-up items discovered during MoonMind.Run, stored as canonical taskCreateRequest payloads, and promoted only after human approval into new Temporal-backed runs. The remaining implementation work centers on canonical proposal intent at submit time, gated proposal-stage execution, payload and policy validation, origin identity and promotion linkage, skill preservation, Mission Control visibility, and worker-boundary tests.

## Coverage Points

- **DESIGN-REQ-001 - Proposal purpose and lifecycle** (requirement, 1. Summary): Proposals are reviewable follow-up work items discovered during MoonMind.Run and remain non-executing review objects until promoted.
- **DESIGN-REQ-002 - Canonical taskCreateRequest payload** (contract, 1, 2, 5): Stored proposals carry /api/executions-compatible taskCreateRequest payloads with repository, runtime, publish, tool, and optional skill selectors.
- **DESIGN-REQ-003 - Human approval gate** (constraint, 2, 7): Proposal creation is not execution; only explicit human promotion starts a new MoonMind.Run.
- **DESIGN-REQ-004 - Canonical submit-time proposal intent** (contract, 3.1, 3.1.1): Task creation paths preserve task.proposeTasks, task.proposalPolicy, task.skills, and step.skills under initialParameters.task.
- **DESIGN-REQ-005 - Codex managed-session normalization** (integration, 3.1.1, 10): Codex managed-session user submissions and internal task creation normalize proposal intent before MoonMind.Run starts.
- **DESIGN-REQ-006 - Proposal lifecycle state vocabulary** (state-model, 3.2, 8.1): The proposals state is consistent across workflow state, execution API, dashboard mapping, and docs.
- **DESIGN-REQ-007 - Proposal-stage gating** (requirement, 3.3): The proposals stage runs only when global generation is enabled and canonical task.proposeTasks is true.
- **DESIGN-REQ-008 - Activity-based proposal generation** (architecture, 3.4, 9): Generators run in Temporal activities, analyze artifacts/results, treat inputs as untrusted, avoid side effects, redact unsafe data, and use refs for large context.
- **DESIGN-REQ-009 - Skill selector preservation** (contract, 3.4, 5.3, 7.2): Explicit non-default skill intent is preserved when material and proposals do not embed skill bodies or mutable runtime skill directories.
- **DESIGN-REQ-010 - Proposal submission validation** (requirement, 3.5, 8.3): Submission validates candidates, policy, origin, routing, and skill fields before storage; malformed candidates are skipped with visible errors.
- **DESIGN-REQ-011 - Finish-summary proposal outcomes** (artifact, 3.6, 8.2): Typed results and reports/run_summary.json record requested/generated/submitted counts and redacted errors.
- **DESIGN-REQ-012 - Global and per-task proposal policy** (state-model, 4): Policy resolution merges global defaults and task.proposalPolicy, applies caps and MoonMind gates, and stamps defaultRuntime only when runtime is omitted.
- **DESIGN-REQ-013 - Project and MoonMind routing** (integration, 4.4, 4.5): Project proposals keep the triggering repository; MoonMind run-quality proposals rewrite to configured MoonMind repo only after category, severity, and tag gates.
- **DESIGN-REQ-014 - Payload runtime and tool validation** (contract, 5): Proposal payloads validate supported runtimes and canonical tool shapes, including tool.type = skill and no tool.type = agent_runtime.
- **DESIGN-REQ-015 - Origin metadata standardization** (state-model, 6): Workflow-originated proposals use origin.source = workflow, origin.id = workflow_id, snake_case metadata, and temporal_run_id only as diagnostic data.
- **DESIGN-REQ-016 - Promotion flow and persisted linkage** (requirement, 7.1, 10): Promotion validates open proposals, merges overrides, creates MoonMind.Run through TemporalExecutionService, stores promoted execution/workflow linkage, and returns both records.
- **DESIGN-REQ-017 - Promotion runtime override semantics** (contract, 7.2, 7.3): Runtime overrides apply to the promoted request, preserve skill selectors by default, and fail before workflow creation for disabled runtimes or incompatible skills.
- **DESIGN-REQ-018 - API and UI observability** (observability, 8): APIs and Mission Control expose proposals state, counts/errors, origin-filtered links, and compact runtime/repository/publish/skill context.
- **DESIGN-REQ-019 - Best-effort failure behavior** (resilience, 2, 8.3): Proposal failures never compromise parent run correctness; retries are bounded/idempotent and partial success is visible.
- **DESIGN-REQ-020 - Worker queue separation** (architecture, 9): Generation runs on LLM-capable workers while submission/storage runs on control-plane or integrations-capable workers.
- **DESIGN-REQ-021 - Documented gap closure** (migration, 10): Stories close the current implementation gaps while keeping migration tracking under docs/tmp.

## Ordered Story Candidates

### STORY-001: Normalize proposal intent for all task submissions

- Short name: `proposal-intent-normalization`
- Why: Codex managed sessions and internal task creation are the highest-risk remaining sources of non-canonical proposal intent.
- Description: As an operator, I want every task creation surface to persist proposal intent in the canonical nested task payload so proposal behavior is durable and independent of adapter-local state.
- Independent test: Submit representative tasks through ordinary API, Codex managed-session, schedule/promotion, and Codex internal task paths; assert initialParameters.task contains proposal and skill fields and non-canonical locations are not the write contract.
- Dependencies: None
- Needs clarification: None
- Scope:
  - Normalize task.proposeTasks and task.proposalPolicy into initialParameters.task for API submissions, promotion, schedules, Codex managed-session user tasks, and Codex internal API-created tasks.
  - Preserve task.skills and step.skills in the same canonical payload when present.
  - Remove reliance on root-level or session-local proposal flags as the new write contract while retaining only documented Temporal replay compatibility reads where required.
- Out of scope:
  - Proposal generation logic itself.
  - New runtime modes or new skill-source precedence.
- Acceptance criteria:
  - All new task creation surfaces write proposal opt-in to initialParameters.task.proposeTasks.
  - All new task creation surfaces write routing overrides to initialParameters.task.proposalPolicy.
  - Explicit task.skills and step.skills survive submission without embedding skill bodies.
  - Codex managed-session-created tasks do not depend on root-level, turn metadata, session binding metadata, container environment, or adapter-local state as durable proposal intent.
  - Workflow-boundary tests cover the real worker binding shape and one previous-payload compatibility case.
- Requirements:
  - Canonicalize proposal intent across submission surfaces.
  - Preserve skill selector fields through submission.
  - Prove Codex managed-session normalization before MoonMind.Run starts.
- Source design coverage:
  - DESIGN-REQ-004: canonical submit-time proposal intent.
  - DESIGN-REQ-005: Codex managed-session normalization.
  - DESIGN-REQ-009: task-facing skill selectors are preserved without materializing skill content.
  - DESIGN-REQ-021: closes a documented partially implemented gap.
- Assumptions:
  - Replay compatibility reads may remain only where required for in-flight histories.
- Handoff: Normalize proposal intent for all task submissions. Build this as a one-story Moon Spec slice with failing workflow/activity/API/UI tests before production code, preserve the original design input, and verify the owned design requirements explicitly.

### STORY-002: Enforce proposal-stage execution and finish-summary reporting

- Short name: `proposal-stage-reporting`
- Why: The proposal stage is partially implemented, but the doc requires consistent gating, lifecycle state mapping, best-effort behavior, and finish-summary artifacts.
- Description: As an operator, I want proposal-capable runs to enter a visible proposals stage only under the documented gates and to report proposal outcomes without changing parent-run correctness.
- Independent test: Run proposal-enabled and proposal-disabled workflows through the Temporal boundary; assert only enabled runs enter proposals, mapping is consistent, summaries contain counts/errors, and injected stage failures are reported without failing successful parent execution.
- Dependencies: STORY-001
- Needs clarification: None
- Scope:
  - Gate proposals on global enablement plus canonical task.proposeTasks.
  - Expose mm_state = proposals while generation/submission is active and map proposals to running for dashboard compatibility.
  - Record requested/generated/submitted/error outcomes in typed results and reports/run_summary.json.
  - Keep proposal-stage failures bounded, redacted, and isolated from otherwise successful parent work.
- Out of scope:
  - Changing proposal candidate schema.
  - Detailed proposal-review UX beyond state/count/error visibility.
- Acceptance criteria:
  - The workflow enters proposals only when global generation is enabled and initialParameters.task.proposeTasks is true.
  - Codex-originated runs use the canonical nested field for the gate.
  - Execution APIs and Mission Control mapping expose proposals consistently.
  - Typed result and run_summary.json include requested status, candidate count, submitted count, and redacted errors.
  - Malformed proposal-stage output is visible as partial/skipped work and does not compromise parent-run correctness.
- Requirements:
  - Implement durable proposal-stage gating.
  - Standardize proposal lifecycle state exposure.
  - Persist proposal outcome telemetry in finish summaries.
- Source design coverage:
  - DESIGN-REQ-001: proposals remain reviewable follow-up work until later promotion.
  - DESIGN-REQ-006: lifecycle vocabulary and status mapping.
  - DESIGN-REQ-007: proposal-stage gate.
  - DESIGN-REQ-011: finish-summary outcomes.
  - DESIGN-REQ-019: best-effort failure behavior.
- Assumptions:
  - Global proposal generation settings already exist or can be read from workflow settings.
- Handoff: Enforce proposal-stage execution and finish-summary reporting. Build this as a one-story Moon Spec slice with failing workflow/activity/API/UI tests before production code, preserve the original design input, and verify the owned design requirements explicitly.

### STORY-003: Validate proposal submission policy and payload contracts

- Short name: `proposal-payload-policy`
- Why: Submission is the durable side-effect boundary for malformed candidates, unsafe payloads, invalid runtimes, and incorrect routing.
- Description: As an operator, I want proposal candidates validated and routed through the documented policy contract so stored proposals are safe, canonical, deduplicable, and execution-ready for later review.
- Independent test: Feed proposal.submit candidates covering valid project and MoonMind routing, invalid runtime/tool shapes, malformed skill selectors, defaultRuntime stamping, explicit runtime preservation, capacity caps, and severity/tag rejection; assert stored records or skipped validation errors match contract.
- Dependencies: STORY-001
- Needs clarification: None
- Scope:
  - Validate taskCreateRequest against the canonical Temporal task submit contract.
  - Enforce supported runtimes and canonical tool shape, including tool.type = skill and rejection of tool.type = agent_runtime.
  - Resolve global defaults plus task.proposalPolicy at submission time, preserving explicit candidate values and stamping defaultRuntime only when missing.
  - Apply per-target caps, MoonMind severity/tag gates, and repository rewrite rules.
  - Validate task.skills and step.skills selectors without embedding skill bodies or mutable runtime state.
- Out of scope:
  - Promotion-time override handling.
  - Proposal detail UI.
- Acceptance criteria:
  - Stored proposals use /api/executions-compatible taskCreateRequest payloads.
  - taskCreateRequest.payload.repository determines deduplication and future execution target.
  - Unsupported runtimes and tool.type = agent_runtime fail before storage.
  - Explicit candidate runtime, publish, repository, tool, and skill selector values win over defaults.
  - defaultRuntime is stamped only when task.runtime.mode is omitted.
  - MoonMind proposals require run_quality category, configured severity floor, and approved tags before repository rewrite.
  - Malformed skill selectors are skipped with visible validation errors rather than silently dropped.
- Requirements:
  - Validate canonical proposal payloads.
  - Resolve proposal policy at submission time.
  - Enforce project vs MoonMind routing.
  - Preserve explicit skill-selection intent without runtime-local skill state.
- Source design coverage:
  - DESIGN-REQ-002: canonical stored taskCreateRequest contract.
  - DESIGN-REQ-008: submission consumes artifact-backed side-effect-free generation output.
  - DESIGN-REQ-009: skill selector preservation during storage.
  - DESIGN-REQ-010: submission validation.
  - DESIGN-REQ-012: policy resolution.
  - DESIGN-REQ-013: project/MoonMind routing.
  - DESIGN-REQ-014: runtime/tool validation.
  - DESIGN-REQ-019: malformed candidate skip semantics.
  - DESIGN-REQ-020: submission validates selectors without materializing skill context.
- Assumptions:
  - MoonMind repository, severity floor, tag allowlist, and caps are available through existing configuration.
- Handoff: Validate proposal submission policy and payload contracts. Build this as a one-story Moon Spec slice with failing workflow/activity/API/UI tests before production code, preserve the original design input, and verify the owned design requirements explicitly.

### STORY-004: Persist proposal origin and promotion linkage

- Short name: `proposal-promotion-linkage`
- Why: The current snapshot says promotion creates MoonMind.Run but lacks persisted promoted execution linkage and fully standardized origin naming.
- Description: As an operator, I want each proposal to retain source workflow identity and record the execution created by promotion so review history and follow-up runs are traceable.
- Independent test: Create workflow-originated proposals and promote one with/without overrides; assert standardized snake_case origin fields, continue-as-new identity stability, stored promoted workflow/execution linkage, and promote API response includes updated proposal plus created execution metadata.
- Dependencies: STORY-003
- Needs clarification: None
- Scope:
  - Standardize workflow-originated proposal records on origin.source = workflow and origin.id = workflow_id.
  - Normalize origin metadata keys to workflow_id, temporal_run_id, trigger_repo, starting_branch, and working_branch.
  - Keep workflow_id as durable identity and temporal_run_id as diagnostic metadata, including continue-as-new.
  - Implement promotion flow that validates open proposal, merges overrides, submits through TemporalExecutionService.create_execution(), persists promoted linkage, and returns both records.
- Out of scope:
  - Dashboard layout changes beyond fields needed by existing surfaces.
  - Compatibility aliases for old origin names outside explicit Temporal cutover requirements.
- Acceptance criteria:
  - Workflow proposals use origin.source = workflow and origin.id = workflow_id, never temporal as canonical origin.
  - Origin metadata uses snake_case keys consistently across workflow payloads, storage, APIs, and docs.
  - temporal_run_id is diagnostic and does not replace workflow_id/taskId identity.
  - Promotion verifies the proposal is open before execution creation.
  - Promotion persists created workflow/execution identifier on the proposal record.
  - Promote API response includes updated proposal and created MoonMind.Run metadata.
  - Superseded origin references are updated or removed in the same change.
- Requirements:
  - Standardize proposal origin identity.
  - Persist promoted execution linkage.
  - Return promotion execution metadata.
  - Preserve the human approval gate before creating work.
- Source design coverage:
  - DESIGN-REQ-003: human promotion is the only action that starts a new run.
  - DESIGN-REQ-015: origin metadata standardization.
  - DESIGN-REQ-016: promotion flow and persisted linkage.
  - DESIGN-REQ-021: closes documented promotion linkage and origin shape gaps.
- Assumptions:
  - MoonMind is pre-release, but Temporal-facing in-flight payload compatibility still needs an explicit cutover or compatibility test.
- Handoff: Persist proposal origin and promotion linkage. Build this as a one-story Moon Spec slice with failing workflow/activity/API/UI tests before production code, preserve the original design input, and verify the owned design requirements explicitly.

### STORY-005: Preserve skill intent through proposal promotion

- Short name: `promotion-skill-runtime`
- Why: The doc calls out that proposal payloads do not yet model task.skills and step.skills end-to-end across generation, storage, promotion, and UI.
- Description: As an operator, I want promotion-time runtime overrides to preserve explicit skill intent unless intentionally overridden so follow-up work does not silently drift.
- Independent test: Promote stored proposals with explicit task.skills and step.skills using no override, runtimeMode override, taskCreateRequestOverride, and malformed/incompatible skill overrides; assert selectors persist by default, stored payload is unchanged, disabled runtimes fail before workflow creation, and incompatible skills fail or require explicit override.
- Dependencies: STORY-003, STORY-004
- Needs clarification: None
- Scope:
  - Merge taskCreateRequestOverride into stored taskCreateRequest without dropping task.skills or step.skills.
  - Apply runtimeMode only by constructing a task.runtime.mode override.
  - Validate merged payload and selected skill selectors before workflow creation.
  - Fail validation for disabled runtimes and incompatible selected skills unless an explicit override path exists.
  - Do not mutate the stored proposal payload when promotion-time runtime overrides are applied.
- Out of scope:
  - New skill compatibility matrix unless none of the existing runtime capability data can validate.
  - Hidden re-resolution of skill-source precedence during promotion.
- Acceptance criteria:
  - Promotion overrides preserve stored task.skills and step.skills unless explicitly changed.
  - runtimeMode shortcut produces only a task.runtime.mode override.
  - Changing runtime does not automatically erase or rewrite skill intent.
  - Merged payload validation runs before TemporalExecutionService.create_execution().
  - Disabled runtimes fail before workflow creation.
  - Incompatible selected skills fail validation or require documented explicit override.
  - Tests cover blank, unknown, or newly introduced runtime/status values where relevant.
- Requirements:
  - Preserve explicit agent skill intent through promotion.
  - Validate runtime overrides and skill selector compatibility.
  - Keep stored proposal payload immutable during promotion.
- Source design coverage:
  - DESIGN-REQ-002: canonical payload fields during promotion.
  - DESIGN-REQ-009: end-to-end skill intent preservation.
  - DESIGN-REQ-014: runtime validation.
  - DESIGN-REQ-016: merged-payload validation before workflow creation.
  - DESIGN-REQ-017: runtime override semantics.
  - DESIGN-REQ-021: closes skill preservation gap.
- Assumptions:
  - Existing runtime availability metadata can serve as backend source of truth.
- Handoff: Preserve skill intent through proposal promotion. Build this as a one-story Moon Spec slice with failing workflow/activity/API/UI tests before production code, preserve the original design input, and verify the owned design requirements explicitly.

### STORY-006: Expose proposal review context in Mission Control

- Short name: `proposal-review-context`
- Why: The design requires proposal-stage visibility and says the UI does not yet clearly expose skill-related execution context.
- Description: As an operator reviewing proposals, I want APIs and Mission Control to show proposal state, origin links, counts/errors, and compact execution context so I can decide whether to promote work.
- Independent test: Use API contract tests and Mission Control UI tests to verify filtered proposal links, counts/errors, proposals -> running status mapping, and runtime/repository/publish/skill context with explicit and inherited skill cases.
- Dependencies: STORY-002, STORY-004, STORY-005
- Needs clarification: None
- Scope:
  - Link execution detail to proposals filtered by originSource=workflow and originId=<workflow_id>.
  - Expose proposal counts and redacted errors from finish summary data.
  - Return/render compact review context for runtime, repository, publish settings, and explicit vs inherited skill mode.
  - Align status mapping for scheduled, waiting_on_dependencies, awaiting_slot, proposals, and completed.
- Out of scope:
  - Full proposal editor redesign.
  - Jira or external issue integration changes.
- Acceptance criteria:
  - Execution detail links to proposals via originSource=workflow and originId=<workflow_id>.
  - Generated/submitted counts and redacted errors are visible where finish summaries are displayed.
  - Status compatibility maps scheduled -> queued, waiting_on_dependencies -> waiting, awaiting_slot -> queued, proposals -> running, completed -> completed.
  - Review context identifies runtime, repository, publish mode, and skill-selection mode without exposing raw skill bodies or runtime paths.
  - UI/API tests cover missing or inherited skill context without inventing explicit selectors.
- Requirements:
  - Expose proposal review links and status.
  - Surface proposal telemetry.
  - Show compact runtime/repository/publish/skill context safely.
- Source design coverage:
  - DESIGN-REQ-006: UI/API lifecycle mapping.
  - DESIGN-REQ-011: user-visible counts/errors.
  - DESIGN-REQ-018: API and UI observability.
  - DESIGN-REQ-019: partial-success visibility.
  - DESIGN-REQ-021: closes UI/context gaps.
- Assumptions:
  - Existing proposal review surfaces can be extended rather than replaced.
- Handoff: Expose proposal review context in Mission Control. Build this as a one-story Moon Spec slice with failing workflow/activity/API/UI tests before production code, preserve the original design input, and verify the owned design requirements explicitly.

### STORY-007: Separate proposal worker boundaries and regression coverage

- Short name: `proposal-worker-boundaries`
- Why: The design depends on workflows orchestrating while activities perform side effects, with generation and submission separated across worker capabilities.
- Description: As a maintainer, I want proposal generation and submission to execute on the correct activity families with boundary-level tests so side effects, LLM analysis, and validation stay isolated.
- Independent test: Run worker-runtime and activity-boundary tests that inspect activity families for proposal.generate and proposal.submit, inject retries/duplicates, and assert generation uses artifact refs while submission performs durable writes idempotently.
- Dependencies: STORY-003
- Needs clarification: None
- Scope:
  - Route proposal.generate to the LLM-capable activity fleet and keep it side-effect free.
  - Route proposal.submit and storage to control-plane or integrations-capable activity workers.
  - Verify submission validates skill payloads without materializing runtime skill context.
  - Add boundary tests for activity routing, invocation shapes, idempotent bounded retries, and artifact-backed context refs.
- Out of scope:
  - LLM prompt changes except those needed for no side effects or secret leakage.
  - Unrelated worker infrastructure changes.
- Acceptance criteria:
  - proposal.generate is scheduled on LLM-capable routing and does not commit, push, create tasks, or store proposals.
  - proposal.submit/storage is scheduled on control-plane or integrations-capable routing and is the only proposal creation side-effect boundary.
  - Large inputs, logs, and skill metadata use artifact-backed refs where needed instead of workflow history blobs.
  - Submission retries are bounded and idempotent.
  - Boundary tests cover real worker binding or Temporal activity wrapper invocation shapes.
  - Tests verify untrusted inputs are redacted or excluded from stored proposals and visible errors.
- Requirements:
  - Enforce worker queue separation.
  - Keep generation side-effect free.
  - Keep workflow histories compact with artifact refs.
  - Cover idempotent and redacted failure behavior at boundaries.
- Source design coverage:
  - DESIGN-REQ-008: activity-based generation safety.
  - DESIGN-REQ-010: side-effecting submission boundary.
  - DESIGN-REQ-019: bounded/idempotent failure behavior.
  - DESIGN-REQ-020: worker queue separation and skill validation boundary.
- Assumptions:
  - proposal.generate and proposal.submit already exist in the activity catalog and can be verified or adjusted.
- Handoff: Separate proposal worker boundaries and regression coverage. Build this as a one-story Moon Spec slice with failing workflow/activity/API/UI tests before production code, preserve the original design input, and verify the owned design requirements explicitly.

## Coverage Matrix

| Design requirement | Owning stories |
| --- | --- |
| DESIGN-REQ-001 | STORY-002 |
| DESIGN-REQ-002 | STORY-003, STORY-005 |
| DESIGN-REQ-003 | STORY-004 |
| DESIGN-REQ-004 | STORY-001 |
| DESIGN-REQ-005 | STORY-001 |
| DESIGN-REQ-006 | STORY-002, STORY-006 |
| DESIGN-REQ-007 | STORY-002 |
| DESIGN-REQ-008 | STORY-003, STORY-007 |
| DESIGN-REQ-009 | STORY-001, STORY-003, STORY-005 |
| DESIGN-REQ-010 | STORY-003, STORY-007 |
| DESIGN-REQ-011 | STORY-002, STORY-006 |
| DESIGN-REQ-012 | STORY-003 |
| DESIGN-REQ-013 | STORY-003 |
| DESIGN-REQ-014 | STORY-003, STORY-005 |
| DESIGN-REQ-015 | STORY-004 |
| DESIGN-REQ-016 | STORY-004, STORY-005 |
| DESIGN-REQ-017 | STORY-005 |
| DESIGN-REQ-018 | STORY-006 |
| DESIGN-REQ-019 | STORY-002, STORY-003, STORY-006, STORY-007 |
| DESIGN-REQ-020 | STORY-003, STORY-007 |
| DESIGN-REQ-021 | STORY-001, STORY-004, STORY-005, STORY-006 |

## Dependencies

- STORY-001 depends on: None
- STORY-002 depends on: STORY-001
- STORY-003 depends on: STORY-001
- STORY-004 depends on: STORY-003
- STORY-005 depends on: STORY-003, STORY-004
- STORY-006 depends on: STORY-002, STORY-004, STORY-005
- STORY-007 depends on: STORY-003

## Out Of Scope

- Creating or modifying spec.md files: Breakdown output mode is docs_tmp; specify happens later.
- Creating directories under specs/: The task explicitly reserves specs/ creation for the specify phase.
- Implementing TaskProposalSystem.md during breakdown: This run creates story handoff artifacts and Jira-board stories only.
- Moving migration checklists into canonical docs: Constitution principle XII keeps volatile implementation tracking under docs/tmp.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
