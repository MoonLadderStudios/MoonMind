# Requirements Traceability: Generic External Agent Adapter Pattern

Maps each DOC-REQ-* from the source document (`docs/ExternalAgents/ExternalAgentIntegrationSystem.md`) to functional requirements, implementation surfaces, and validation strategy.

| DOC-REQ | FR | Implementation Surface | Validation Strategy |
|---|---|---|---|
| DOC-REQ-001 | FR-001 | `base_external_agent_adapter.py` — `BaseExternalAgentAdapter` ABC | ✅ Already tested in `test_base_external_agent_adapter.py` |
| DOC-REQ-002 | FR-002 | `base_external_agent_adapter.py` — `_validate_request()` | ✅ Already tested: `test_start_rejects_wrong_agent_kind`, `test_start_rejects_wrong_agent_id` |
| DOC-REQ-003 | FR-003 | `base_external_agent_adapter.py` — `_inject_correlation_metadata()` | ✅ Already tested: `test_start_injects_correlation_metadata` |
| DOC-REQ-004 | FR-004 | `base_external_agent_adapter.py` — `_starts_by_idempotency` | ✅ Already tested: `test_start_idempotency_cache_*` |
| DOC-REQ-005 | FR-005 | `base_external_agent_adapter.py` — `build_handle/status/result` helpers | ✅ Already tested: `test_build_handle_*`, `test_build_result_*` |
| DOC-REQ-006 | FR-006 | `base_external_agent_adapter.py` — `cancel()` fallback (NEW) | Unit test: `test_cancel_returns_fallback_when_unsupported` |
| DOC-REQ-007 | FR-001 | Provider adapters override `do_*` hooks only | ✅ Already verified by `JulesAgentAdapter` and `CodexCloudAgentAdapter` structure |
| DOC-REQ-008 | FR-001 | `base_external_agent_adapter.py` — ABC abstract methods | ✅ Already defined and tested |
| DOC-REQ-009 | FR-007 | `agent_runtime_models.py` — `ProviderCapabilityDescriptor` | ✅ Already tested: `test_provider_capability_returns_descriptor` |
| DOC-REQ-010 | FR-008 | `base_external_agent_adapter.py` — `start()` auto-populates `poll_hint_seconds` (NEW) | Unit test: `test_start_populates_poll_hint_from_capability` |
| DOC-REQ-011 | FR-009, FR-011 | `codex_cloud_activities.py` (NEW), activity catalog registration | Unit test: `test_codex_cloud_activities.py` |
| DOC-REQ-012 | FR-010 | `adapters/__init__.py` — export `BaseExternalAgentAdapter` | Import test: verify importable from `moonmind.workflows.adapters` |
| DOC-REQ-013 | FR-012 | `docs/ExternalAgents/AddingExternalProvider.md` (NEW) | Manual review: guide completeness |
