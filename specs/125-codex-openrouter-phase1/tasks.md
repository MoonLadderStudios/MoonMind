# Tasks: Codex CLI OpenRouter Phase 1

## Phase 1: Exact-profile launch plumbing

- [X] T001 Update `moonmind/schemas/agent_runtime_models.py` to model path-aware `file_templates` and richer launch contract fields. (DOC-REQ-001, DOC-REQ-002)
- [X] T002 Update `moonmind/workflows/temporal/artifacts.py` and `moonmind/workflows/adapters/managed_agent_adapter.py` so provider-profile activity output and launcher payloads preserve OpenRouter launch fields. (DOC-REQ-001, DOC-REQ-005)
- [X] T003 Implement path-aware runtime materialization, generated `config.toml`, and `CODEX_HOME` support in `moonmind/workflows/adapters/materializer.py` and `moonmind/workflows/temporal/runtime/launcher.py`. (DOC-REQ-002, DOC-REQ-003)
- [X] T004 Update `moonmind/workflows/temporal/runtime/strategies/codex_cli.py` so provider-driven config can suppress the default `-m` while explicit request overrides still work. (DOC-REQ-005)
- [X] T005 Add the OpenRouter Codex auto-seed path in `api_service/main.py` and widen provider-profile API typing in `api_service/api/routers/provider_profiles.py` for nested env/file templates. (DOC-REQ-004)

## Phase 2: Validation

- [X] T006 Add or update unit tests in `tests/unit/workflows/adapters/test_materializer.py`, `tests/unit/workflows/adapters/test_managed_agent_adapter.py`, `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py`, and `tests/unit/api_service/test_provider_profile_auto_seed.py`. (DOC-REQ-001 through DOC-REQ-005 validation)
- [X] T007 Run `./tools/test_unit.sh --ui-args` is not relevant; run focused Python unit tests and the full `./tools/test_unit.sh` suite for final validation. (validation)
- [X] T008 Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`. (validation)
