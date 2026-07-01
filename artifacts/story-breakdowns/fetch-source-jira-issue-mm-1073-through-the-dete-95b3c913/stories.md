# Story Breakdown: MM-1073 Status Cleanup

- Source: `docs/Workflows/WorkflowStatus.md`
- Source document class: `canonical-declarative`
- Extracted at: `2026-07-01T08:37:15Z`
- Output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The selected source is an archived workflow-status pointer that directs new status decisions to active Temporal Visibility, Workflow Execution Product, and Step Ledger documents. The broader Jira cleanup asks MoonMind to keep legitimate status domains separate, standardize value formatting, centralize backend/frontend ownership, quarantine legacy no_changes aliases, repair persisted legacy values, and add audit/test guardrails against future cross-domain drift.

## Source Reference

The selected source file is an archived pointer. It is preserved as the file-backed source for traceability, while story content follows the active canonical documents named by that pointer:

- `docs/Temporal/VisibilityAndUiQueryModel.md`
- `docs/Temporal/WorkflowExecutionProductModel.md`
- `docs/Temporal/StepLedgerAndProgressModel.md`

## Canonical Claims

- `docs/Workflows/WorkflowStatus.md#archived-pointer-001`: WorkflowStatus.md is an archived pointer and is no longer the canonical workflow status design source.
- `docs/Workflows/WorkflowStatus.md#archived-pointer-002`: Workflow status decisions belong in VisibilityAndUiQueryModel, WorkflowExecutionProductModel, and StepLedgerAndProgressModel.
- `docs/Workflows/WorkflowStatus.md#archived-pointer-003`: The archived design failed to separate workflow status from step status and no longer matches the active exact-vs-compatibility model.
- `docs/Workflows/WorkflowStatus.md#archived-pointer-004`: The archived file should remain only as a migration pointer during documentation cleanup and must not drive new product decisions.

## Coverage Points

- `DESIGN-REQ-001` (state-model): Status domains stay separate and named by owner - Workflow lifecycle state, Temporal close status, derived temporalStatus, step ledger status, step execution artifact status, and provider normalized status must be distinct domains with explicit owners.
- `DESIGN-REQ-002` (contract): Canonical formatting by surface - Stored/API machine values use lowercase snake_case, JSON fields use camelCase, Python enum names use UPPER_SNAKE_CASE with lowercase values, finish outcome codes use UPPER_SNAKE_CASE, labels use product case, and CSS classes use kebab-case.
- `DESIGN-REQ-003` (requirement): Backend status modules own canonical values and mappings - Small domain modules should own workflow, close, temporal, step ledger, step execution, integration, and compatibility constants and conversion helpers rather than a global status enum.
- `DESIGN-REQ-004` (migration): Legacy aliases are accepted only at compatibility boundaries - no_changes and NO_CHANGES remain inbound compatibility aliases only; new workflow histories, DB rows, API responses, UI strings, tests, and docs emit no_commit / NO_COMMIT.
- `DESIGN-REQ-005` (integration): Provider aliases remain inside adapter normalizers - Provider-specific statuses such as Jules done, cancelled, in-progress, processing, and awaiting_user_feedback are valid adapter-boundary input but must not leak into MoonMind workflow state vocabulary.
- `DESIGN-REQ-006` (state-model): Step ledger and step execution artifact vocabularies are explicit - StepLedgerRowModel.status owns the operator step ledger vocabulary. Any retained StepExecutionStatus vocabulary must be renamed to clarify artifact/projection semantics and mapped explicitly to ledger status.
- `DESIGN-REQ-007` (requirement): Frontend status helpers are domain-specific - Dashboard helpers should split workflow, step, and integration status labeling/pill behavior, normalize tokens consistently, generate CSS class names from canonical values, and keep legacy aliases explicit.
- `DESIGN-REQ-008` (observability): Audit tooling categorizes status-shaped tokens by domain - A status-token audit tool should report token, guessed domain, files, canonicality, and action so valid provider or historical statuses are not flattened into workflow lifecycle cleanup.
- `DESIGN-REQ-009` (migration): Persisted legacy values are repaired deliberately - Persisted search attributes, state fields, finish summaries, and related JSON should be inventoried and repaired from legacy no_changes / NO_CHANGES forms to no_commit / NO_COMMIT where applicable.
- `DESIGN-REQ-010` (artifact): Documentation points to canonical status sources - References to archived WorkflowStatus.md should be replaced with the active canonical Temporal Visibility, Workflow Execution Product, and Step Ledger documents, with examples using canonical values.
- `DESIGN-REQ-011` (constraint): Regression tests and static checks prevent status drift - Backend, frontend, mapper, and audit tests should fail when canonical status domains drift or new raw status strings appear outside approved modules and compatibility allowlists.
- `DESIGN-REQ-012` (non-goal): Archived pointer remains non-authoritative - WorkflowStatus.md should not regain authority over status product decisions; durable status rules live in the active canonical documents.

## Ordered Stories

### STORY-001: Define canonical status domains and audit inventory

As a MoonMind maintainer, I want a canonical status-domain matrix and inventory tool so workflow, step, provider, close-status, and compatibility tokens have clear ownership before behavior changes land.

- Short name: `status-domain-inventory`
- Source path: `docs/Workflows/WorkflowStatus.md`
- Claim IDs: `docs/Workflows/WorkflowStatus.md#archived-pointer-002`, `docs/Workflows/WorkflowStatus.md#archived-pointer-003`, `docs/Workflows/WorkflowStatus.md#archived-pointer-004`
- Coverage IDs: `DESIGN-REQ-001`, `DESIGN-REQ-002`, `DESIGN-REQ-008`, `DESIGN-REQ-010`, `DESIGN-REQ-012`
- Dependencies: None

Acceptance criteria:
- A reader can identify the owner and canonical value set for workflow lifecycle state, Temporal close status, temporalStatus, step ledger status, step execution artifact status, and provider normalized status.
- The audit report categorizes seeded tokens including mm_state, closeStatus, temporalStatus, no_changes, awaiting_action, queued, in-progress, and StepExecutionStatus by domain/action.
- Archived WorkflowStatus.md is not cited as the source for new status decisions except as an archival pointer.
- Formatting rules are documented using lowercase snake_case machine values, camelCase JSON fields, UPPER_SNAKE_CASE finish outcome codes, and kebab-case CSS classes.

Independent test: Run the audit command against the repository and verify it emits domain, files, canonicality, and action columns while the new domain matrix names every status domain from the design.

### STORY-002: Centralize backend workflow and step status contracts

As a backend maintainer, I want domain-specific status modules and explicit mapping helpers so workflow lifecycle, close status, temporalStatus, step ledger, step execution artifact, and integration statuses cannot be mixed accidentally.

- Short name: `backend-status-contracts`
- Source path: `docs/Workflows/WorkflowStatus.md`
- Claim IDs: `docs/Workflows/WorkflowStatus.md#archived-pointer-002`, `docs/Workflows/WorkflowStatus.md#archived-pointer-003`
- Coverage IDs: `DESIGN-REQ-001`, `DESIGN-REQ-002`, `DESIGN-REQ-003`, `DESIGN-REQ-005`, `DESIGN-REQ-006`
- Dependencies: `STORY-001`

Acceptance criteria:
- Workflow lifecycle state values exactly match the active Temporal Visibility mm_state value set.
- Temporal close status remains a separate domain and workflow_state_to_close_status covers every terminal workflow lifecycle state.
- Step ledger status values exactly match the active Step Ledger document.
- Provider-specific aliases are absent from workflow lifecycle canonical modules and remain in adapter normalizers.
- Any retained StepExecutionStatus vocabulary is renamed or sourced from one place and has an explicit mapping to ledger status.

Independent test: Targeted backend tests import the new modules, assert canonical value sets match the Temporal Visibility and Step Ledger docs, and verify close-status mapping completeness.

Needs clarification:
- [NEEDS CLARIFICATION] Whether StepExecutionStatus is still an active artifact/projection domain or can be removed entirely must be decided from audit evidence.

### STORY-003: Quarantine no_changes compatibility and repair persisted values

As an operator, I want legacy no_changes / NO_CHANGES values accepted only where old histories or artifacts enter the system so new state, APIs, and UI consistently emit no_commit / NO_COMMIT.

- Short name: `legacy-status-quarantine`
- Source path: `docs/Workflows/WorkflowStatus.md`
- Claim IDs: `docs/Workflows/WorkflowStatus.md#archived-pointer-003`, `docs/Workflows/WorkflowStatus.md#archived-pointer-004`
- Coverage IDs: `DESIGN-REQ-004`, `DESIGN-REQ-009`, `DESIGN-REQ-011`, `DESIGN-REQ-012`
- Dependencies: `STORY-001`, `STORY-002`

Acceptance criteria:
- New workflow histories, DB rows, API responses, UI labels, tests, and docs emit no_commit / NO_COMMIT only.
- Legacy aliases are accepted only through named compatibility functions or documented inbound parsing surfaces.
- Alias observation includes domain, alias, and canonical value without logging secrets or large payloads.
- Persisted search attributes, state fields, and finish-summary JSON have an explicit inventory and repair path.

Independent test: Seed legacy no_changes / NO_CHANGES inputs at each compatibility boundary and verify outputs, stored new values, and API responses canonicalize to no_commit / NO_COMMIT while direct canonical-domain callers reject aliases.

### STORY-004: Split frontend workflow, step, and integration status helpers

As a dashboard user, I want workflow and step statuses rendered with domain-correct labels and classes so execution-level state, step progress, and provider statuses are not visually or semantically conflated.

- Short name: `frontend-status-helpers`
- Source path: `docs/Workflows/WorkflowStatus.md`
- Claim IDs: `docs/Workflows/WorkflowStatus.md#archived-pointer-002`, `docs/Workflows/WorkflowStatus.md#archived-pointer-003`
- Coverage IDs: `DESIGN-REQ-001`, `DESIGN-REQ-002`, `DESIGN-REQ-004`, `DESIGN-REQ-007`, `DESIGN-REQ-011`
- Dependencies: `STORY-001`, `STORY-002`

Acceptance criteria:
- Workflow state labels and pill props are generated from the workflow lifecycle domain only.
- Step status labels and pill props are generated from the step ledger domain only.
- Integration/provider statuses are handled by integration helpers and do not appear as workflow lifecycle values.
- Unknown statuses render neutral and expose a developer warning or equivalent diagnostic without crashing.
- CSS class names use kebab-case derived from canonical lowercase snake_case values.

Independent test: Run focused frontend tests proving every workflow and step status has a label and pill class, legacy no_changes renders as No commit, and workflow-only states are rejected by step helper tests.

### STORY-005: Enforce status-domain guardrails in tests and CI

As a maintainer, I want automated backend, frontend, and static checks so new raw status strings or cross-domain aliases cannot re-enter active code unnoticed.

- Short name: `status-guardrails`
- Source path: `docs/Workflows/WorkflowStatus.md`
- Claim IDs: `docs/Workflows/WorkflowStatus.md#archived-pointer-004`
- Coverage IDs: `DESIGN-REQ-008`, `DESIGN-REQ-010`, `DESIGN-REQ-011`, `DESIGN-REQ-012`
- Dependencies: `STORY-001`, `STORY-002`, `STORY-003`, `STORY-004`

Acceptance criteria:
- CI or the targeted required suite fails when a new raw status string appears outside approved locations.
- Tests prove canonical backend value sets match the active docs.
- Tests prove frontend helpers cover all canonical workflow and step statuses.
- Docs and API examples no longer point implementers at archived WorkflowStatus.md as the status authority.
- The coverage explicitly protects against recreating a generic global status vocabulary.

Independent test: Introduce a temporary unallowlisted raw status token in active code and verify the audit fail-on-unknown mode fails, then remove it and verify targeted backend/frontend tests pass.

## Coverage Matrix

- `docs/Workflows/WorkflowStatus.md#archived-pointer-001` -> `STORY-001`
- `docs/Workflows/WorkflowStatus.md#archived-pointer-002` -> `STORY-001`, `STORY-002`, `STORY-004`
- `docs/Workflows/WorkflowStatus.md#archived-pointer-003` -> `STORY-001`, `STORY-002`, `STORY-003`, `STORY-004`
- `docs/Workflows/WorkflowStatus.md#archived-pointer-004` -> `STORY-001`, `STORY-003`, `STORY-005`
- `DESIGN-REQ-001` -> `STORY-001`, `STORY-002`, `STORY-004`
- `DESIGN-REQ-002` -> `STORY-001`, `STORY-002`, `STORY-004`
- `DESIGN-REQ-003` -> `STORY-002`
- `DESIGN-REQ-004` -> `STORY-003`, `STORY-004`
- `DESIGN-REQ-005` -> `STORY-002`
- `DESIGN-REQ-006` -> `STORY-002`
- `DESIGN-REQ-007` -> `STORY-004`
- `DESIGN-REQ-008` -> `STORY-001`, `STORY-005`
- `DESIGN-REQ-009` -> `STORY-003`
- `DESIGN-REQ-010` -> `STORY-001`, `STORY-005`
- `DESIGN-REQ-011` -> `STORY-003`, `STORY-004`, `STORY-005`
- `DESIGN-REQ-012` -> `STORY-001`, `STORY-003`, `STORY-005`

## Out Of Scope

- Creating one global Status enum.
- Using archived WorkflowStatus.md as the source for new product decisions.
- Running /moonspec.specify, generating spec.md, creating specs/ directories, implementing code, creating Jira issues, or opening a pull request in this breakdown step.

## Recommended First Story

Run `STORY-001: Define canonical status domains and audit inventory` through `/moonspec.specify` first. It establishes the domain matrix and audit evidence that the backend, frontend, migration, and guardrail stories depend on.

## Downstream Notes

- TDD remains the default strategy for downstream `/moonspec.plan`, `/moonspec.tasks`, and `/moonspec.implement`.
- Run `/moonspec.verify` after implementation to compare final behavior against the original design preserved through specify.
- This breakdown did not create `spec.md` files or directories under `specs/`.
