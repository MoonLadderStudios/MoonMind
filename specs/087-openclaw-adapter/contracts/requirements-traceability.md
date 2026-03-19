# Requirements traceability — 087-openclaw-adapter

| DOC-REQ | Functional requirement | Implementation surfaces | Validation |
|---------|------------------------|-------------------------|------------|
| DOC-REQ-001 | FR-001 | `moonmind/openclaw/settings.py`, `moonmind/workflows/adapters/external_adapter_registry.py` | Unit: gate + registry when disabled |
| DOC-REQ-002 | FR-002 | `moonmind/openclaw/execute.py` | Unit: gate requires token when enabled |
| DOC-REQ-003 | FR-003 | `moonmind/workflows/adapters/openclaw_client.py` | Unit: `parse_sse_lines_for_deltas` |
| DOC-REQ-004 | FR-004 | `moonmind/workflows/adapters/openclaw_agent_adapter.py`, `moonmind/openclaw/execute.py` | Unit: message + result builders |
| DOC-REQ-005 | FR-005 | `moonmind/schemas/agent_runtime_models.py`, `external_adapter_execution_style` activity | Unit: capability `execution_style` |
| DOC-REQ-006 | FR-005 | `moonmind/workflows/temporal/workflows/agent_run.py` | Integration: external Jules path order |
| DOC-REQ-007 | FR-006 | `activity_catalog.py`, `activity_runtime.py` | Catalog entry present |
| DOC-REQ-008 | FR-001 | `external_adapter_registry.py` | Registry registers `openclaw` when gate passes |
| DOC-REQ-009 | FR-003 | `moonmind/openclaw/execute.py` (`activity.heartbeat`) | Code review / optional activity test |
| DOC-REQ-010 | FR-003 | `agent_run.py` (TRY_CANCEL + heartbeat timeout on execute) | Integration / manual |
| DOC-REQ-011 | FR-007 | `tests/unit/workflows/adapters/test_openclaw_*.py` | `pytest` |
