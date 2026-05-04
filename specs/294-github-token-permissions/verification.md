# Verification: GitHub Token Permission Improvements

## Quickstart Validation

- `pytest tests/unit/workflows/adapters/test_github_service.py -q`: passed.
- `pytest tests/unit/workflows/temporal/runtime/test_managed_api_key_resolve.py -q`: passed as part of the focused resolver/indexer/runtime run.
- `pytest tests/unit/indexers/test_github_indexer.py -q`: passed as part of the focused resolver/indexer/runtime run.
- `pytest tests/unit/auth/test_github_credentials.py tests/unit/workflows/adapters/test_github_service.py tests/unit/publish/test_publish_service_github_auth.py tests/unit/indexers/test_github_indexer.py tests/unit/workflows/temporal/runtime/test_managed_api_key_resolve.py -q`: passed, 54 tests.
- `pytest tests/unit/auth/test_github_credentials.py tests/unit/workflows/adapters/test_github_service.py tests/unit/publish/test_publish_service_github_auth.py tests/unit/indexers/test_github_indexer.py tests/unit/workflows/temporal/runtime/test_managed_api_key_resolve.py tests/unit/agents/codex_worker/test_handlers.py tests/unit/agents/codex_worker/test_worker.py::test_run_publish_stage_uses_verbatim_overrides_and_redacts_command_logs tests/unit/services/temporal/runtime/test_managed_session_controller.py::test_controller_launch_clones_workspace_before_starting_container -q`: passed, 105 tests.
- `pytest tests/unit/agents/codex_worker/test_handlers.py -q`: passed, 48 tests.
- `pytest tests/unit/services/temporal/test_fetch_result_push.py -q`: passed, 54 tests.
- `pytest tests/integration/api/test_github_token_probe.py tests/integration/temporal/test_github_publish_readiness_boundaries.py -q`: passed, 6 tests.
- `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`: failed because the active working tree is missing checked-in `.agents/skills/...` files required by unrelated PR resolver and skill resolver tests. The two publish/runtime-adjacent failures found in that run were fixed and now pass individually.
- `./tools/test_integration.sh`: blocked because Docker is unavailable in the managed container (`/var/run/docker.sock` is missing).

## Deviations

- Full-suite unit verification is blocked by unrelated `.agents/skills` projection state in this managed run.
- Full-suite integration verification requires Docker access, which is not available in this managed container.
