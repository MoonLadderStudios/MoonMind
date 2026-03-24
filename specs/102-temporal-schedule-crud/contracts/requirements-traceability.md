# Requirements Traceability: Temporal Schedule CRUD

| DOC-REQ | Requirement Summary | FR IDs | Implementation Surface | Validation Strategy |
|---|---|---|---|---|
| DOC-REQ-001 | Create Temporal Schedule via SDK | FR-001 | `client.py:create_schedule()` | Unit test: mock client, verify `client.create_schedule()` called with correct `Schedule` object |
| DOC-REQ-002 | Support describe, update, pause, unpause, trigger, delete | FR-002, FR-003, FR-004, FR-005, FR-006 | `client.py:describe_schedule()`, `update_schedule()`, `pause_schedule()`, `unpause_schedule()`, `trigger_schedule()`, `delete_schedule()` | Unit tests per method: mock `ScheduleHandle`, verify correct SDK calls |
| DOC-REQ-003 | Map overlap policy modes to `ScheduleOverlapPolicy` | FR-007 | `schedule_mapping.py:map_overlap_policy()` | Unit tests: verify each mode maps to the correct enum value |
| DOC-REQ-004 | Map catchup modes to `catchup_window` durations | FR-008 | `schedule_mapping.py:map_catchup_window()` | Unit tests: verify each mode maps to the correct `timedelta` |
| DOC-REQ-005 | Schedule ID convention `mm-schedule:{uuid}` | FR-009, FR-010 | `schedule_mapping.py:make_schedule_id()`, `make_workflow_id_template()` | Unit tests: verify format, UUID roundtrip |
| DOC-REQ-006 | Map jitterSeconds to `ScheduleSpec.jitter` | FR-011 | `schedule_mapping.py:build_schedule_spec()` | Unit test: verify `jitter` field in returned `ScheduleSpec` |
| DOC-REQ-007 | Wrap SDK exceptions in adapter-level types | FR-012 | `schedule_errors.py`, `client.py` (catch blocks) | Unit tests: mock SDK to raise, verify adapter exception type |
