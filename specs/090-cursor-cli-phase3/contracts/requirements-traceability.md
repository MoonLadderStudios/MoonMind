# Requirements Traceability Matrix — Cursor CLI Phase 3

| DOC-REQ | Functional Req | Implementation Surface | Validation |
|---------|---------------|----------------------|------------|
| DOC-REQ-P3-001 | FR-001, FR-002 | `api_service/migrations/versions/seed_cursor_cli_auth_profile.py` (NEW) | Migration SQL validation |
| DOC-REQ-P3-002 | FR-003, FR-004 | No code changes — existing `auth_profile.ensure_manager` is runtime_id agnostic | research.md R1 |
| DOC-REQ-P3-003 | — | Phase 1 (spec 088) already complete | research.md R2 |
