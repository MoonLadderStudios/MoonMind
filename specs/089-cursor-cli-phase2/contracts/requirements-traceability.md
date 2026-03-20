# Requirements Traceability Matrix — Cursor CLI Phase 2

| DOC-REQ | Functional Req | Implementation Surface | Validation |
|---------|---------------|----------------------|------------|
| DOC-REQ-P2-001 | FR-001 | `moonmind/agents/base/adapter.py` → `resolve_volume_mount_env()` | `tests/unit/agents/base/test_adapter.py::test_resolve_volume_mount_env_cursor` |
| DOC-REQ-P2-002 | FR-002, FR-003 | `moonmind/agents/base/adapter.py` → `shape_agent_environment()` | `tests/unit/agents/base/test_adapter.py::test_shape_agent_environment_oauth_includes_cursor_key` |
| DOC-REQ-P2-003 | FR-004, FR-005, FR-006, FR-007 | `moonmind/workflows/temporal/runtime/launcher.py` → `build_command()` | `tests/unit/services/temporal/runtime/test_launcher.py::test_build_command_cursor_cli*` |
| DOC-REQ-P2-004 | FR-008, FR-009, FR-010 | `moonmind/agents/base/ndjson_parser.py` (NEW) | `tests/unit/agents/base/test_ndjson_parser.py` |
| DOC-REQ-P2-005 | FR-011 | No code change — `cursor_cli` dispatches via existing `agent_runtime` fleet/profile system | Research.md R1 documents rationale |
