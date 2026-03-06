# Research: Temporal Artifact Presentation Contract

## Decision 1: Keep `/tasks/:taskId` as the canonical Temporal detail route

- **Decision**: Temporal-backed detail stays on the existing task route, with `taskId == workflowId` for Temporal records.
- **Rationale**: This preserves the product's task-oriented surface, keeps reruns/Continue-As-New stable, and matches the compatibility rules already documented for Temporal-backed work.
- **Alternatives considered**:
  - Introduce `/tasks/temporal/:workflowId` as the primary route: rejected because it creates a separate user-facing namespace and weakens compatibility guarantees.
  - Key detail pages by `temporalRunId`: rejected because run IDs are not stable across reruns.

## Decision 2: Fetch execution detail before artifacts and derive latest-run scope from detail

- **Decision**: The dashboard must fetch execution detail first and use the latest `temporalRunId` returned there when listing artifacts.
- **Rationale**: This prevents stale list-row metadata from leaking into detail, keeps default artifact scope aligned with the newest run, and satisfies the latest-run-only requirement.
- **Alternatives considered**:
  - Reuse a cached `temporalRunId` from the list row: rejected because it can point at an older run after rerun/Continue-As-New.
  - Merge artifacts from all runs by default: rejected because it silently mixes prior-run evidence into the main view.

## Decision 3: Implement artifact presentation normalization in the existing dashboard runtime

- **Decision**: Keep artifact presentation logic in `api_service/static/task_dashboard/dashboard.js`, backed by server-provided endpoint templates and artifact metadata.
- **Rationale**: The current dashboard is already a thin server-hosted web app with client-side source adapters. This keeps presentation logic close to the rendered UI without inventing a separate server-side view model layer just for Temporal artifacts.
- **Alternatives considered**:
  - Build a separate server-rendered Temporal detail page: rejected because it would duplicate the dashboard shell and source-adapter model.
  - Push all presentation shaping into the API: rejected because the dashboard still needs source-specific rendering logic and action handling.

## Decision 4: Prefer preview and respect access-policy metadata

- **Decision**: Artifact actions should prefer `preview_artifact_ref`, use `default_read_ref` for display metadata, and allow raw download only when `raw_access_allowed` permits it.
- **Rationale**: This aligns with the redaction and presigned-access design, reduces unsafe inline assumptions, and preserves MoonMind-controlled access flows.
- **Alternatives considered**:
  - Always show raw download when an artifact exists: rejected because restricted raw content must not be exposed by default.
  - Inline raw content whenever the MIME type looks renderable: rejected because safety depends on policy metadata, not MIME type alone.

## Decision 5: Keep the primary Temporal detail experience summary-and-artifacts first

- **Decision**: The default Temporal detail page should show synthesized status, summary, waiting context, timeline, and artifacts; raw Temporal history JSON is not part of the default UX.
- **Rationale**: The product surface remains task-oriented and operators need durable evidence and state summaries more than workflow-engine internals.
- **Alternatives considered**:
  - Surface raw history JSON in the main detail body: rejected because it leaks Temporal internals into the primary experience.
  - Hide advanced execution metadata entirely: rejected because secondary debug metadata is still useful when kept subordinate to the task view.

## Decision 6: Keep actions and submit behavior task-oriented and config-gated

- **Decision**: Temporal detail should continue using task-facing labels and route stability, while action and submit affordances remain governed by runtime config/feature flags.
- **Rationale**: This lets the dashboard evolve incrementally without exposing Temporal as a worker runtime or leaking implementation jargon into normal workflows.
- **Alternatives considered**:
  - Add `temporal` to the runtime selector: rejected because Temporal is an orchestration substrate, not a worker runtime.
  - Enable all Temporal actions unconditionally: rejected because rollout posture already uses feature flags for staged support.

## Decision 7: Validation must span both Python route/config checks and Node dashboard-runtime checks

- **Decision**: Use `./tools/test_unit.sh` as the acceptance path, covering both Python unit tests and Node-based dashboard runtime tests.
- **Rationale**: The feature crosses FastAPI route/runtime config surfaces and browser-side rendering logic; either layer can regress route/run-scope behavior.
- **Alternatives considered**:
  - Test only Python APIs: rejected because artifact presentation and fetch ordering live in dashboard JavaScript.
  - Test only manual browser flows: rejected because runtime behavior needs repeatable automated coverage.

## Decision 8: Runtime-vs-docs mode stays explicitly aligned to runtime implementation

- **Decision**: Treat runtime implementation mode as authoritative for this feature and document docs-mode behavior only as a scope-check reference.
- **Rationale**: The task objective explicitly requires production runtime code changes plus validation tests; planning artifacts must not leave a docs-only completion path ambiguous.
- **Alternatives considered**:
  - Allow docs/spec output to satisfy the feature: rejected because it conflicts with the task objective and FR-001/FR-002.
  - Omit mode language from planning artifacts: rejected because downstream tasks and validation gates would drift.
