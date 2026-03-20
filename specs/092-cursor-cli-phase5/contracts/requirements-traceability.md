# Requirements Traceability Matrix — Cursor CLI Phase 5

| DOC-REQ | Functional Req | Implementation Surface | Validation |
|---------|---------------|----------------------|------------|
| DOC-REQ-P5-001 | FR-008, FR-009 | Already done in Phase 2 (17 tests) | ✅ Complete |
| DOC-REQ-P5-002 | — | Deferred (requires full Docker stack) | research.md R5 |
| DOC-REQ-P5-003 | FR-001 through FR-003 | `ndjson_parser.py` `detect_rate_limit()` | `test_ndjson_parser.py` |
| DOC-REQ-P5-004 | FR-004 through FR-006 | `process_control.py` `cancel_managed_process()` | `test_process_control.py` |
| DOC-REQ-P5-005 | FR-007 | `dashboard.js` `TASK_RUNTIME_LABELS` | Visual + unit test |
