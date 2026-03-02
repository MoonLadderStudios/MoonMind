# Requirements Traceability: Manifest Queue Alignment and Hardening

## DOC-REQ to FR Coverage

| DOC-REQ | Functional Requirement Mapping | Planned Implementation Surfaces | Validation Strategy |
|---------|--------------------------------|----------------------------------|---------------------|
| `DOC-REQ-001` (`docs/ManifestTaskSystem.md` §6.4) | `FR-002`, `FR-003` | `api_service/api/schemas.py` (`ManifestRunRequest._validate_action`), `api_service/api/routers/manifests.py` request handling with normalized action pass-through | Unit tests in `tests/unit/api/test_manifest_run_request_schema.py` for defaulting/normalization/rejection; router-path tests in `tests/unit/api/routers/test_manifests.py` |
| `DOC-REQ-002` (`.specify/memory/constitution.md` Principle VII) | `FR-001` | `specs/028-manifest-queue/spec.md`, `plan.md`, `tasks.md`, `research.md`, `data-model.md`, `contracts/manifests-api.md`, `quickstart.md` | Cross-artifact consistency review ensuring paths/contracts/behaviors match runtime code and test locations |
| `DOC-REQ-003` (`AGENTS.md` testing instructions) | `FR-005` | `tools/test_unit.sh`, manifest-related unit tests under `tests/unit/api/**` and `tests/unit/workflows/agent_queue/**` | Execute unit validation through `./tools/test_unit.sh` and confirm manifest-scope tests pass |
| `DOC-REQ-004` (runtime scope guard from feature input) | `FR-005`, `FR-006` | Runtime behavior enforcement in `api_service/api/schemas.py`; regression tests in `tests/unit/api/test_manifest_run_request_schema.py` | Verify deliverables include production runtime code + validation tests; reject docs-only completion for this feature |
| `DOC-REQ-005` (`docs/ManifestTaskSystem.md` §6.6) | `FR-004` | `moonmind/workflows/agent_queue/manifest_contract.py`, `api_service/services/manifests_service.py`, queue metadata serialization in manifests API | Regression coverage in `tests/unit/workflows/agent_queue/test_manifest_contract.py` and router/service tests confirming metadata compatibility |

## Coverage Result

All `DOC-REQ-*` items in `spec.md` are mapped to at least one functional requirement and include both implementation surfaces and validation strategy.
Coverage check on 2026-03-02 confirms 5/5 `DOC-REQ-*` entries (`DOC-REQ-001`..`DOC-REQ-005`) have corresponding table rows and non-empty validation strategy values.
