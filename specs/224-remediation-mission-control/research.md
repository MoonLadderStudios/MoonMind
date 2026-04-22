# Research: Remediation Mission Control Surfaces

## FR-001 / Create Remediation Entry Points

Decision: Add the create action to task detail and eligible status/problem surfaces, starting with failed, attention-required, stuck, provider-slot, and session problem states already represented in task detail data.
Evidence: `docs/Tasks/TaskRemediation.md` section 15.1 requires Mission Control create entrypoints; `frontend/src/entrypoints/task-detail.tsx` owns task detail action and evidence regions.
Rationale: Task detail is the shared surface where the target workflow ID, current run state, steps, artifacts, and problem context are available without inventing a new route.
Alternatives considered: A standalone remediation page was rejected because the source design calls for entrypoints from existing target surfaces.
Test implications: UI test renders eligible and ineligible task states and asserts create action visibility.

## FR-002 and FR-003 / Canonical Create Prefill

Decision: Use the existing `POST /api/executions/{workflow_id}/remediation` convenience route when available and prefill its body from task detail state.
Evidence: `api_service/api/routers/executions.py` already defines `create_remediation_execution`; `docs/Tasks/TaskRemediation.md` section 7.5 allows the route only as an expansion into canonical task-shaped creation.
Rationale: Reusing the existing route avoids a second durable payload shape while giving Mission Control a simple target-scoped create flow.
Alternatives considered: Directly constructing a full `POST /api/executions` payload in the frontend was rejected as higher duplication when the convenience route already exists.
Test implications: Frontend submit test and router regression test for canonical target/policy/evidence payload shape.

## FR-004 and FR-005 / Bidirectional Link Read Surface

Decision: Add or expose a bounded execution remediations API that returns inbound and outbound remediation link summaries for task detail.
Evidence: `TemporalExecutionService.list_remediations_for_target` and `list_remediation_targets` already exist, but no REST read route was found for Mission Control consumption.
Rationale: The frontend should not infer remediation links by scanning artifacts or parsing task parameters; it needs a trusted read model sourced from persisted links.
Alternatives considered: Embedding remediation link data into every task detail response was rejected unless the existing detail payload already has a natural extension point, because a separate route keeps payload growth bounded.
Test implications: API unit tests for inbound/outbound direction, empty state, and bounded compact fields; UI tests for target and remediation panels.

## FR-006 and FR-007 / Evidence Presentation

Decision: Group remediation evidence artifacts by known remediation artifact types while still routing access through existing artifact preview/download links.
Evidence: `docs/Tasks/TaskRemediation.md` section 14.1 defines required remediation artifacts; current task detail already renders artifacts and timeline artifact links.
Rationale: Operators need remediation-specific labels and grouping, but authorization and content access must stay in the artifact subsystem.
Alternatives considered: Reading context/log bodies directly into task detail was rejected because the source design requires artifact refs and bounded presentation.
Test implications: UI tests assert grouped evidence labels and absence of raw storage identifiers in rendered state.

## FR-008 and FR-009 / Approval Handoff

Decision: Represent approval-gated remediation through a compact approval state in the remediation read model and add approve/reject controls only when permission and pending state are present.
Evidence: `docs/Tasks/TaskRemediation.md` section 15.6 requires proposed action, preconditions, blast radius, approve/reject, and audit trail; task detail timeline already handles approval row types generically.
Rationale: Approval controls must be explicit and permission-aware; read-only state is safer than showing disabled controls without context.
Alternatives considered: Encoding approvals only as timeline events was rejected because operators need a stable current-state panel and decision controls.
Test implications: API tests for permission/pending/read-only states and UI tests for approve, reject, and unauthorized read-only rendering.

## FR-010 / Degraded States

Decision: Treat missing links, missing context refs, unavailable live follow, partial artifact refs, and approval read failures as explicit degraded states inside the remediation panels.
Evidence: `docs/Tasks/TaskRemediation.md` sections 16.3 through 16.5 require partial-evidence and live-follow fallback behavior.
Rationale: Remediation is often used when systems are already failing; Mission Control must remain useful when evidence is incomplete.
Alternatives considered: Hiding panels until complete data exists was rejected because it obscures troubleshooting state.
Test implications: UI tests for empty/degraded copy and preserved task detail rendering.

## FR-011 and FR-012 / Mission Control Integration

Decision: Reuse existing Mission Control matte evidence region styling and add focused regression tests for remediation panels.
Evidence: `specs/223-accessibility-performance-fallbacks` added contrast, focus, reduced-motion, fallback, and dense evidence-region posture; `frontend/src/styles/mission-control.css` contains task-detail evidence-region selectors.
Rationale: Remediation panels are dense operational surfaces and should follow the existing task-detail composition rather than introducing new premium effects.
Alternatives considered: Creating a visually distinct remediation page treatment was rejected as unnecessary scope and likely to regress dense evidence readability.
Test implications: UI tests for focusable controls, containment, degraded states, and non-remediation route regressions.
