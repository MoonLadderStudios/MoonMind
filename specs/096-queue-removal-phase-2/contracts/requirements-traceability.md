| Requirement ID | Functional Requirements | Planned Implementation Surfaces | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001 | `api_service/api/routers/task_dashboard_view_model.py` (`build_runtime_config`) | Validate `sources.queue` is absent from config API payload |
| DOC-REQ-002 | FR-001 | `api_service/api/routers/task_dashboard_view_model.py` (`build_runtime_config`) | Validate `sources.manifests` maps only to Temporal APIs |
| DOC-REQ-003 | FR-001 | `api_service/api/routers/task_dashboard_view_model.py` (`_STATUS_MAPS`) | Unit test `_STATUS_MAPS` block to confirm only `proposals` and `temporal` exist |
| DOC-REQ-004 | FR-002 | `api_service/api/routers/task_dashboard_view_model.py` (`normalize_status`) | Unit tests for status normalization cover Temporal state sets only |
| DOC-REQ-005 | FR-003 | `web/static/js/dashboard.js` | UI test / code review confirms `orchestratorDetailMatch` and validation routines are gone |
| DOC-REQ-006 | FR-004 | `web/static/js/dashboard.js` | UI test / code review confirms polling loops only fetch Temporal tasks |
| DOC-REQ-007 | FR-005 | `api_service/api/routers/task_compatibility.py` | Unit test execution endpoints ignore or deprecate `source` param |
