# Requirements Traceability: Temporal Artifact Presentation Contract

| DOC-REQ ID | Source Reference | Mapped FR(s) | Planned Implementation Surfaces | Validation Strategy |
|---|---|---|---|---|
| DOC-REQ-001 | `docs/UI/TemporalDashboardIntegration.md` section 9.2 | FR-003 | `api_service/api/routers/task_dashboard.py`, `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/task_dashboard_view_model.py` | Node dashboard-runtime tests verify latest-run artifact request resolution; Python unit tests verify route shell/runtime config contract |
| DOC-REQ-002 | `docs/UI/TemporalDashboardIntegration.md` section 9.3 | FR-004 | `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/task_dashboard_view_model.py` | Dashboard runtime tests and targeted UI assertions verify task-oriented header fields remain primary while debug metadata is secondary |
| DOC-REQ-003 | `docs/UI/TemporalDashboardIntegration.md` section 9.4 | FR-005 | `api_service/static/task_dashboard/dashboard.js` | Dashboard runtime tests verify synthesized timeline/waiting-state rendering and absence of raw-history-first behavior in the default view |
| DOC-REQ-004 | `docs/UI/TemporalDashboardIntegration.md` sections 10.1-10.3 | FR-006 | `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/task_dashboard_view_model.py`, `api_service/api/routers/executions.py` | Python unit tests plus dashboard action-surface tests verify task-facing action mapping and state-gated controls |
| DOC-REQ-005 | `docs/UI/TemporalDashboardIntegration.md` sections 11.1-11.4 | FR-007 | `api_service/api/routers/task_dashboard.py`, `api_service/api/routers/task_dashboard_view_model.py`, `api_service/static/task_dashboard/dashboard.js` | Route-shell tests and dashboard runtime tests verify canonical task routing stays stable and no Temporal runtime selector is introduced |
| DOC-REQ-006 | `docs/UI/TemporalDashboardIntegration.md` sections 12.1-12.2 | FR-008, FR-013 | `api_service/api/routers/task_dashboard_view_model.py`, `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/temporal_artifacts.py`, `moonmind/schemas/temporal_artifact_models.py` | `tests/task_dashboard/test_temporal_detail_runtime.js` and `tests/unit/api/routers/test_temporal_artifacts.py` verify artifact metadata, preview/download wiring, and execution-scoped artifact access behavior |
| DOC-REQ-007 | `docs/UI/TemporalDashboardIntegration.md` section 12.3 | FR-009, FR-010, FR-011 | `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/temporal_artifacts.py`, `moonmind/schemas/temporal_artifact_models.py` | `tests/task_dashboard/test_temporal_detail_runtime.js` and `tests/unit/api/routers/test_temporal_artifacts.py` verify preview-first, raw-restricted, and no-safe-preview behavior plus policy metadata availability |
| DOC-REQ-008 | `docs/UI/TemporalDashboardIntegration.md` section 12.4 | FR-012 | `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/task_dashboard.py` | Dashboard runtime tests verify default artifact scope follows the latest run only and does not mix prior-run artifacts |
| DOC-REQ-009 | `docs/Temporal/WorkflowArtifactSystemDesign.md` sections 12.2-12.3 | FR-009, FR-010, FR-013 | `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/temporal_artifacts.py`, `moonmind/schemas/temporal_artifact_models.py` | `tests/task_dashboard/test_temporal_detail_runtime.js` and `tests/unit/api/routers/test_temporal_artifacts.py` verify presigned access usage, preview preference, and restricted raw handling |
| DOC-REQ-010 | Runtime scope guard from task objective | FR-001, FR-002 | `api_service/api/routers/task_dashboard.py`, `api_service/api/routers/task_dashboard_view_model.py`, `api_service/api/routers/temporal_artifacts.py`, `api_service/static/task_dashboard/dashboard.js`, `moonmind/schemas/temporal_artifact_models.py`, `tests/unit/api/routers/test_task_dashboard.py`, `tests/unit/api/routers/test_task_dashboard_view_model.py`, `tests/unit/api/routers/test_temporal_artifacts.py`, `tests/task_dashboard/test_temporal_detail_runtime.js` | `./tools/test_unit.sh`, `tests/unit/specs/test_doc_req_traceability.py`, and runtime scope validation ensure production runtime paths, automated validation tests, and `DOC-REQ-*` mappings remain present |

## Runtime Mode Alignment Gate

- Selected orchestration mode: **runtime implementation mode**.
- Required implementation scope includes production runtime dashboard/router code and automated validation tests.
- Docs-mode scope behavior remains documented only for consistency; it cannot satisfy this feature's completion gate.

## Coverage Gate

- Source requirements tracked: **10** (`DOC-REQ-001` through `DOC-REQ-010`).
- Every requirement maps to FR coverage, planned implementation surfaces, and explicit validation strategy.
- Planning fails if any `DOC-REQ-*` item becomes unmapped or loses planned validation coverage.
