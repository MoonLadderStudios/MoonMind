# Requirements Traceability: TmateSessionManager

**Feature**: 104-tmate-session-manager

| DOC-REQ | FR ID(s) | Implementation Surface | Validation Strategy |
|---|---|---|---|
| DOC-REQ-001 | FR-001 | `moonmind/workflows/temporal/runtime/tmate_session.py` (new file) | File exists at expected path; class importable |
| DOC-REQ-002 | FR-002 | `TmateEndpoints` dataclass in `tmate_session.py` | Unit test asserting all 6 fields present and typed |
| DOC-REQ-003 | FR-004, FR-005, FR-006, FR-016, FR-017 | `TmateSessionManager` class with `is_available()`, `start()`, `teardown()`, `endpoints`, `exit_code_path` | Unit tests for each method and property |
| DOC-REQ-004 | FR-005 | `TmateSessionManager.start()` implementation | Unit tests: socket dir creation, config write, subprocess launch, readiness wait, endpoint extraction |
| DOC-REQ-005 | FR-003, FR-008 | `TmateServerConfig` dataclass + config generation in `start()` | Unit test: config file contains `set-option` directives when server config provided |
| DOC-REQ-006 | FR-008 | Config file writer in `start()` | Unit test: per-session config includes server host/port/fingerprint directives |
| DOC-REQ-007 | FR-010 | `launcher.py` refactored to call `TmateSessionManager.start()` | Existing launcher tests pass; diff review confirms inline tmate logic removed |
| DOC-REQ-008 | FR-011 | `oauth_session_activities.py` refactored to use shared endpoint patterns | OAuth activity tests pass; shared constants used for endpoint keys |
| DOC-REQ-009 | FR-013 | Lifecycle documented; manager tracks internal state | Unit test: start/teardown transitions verified |
| DOC-REQ-010 | FR-007 | `teardown()` implementation | Unit test: socket, config, exit-code files removed after teardown |
| DOC-REQ-011 | FR-012 | Both `launcher.py` and `oauth_session_activities.py` import from `tmate_session.py` | Code review: no remaining inline tmate lifecycle logic in consumers |
| DOC-REQ-012 | FR-006 | `start()` signature with optional parameters | Unit test: `start()` accepts command, env, cwd, exit_code_capture, timeout_seconds |
| DOC-REQ-013 | FR-009 | `TmateServerConfig.from_env()` factory | Unit test: factory reads correct env vars; missing vars handled gracefully |
