# Requirements Traceability: Jules Question Auto-Answer

| DOC-REQ | Functional Requirement(s) | Implementation Surface | Validation Strategy |
|---------|--------------------------|----------------------|---------------------|
| DOC-REQ-001 | FR-001, FR-002 | `jules_models.py`, `jules/status.py` | Unit test: verify `awaiting_user_feedback` normalizes to `awaiting_feedback` |
| DOC-REQ-002 | FR-003, FR-004 | `jules_client.py`, `jules_activities.py` | Unit test: mock Activities API response, verify question extraction |
| DOC-REQ-003 | FR-005 | `jules_activities.py` | Unit test: mock LLM dispatch, verify prompt construction and answer routing |
| DOC-REQ-004 | FR-013 | `agent_run.py`, `run.py` | Unit test: verify `sendMessage` activity called with LLM answer |
| DOC-REQ-005 | FR-007, FR-008 | `agent_run.py`, `run.py` | Unit test: mock status poll returning `awaiting_feedback`, verify sub-flow triggers |
| DOC-REQ-006 | FR-009 | `agent_run.py`, `run.py` | Unit test: exceed max cycles, verify `intervention_requested` |
| DOC-REQ-007 | FR-010 | `agent_run.py`, `run.py` | Unit test: duplicate activity ID, verify no second LLM call |
| DOC-REQ-008 | FR-011 | `agent_run.py`, `run.py` | Unit test: `JULES_AUTO_ANSWER_ENABLED=false`, verify no LLM call |
| DOC-REQ-009 | FR-012 | `agent_run.py`, `run.py` | Unit test: verify env var reads, default values |
| DOC-REQ-010 | FR-006 | `jules_models.py` | Unit test: model instantiation and serialization |
| DOC-REQ-011 | FR-004 | `jules_activities.py`, `activity_catalog.py`, `activity_runtime.py` | Unit test: activity registration and execution |
| DOC-REQ-012 | FR-005 | `jules_activities.py`, `activity_catalog.py`, `activity_runtime.py` | Unit test: activity registration and execution |
