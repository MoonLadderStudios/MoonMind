# Requirements Traceability

| DOC-REQ ID | Functional Requirements | Planned Implementation Surfaces | Validation Strategy |
|------------|-------------------------|--------------------------------|---------------------|
| DOC-REQ-001 | FR-001 | `moonmind.agents.base.adapter` | Unit test shaped environment |
| DOC-REQ-002 | FR-002 | `moonmind.workflows.auth_profile.manager` | Unit test singleton initialization |
| DOC-REQ-003 | FR-003 | `moonmind.agents.base.adapter` | Unit test volume path resolution |
| DOC-REQ-004 | FR-002 | `moonmind.workflows.auth_profile.manager` | Unit test singleton ID |
| DOC-REQ-005 | FR-004 | `moonmind.workflows.auth_profile.manager` | Unit test profile selection logic |
| DOC-REQ-006 | FR-005 | `moonmind.workflows.auth_profile.manager` | Unit test queue logic |
| DOC-REQ-007 | FR-006 | `moonmind.workflows.auth_profile.manager` | Unit test cooldown signal handling |
| DOC-REQ-008 | FR-007 | `moonmind.workflows.auth_profile.manager` | Unit test CAN threshold |
| DOC-REQ-009 | FR-003 | `moonmind.agents.base.adapter` | Unit test launcher setup |
| DOC-REQ-010 | FR-008 | `api_service.db.models`, `schemas`, `routers` | Integration test CRUD endpoints |
| DOC-REQ-011 | FR-009 | `moonmind.workflows.auth_profile.manager` | Unit test pure workflow logic |
| DOC-REQ-012 | FR-010 | `moonmind.workflows.auth_profile.manager` | Code review to ensure keys aren't serialized |
