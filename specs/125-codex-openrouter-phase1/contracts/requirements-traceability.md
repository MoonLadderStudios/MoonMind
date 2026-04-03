# Requirements Traceability: Codex CLI OpenRouter Phase 1

| FR | DOC-REQ | Implementation File | Validation Evidence |
|----|---------|---------------------|---------------------|
| FR-001, FR-005 | DOC-REQ-001 | `moonmind/workflows/temporal/artifacts.py`, `moonmind/workflows/adapters/managed_agent_adapter.py` | `test_provider_profile_list_preserves_path_aware_codex_materialization_fields`, `test_start_passes_rich_provider_profile_fields_to_launcher` |
| FR-002 | DOC-REQ-002 | `moonmind/schemas/agent_runtime_models.py`, `moonmind/workflows/adapters/materializer.py` | `test_materializer_path_aware_file_templates_written_and_cleanup` |
| FR-003 | DOC-REQ-003 | `moonmind/workflows/adapters/materializer.py`, `moonmind/workflows/temporal/runtime/launcher.py` | `test_materializer_path_aware_file_templates_written_and_cleanup` |
| FR-004 | DOC-REQ-004 | `api_service/main.py` | `test_auto_seed_includes_openrouter_codex_profile_when_env_set` |
| FR-005 | DOC-REQ-005 | `moonmind/workflows/temporal/runtime/strategies/codex_cli.py` | `test_suppress_default_model_flag_omits_default_m_flag`, `test_suppress_default_model_flag_keeps_explicit_request_model` |
