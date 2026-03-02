# Requirements Traceability: 015 Skills Workflow Alignment

## DOC-REQ Coverage Matrix

| DOC Requirement | FR Mapping | Implementation Tasks | Validation Tasks | Implementation Surfaces | Validation Strategy |
|-----------------|------------|----------------------|------------------|-------------------------|--------------------|
| DOC-REQ-001 | FR-001 | T003, T010 | T011, T018 | `moonmind/workflows/speckit_celery/tasks.py`, `specs/015-skills-workflow/contracts/skills-stage-contract.md` | Unit tests assert canonical task names and stage-routing behavior; unit suite run via `./tools/test_unit.sh`. |
| DOC-REQ-002 | FR-005 | T013 | T015 | `specs/015-skills-workflow/contracts/compose-fast-path.md`, `specs/015-skills-workflow/contracts/skills-stage-contract.md` | Verify shared-skills contract uses one `skills_active` root with `.agents/skills` + `.gemini/skills` links. |
| DOC-REQ-003 | FR-005 | T014 | T015 | `specs/015-skills-workflow/quickstart.md`, `specs/015-skills-workflow/contracts/compose-fast-path.md` | Verify operator flow requires `./tools/auth-codex-volume.sh` and `./tools/auth-gemini-volume.sh` before startup. |
| DOC-REQ-004 | FR-006 | T004, T009, T012 | T007, T011, T015 | `moonmind/workflows/speckit_celery/tasks.py`, `moonmind/workflows/skills/registry.py`, `moonmind/schemas/workflow_models.py` | Unit tests cover conditional Speckit verification and API projection of normalized metadata. |
| DOC-REQ-005 | FR-002, FR-004 | T004, T008, T009, T010 | T006, T007, T016, T018 | `moonmind/workflows/speckit_celery/models.py`, `moonmind/schemas/workflow_models.py`, `api_service/api/routers/spec_automation.py`, `specs/015-skills-workflow/contracts/spec-automation-api.openapi.yaml` | Unit tests verify `selected_skill`, `adapter_id`, and `execution_path` observability for explicit and normalized payloads. |
| DOC-REQ-006 | FR-003 | T008, T017 | T005, T006, T007, T016, T018 | `moonmind/workflows/speckit_celery/models.py`, `api_service/api/routers/spec_automation.py` | Compatibility tests verify legacy defaults and missing-key behavior for Speckit and non-Speckit phases. |
| DOC-REQ-007 | FR-007 | T003, T008, T009, T012, T017 | T016, T018, T019, T020 | Runtime surfaces under `moonmind/` and `api_service/`; execution tracking in `specs/015-skills-workflow/tasks.md` | Runtime mode guarded by scope checks (`--check diff`, `--check tasks`) plus unit validation execution. |
| DOC-REQ-008 | FR-008 | T022 | T018 | `tools/test_unit.sh`, `specs/015-skills-workflow/quickstart.md`, `specs/015-skills-workflow/tasks.md` | Canonical unit gate executes with `./tools/test_unit.sh` and is documented in quickstart + tasks. |

## Coverage Check

- All `DOC-REQ-001` through `DOC-REQ-008` map to at least one FR.
- Every `DOC-REQ-*` maps to at least one implementation task and at least one validation task.
- Runtime-mode scope guard tasks (`T019`, `T020`) and unit gate task (`T018`) are explicitly tracked.
