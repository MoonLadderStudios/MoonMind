# Research: Temporal Dashboard Integration

## Decision 1: Temporal is a dashboard source, not a worker runtime

- **Decision**: Keep Temporal modeled as `source=temporal` inside the dashboard while leaving the existing worker runtime picker limited to worker runtimes such as `codex`, `gemini`, `claude`, `jules`, and `universal`.
- **Rationale**: This preserves the current task-oriented submit contract, satisfies `DOC-REQ-002` and `DOC-REQ-003`, and avoids conflating execution engine selection with worker-runtime selection.
- **Alternatives considered**:
  - Add `temporal` to the runtime picker: rejected because it breaks the documented compatibility model and would mislead operators about what the runtime control means.
  - Create a separate Temporal-only dashboard product surface: rejected because the source doc explicitly keeps `/tasks*` as the primary product contract.

## Decision 2: Temporal rollout flags must be settings-backed, not hardcoded

- **Decision**: Move Temporal dashboard feature toggles behind runtime settings and export them through `build_runtime_config()` as `features.temporalDashboard`.
- **Rationale**: This satisfies `DOC-REQ-004` and `DOC-REQ-006`, aligns with the constitution's runtime configurability rule, and makes phased rollout reversible without code edits.
- **Alternatives considered**:
  - Keep booleans hardcoded in `task_dashboard_view_model.py`: rejected because rollout would not be operator-configurable.
  - Hide flags entirely in the client: rejected because the server must remain the source of truth for exposed behavior.

## Decision 3: Canonical detail routing stays `/tasks/:taskId`

- **Decision**: Keep `/tasks/:taskId` as the canonical detail route, treat `taskId == workflowId` for Temporal-backed work, and use `GET /api/tasks/{taskId}/source` as the documented source-resolution contract.
- **Rationale**: This satisfies `DOC-REQ-007`, `DOC-REQ-012`, and `DOC-REQ-017` while preserving the current route family used across dashboard sources.
- **Alternatives considered**:
  - Introduce `/tasks/executions/:workflowId` as the new primary route: rejected because it fractures the task-oriented product contract during migration.
  - Depend on identifier-shape probing alone: rejected because source resolution must be explicit and durable.

## Decision 4: Mixed-source and Temporal-only list modes have different guarantees

- **Decision**: Keep mixed-source list mode as a bounded convenience view and preserve authoritative `/api/executions` pagination, `nextPageToken`, and `count/countMode` semantics only when `source=temporal`.
- **Rationale**: This matches `DOC-REQ-008`, `DOC-REQ-009`, and `DOC-REQ-010` and avoids inventing fake global pagination across heterogeneous backends.
- **Alternatives considered**:
  - Present a globally paginated merged list: rejected because no single backend owns that dataset.
  - Hide Temporal from mixed-source mode entirely: rejected because the product goal is one dashboard surface during migration.

## Decision 5: Normalize Temporal rows into task-oriented fields while preserving raw metadata

- **Decision**: Temporal list/detail data must expose normalized task-oriented fields and preserve raw Temporal lifecycle metadata including `rawState`, `temporalStatus`, `closeStatus`, `waitingReason`, and `attentionRequired`.
- **Rationale**: This satisfies `DOC-REQ-001`, `DOC-REQ-005`, `DOC-REQ-011`, and `DOC-REQ-013`, letting the dashboard remain task-first without throwing away diagnostic truth.
- **Alternatives considered**:
  - Show only raw Temporal states: rejected because it violates task-oriented UX.
  - Collapse to compatibility status only: rejected because operators need exact state/debug data to understand blocked or terminal executions.

## Decision 6: Detail fetch sequencing must be execution-first, artifacts-second

- **Decision**: Temporal detail loads must first fetch `GET /api/executions/{workflowId}` and only then fetch `GET /api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts` using the latest run ID from the detail response.
- **Rationale**: This satisfies `DOC-REQ-012` and `DOC-REQ-016`, and avoids stale artifact lookups after rerun or Continue-As-New behavior.
- **Alternatives considered**:
  - Fetch artifacts using a run ID cached from list rows: rejected because reruns can invalidate that run ID.
  - Show mixed-run artifacts by default: rejected because the source doc makes latest-run scope the v1 default.

## Decision 7: Temporal actions are state-aware and feature-flagged

- **Decision**: Keep action visibility gated by rollout flags and current execution state, using the existing execution lifecycle APIs for updates, signals, and cancel requests.
- **Rationale**: This covers `DOC-REQ-006` and `DOC-REQ-014` while avoiding unsupported buttons or unsafe operator actions.
- **Alternatives considered**:
  - Expose all actions whenever Temporal detail is visible: rejected because actions must be state-valid and phased.
  - Defer all action support to later work: rejected because action integration is part of the documented rollout.

## Decision 8: Submit integration stays backend-routed and task-shaped

- **Decision**: Any Temporal-backed submit behavior remains behind backend routing and feature flags, using task-shaped forms plus artifact refs while mapping eligible requests to `POST /api/executions`.
- **Rationale**: This satisfies `DOC-REQ-003` and `DOC-REQ-015` and preserves the current product language and form model.
- **Alternatives considered**:
  - Expose raw workflow start payload fields directly in the dashboard: rejected because `workflowType`, `failurePolicy`, and low-level execution details are backend contract concerns.
  - Create a dedicated Temporal submit screen: rejected because canonical routes remain `/tasks/new` and related task submit flows.

## Decision 9: Artifact presentation must remain inside MoonMind authorization/download flows

- **Decision**: Temporal detail should use MoonMind artifact APIs for metadata, preview, and download behavior and must treat artifacts as immutable references scoped to the latest run by default.
- **Rationale**: This satisfies `DOC-REQ-016` and `DOC-REQ-019` and keeps browser access policy consistent with the rest of the product.
- **Alternatives considered**:
  - Link directly to Temporal or raw object storage URLs: rejected because the browser must not bypass MoonMind authorization.
  - Treat artifacts as editable blobs on the detail page: rejected because the artifact model is immutable-reference based.

## Decision 10: Validation must cover both backend and dashboard-client seams

- **Decision**: Validation will span runtime config tests, route/source-resolution tests, dashboard normalization tests, execution/artifact contract tests, and browser/e2e coverage for redirect/canonical routing behavior.
- **Rationale**: This satisfies `DOC-REQ-018` and protects the thin-dashboard architecture where behavior is split across server and client modules.
- **Alternatives considered**:
  - Rely only on backend unit tests: rejected because list/detail/action behavior is partly implemented in `dashboard.js`.
  - Rely only on browser smoke tests: rejected because failure localization would be weak and contract regressions would be harder to catch.

## Decision 11: Runtime-vs-docs behavior stays explicitly mode-aligned

- **Decision**: Keep this feature in runtime implementation mode and document docs-mode skip semantics only as orchestration hygiene; production code changes plus automated validation remain mandatory.
- **Rationale**: This aligns the planning artifacts with the feature objective and prevents false-complete docs-only delivery.
- **Alternatives considered**:
  - Allow docs-only closure because planning artifacts exist: rejected because the spec explicitly requires runtime code changes and validation tests.
  - Ignore mode semantics in planning: rejected because downstream tasks must inherit the same scope gate.
