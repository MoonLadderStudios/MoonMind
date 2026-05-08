# Quickstart: Bounded Remediation Evidence Context

## Prerequisites

- Python 3.12 environment for backend tests.
- Node/npm dependencies prepared by `./tools/test_unit.sh` when frontend tests are enabled.
- No external credentials are required for the planned unit and hermetic integration tests.

## Test-First Flow

1. Confirm the active feature:

   ```bash
   sed -n '1,40p' .specify/feature.json
   ```

2. Add or update focused backend unit tests first:

   ```bash
   ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py
   ```

   Expected red-first targets:
   - context builder resolves observability/log/diagnostic/provider refs into `evidence.taskRuns`;
   - context builder records unavailable evidence classes and degraded historical targets;
   - live-follow state reports `active`, `unavailable`, `unsupported`, or `policy_denied`;
   - remediation log reader adapter reads only task runs declared by context;
   - `prepare_action_request` continues to reread target health before side effects.

3. Add or update API/router unit tests where serialization or permissions change:

   ```bash
   ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/api/routers/test_task_runs.py
   ```

4. Add or update Mission Control tests for evidence presentation:

   ```bash
   ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx
   ```

   Expected red-first targets:
   - all remediation evidence artifact classes are discoverable from the remediation panel;
   - live observation state is distinct from durable fallback evidence;
   - degraded evidence messages remain visible and accessible.

5. Implement the smallest backend/UI changes needed to pass the focused tests.

6. Run the full unit suite:

   ```bash
   ./tools/test_unit.sh
   ```

7. Add hermetic integration coverage for the full evidence path when code changes cross service boundaries:

   ```bash
   ./tools/test_integration.sh
   ```

   Integration proof should cover:
   - remediation task creation with a target execution;
   - context artifact publication and `context_artifact_ref` linkage before diagnostic work;
   - typed read of declared target artifacts/logs through server-mediated surfaces;
   - live-follow unavailable fallback to durable logs/artifacts;
   - no presigned URLs, storage keys, local paths, or secrets in context payload.

## End-to-End Validation

For a seeded or test-created target execution:

1. Create a remediation task with `mode = snapshot_then_follow`.
2. Confirm the remediation link has `contextArtifactRef`.
3. Download/read the linked `remediation.context` artifact through the artifact API.
4. Confirm the payload includes target identity, selected task-run evidence, policy snapshots, availability records, and live-follow state.
5. Confirm the payload excludes raw log bodies, storage paths, presigned URLs, and secrets.
6. Confirm declared logs/artifacts can be read through typed remediation evidence surfaces.
7. Confirm an undeclared artifact or task run is rejected.
8. Confirm Mission Control renders the context, evidence classes, degraded state, and live-follow/fallback messaging.

## Requirement Status Reflection

- `missing`: none identified.
- `partial`: FR-002, FR-006 through FR-012, scenarios involving live follow/degraded evidence/presentation, SC-001, SC-003 through SC-005, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-025.
- `implemented_verified`: FR-001, FR-003, FR-004, FR-005, FR-013, FR-014, SC-002, SC-006, DESIGN-REQ-009.

Do not proceed to `/speckit.tasks` until `plan.md`, `research.md`, `data-model.md`, this quickstart, and `contracts/remediation-evidence.md` remain mutually consistent.
