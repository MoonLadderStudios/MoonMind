# Implementation Plan: Durable Task Edit Reconstruction

**Branch**: `180-durable-task-edit-reconstruction` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/specs/180-durable-task-edit-reconstruction/spec.md`

## Summary

Implement a durable original task input snapshot artifact for `MoonMind.Run` create, edit, and rerun flows. The create path records the operator-submitted create-form draft before backend normalization or planner synthesis; execution detail exposes a compact reconstruction descriptor; the frontend reconstructs `/tasks/new` edit/rerun drafts from the snapshot first and treats plan artifacts only as degraded read-only recovery assistance. Validation is test-first across frontend reconstruction helpers, API/contract serialization, artifact linkage, and Temporal workflow/update boundaries.

This is not safe to implement in the same run as the design work. It touches API request normalization, artifact persistence, execution detail contracts, frontend reconstruction, and Temporal update/rerun boundaries, and it requires coordinated tests before runtime code changes.

## Technical Context

**Language/Version**: Python 3.12, TypeScript/React
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy, Temporal Python SDK, pytest, Vitest, existing Temporal artifact service and task editing UI
**Storage**: Existing immutable artifact store and artifact index plus existing execution canonical records; no new database table is planned unless implementation discovers link metadata cannot support the required query efficiently
**Unit Testing**: `./tools/test_unit.sh` for Python unit tests; `npm run ui:test -- <path>` or `./tools/test_unit.sh --ui-args <path>` for frontend unit tests
**Integration Testing**: `./tools/test_integration.sh` for hermetic integration; focused API/Temporal boundary tests through pytest during iteration
**Target Platform**: Docker Compose and managed-agent worker containers
**Project Type**: Temporal-backed orchestration service with FastAPI control plane and React Mission Control frontend
**Performance Goals**: Snapshot creation adds bounded submit latency; execution detail remains compact by returning refs and descriptors, not large snapshot payloads
**Constraints**: Preserve original input versus derived planner/runtime state; keep large content out of workflow history; do not mutate checked-in skills or runtime snapshots; no hidden compatibility transforms; payload boundary changes require tests or an explicit cutover plan
**Scale/Scope**: One snapshot per create/edit/rerun submission event for `MoonMind.Run`; reconstruction covers current `/tasks/new` fields and excludes schedule controls from edit/rerun

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan persists operator input and keeps agent behavior in existing runtime/planner systems.
- **II. One-Click Agent Deployment**: PASS. The feature uses existing API, artifact, and Temporal services.
- **III. Avoid Vendor Lock-In**: PASS. The snapshot is provider-neutral JSON with runtime-specific selections stored as data.
- **IV. Own Your Data**: PASS. Original operator input and refs are stored in MoonMind-controlled artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. Selected agent skills and resolved skillset refs are captured as data without mutating skill folders.
- **VI. Design for Deletion / Scientific Method**: PASS. The snapshot is a narrow evidence artifact with tests, not a planner replacement.
- **VII. Powerful Runtime Configurability**: PASS. Runtime/profile/model/effort values are preserved exactly as operator input where required.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay at create normalization, artifact service, execution detail API, frontend reconstruction, and workflow/update boundaries.
- **IX. Resilient by Default**: PASS. Durable snapshots make edit/rerun recoverable after worker restarts; boundary tests cover refs and cutover behavior.
- **X. Facilitate Continuous Improvement**: PASS. Disabled reasons and degraded warnings are operator-visible.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Runtime work is broken down under this spec before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. The implementation backlog stays in this spec and contracts, not canonical docs.
- **XIII. Pre-Release Compatibility Policy**: PASS. The design avoids broad compatibility shims and uses a clear cutover for pre-snapshot executions.

## Project Structure

### Documentation (this feature)

```text
specs/180-durable-task-edit-reconstruction/
+-- spec.md
+-- plan.md
+-- research.md
+-- data-model.md
+-- quickstart.md
+-- contracts/
|   +-- original-task-input-snapshot.md
+-- checklists/
|   +-- requirements.md
+-- tasks.md
```

### Source Code (repository root)

```text
api_service/
+-- api/routers/executions.py              # create snapshot, expose descriptor, update/rerun validation
+-- services/                              # artifact creation/link helpers if split from router

moonmind/
+-- schemas/temporal_models.py             # execution detail descriptor and update payload schema
+-- schemas/temporal_artifact_models.py    # artifact metadata/link constants if centralized
+-- workflows/temporal/
    +-- service.py                         # persist compact refs on create/update/rerun records
    +-- workflows/run.py                   # carry compact refs only where workflow input/update payloads change

frontend/src/
+-- lib/temporalTaskEditing.ts             # snapshot-first reconstruction and degraded-source classification
+-- entrypoints/task-create.tsx            # read descriptor/ref, block unsafe submits, create rerun snapshots

tests/
+-- contract/test_temporal_execution_api.py
+-- unit/workflows/temporal/
+-- integration/workflows/temporal/
+-- frontend via frontend/src/entrypoints/task-create.test.tsx
```

**Structure Decision**: Use immutable artifacts as the canonical source for reconstruction and add only compact refs/descriptors to execution records and API responses. This preserves artifact-first Temporal discipline and avoids embedding large draft content in workflow history.

## Complexity Tracking

No constitution violations.
