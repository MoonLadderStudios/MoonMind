# Requirements Traceability: Provider Profiles Phase 2

## Traceability Matrix

| Source Requirement | Functional Requirement | Planned Implementation Surface | Validation Strategy |
| --- | --- | --- | --- |
| **DOC-REQ-001** (Table additions) | FR-001 | `moonmind/models/provider_profiles.py` & Alembic | `poetry run pytest tests/` testing that models can be inserted with these fields. |
| **DOC-REQ-002** (CRUD logic) | FR-002, FR-003, FR-004 | `moonmind/services/provider_profile_service.py` & schemas. | Service unit tests validating rejection of bare secrets and checking that schemas enforce structural integrity. |
| **DOC-REQ-003** (OAuth fixes) | FR-005 | `moonmind/services/oauth_session_service.py` & `auth_profile_service.py` legacy code. | Integration testing of OAuth finalizing route verifying database persistence. |
| **DOC-REQ-004** (Migrations) | FR-006 | `moonmind/alembic/versions/...` & `moonmind/models/...` enums. | Alembic tests. Spinning up the db container and running upgrade head. |
