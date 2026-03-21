# Requirements Traceability: 095-managed-runtime-strategy

| DOC-REQ | FR IDs | Implementation Surface | Validation Strategy |
|---|---|---|---|
| DOC-REQ-001 | FR-001, FR-002, FR-003 | `strategies/base.py` — ABC definition | Unit test: ABC cannot be instantiated; concrete subclass must implement abstracts |
| DOC-REQ-002 | FR-004 | `strategies/__init__.py` — `RUNTIME_STRATEGIES` dict | Unit test: registry contains expected entries; lookup returns correct strategy |
| DOC-REQ-003 | FR-005 | `strategies/gemini_cli.py` — `GeminiCliStrategy` | Unit test: `build_command()` output matches existing launcher output |
| DOC-REQ-004 | FR-007 | `launcher.py` — `build_command()` delegation | Unit test: strategy is called for registered runtimes; fallthrough for others |
| DOC-REQ-005 | FR-008 | `managed_agent_adapter.py` — `start()` delegation | Unit test: adapter reads defaults from strategy when available |
| DOC-REQ-006 | FR-006 | `strategies/gemini_cli.py` — `shape_environment()` | Unit test: GEMINI_HOME/GEMINI_CLI_HOME passed through when present |
| DOC-REQ-007 | FR-009, FR-010 | No changes to `supervisor.py` | Regression: `./tools/test_unit.sh` passes; no supervisor modifications |
