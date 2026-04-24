# Requirements Traceability: Phase 6

| DOC-REQ ID | Function Req | Implementation Surface | Validation Strategy |
|---|---|---|---|
| DOC-REQ-001 | FR4 | `ui/src/components/` | Verify historical tasks gracefully degrade |
| DOC-REQ-002 | FR1 | `moonmind/schemas/` | Confirm `TaskRunLiveSession` gets removed or aggressively marked deprecated |
| DOC-REQ-003 | FR3 | `api_service/api/` | Test verifying that any socket handlers return errors or 404s for managed agents |
| DOC-REQ-004 | FR2 | `moonmind/agents/managed/` | Integration tests verifying process launcher does not attempt to bind any live server hooks for legacy sessions |
| DOC-REQ-005 | FR1 | `ui/src/hooks/` and `api_service/` | Ensure no UI code attempts to query `web_ro` metadata objects |
| DOC-REQ-006 | FR6 | `docs/` | Read manual to ensure terminal assumptions are wiped out |
| DOC-REQ-007 | FR6 | `local-only handoffs` | Release log updates mention operator cut-offs |
| DOC-REQ-008 | FR5 | `tests/integration/` | Check legacy compatibility via unit tests with mock payloads |
