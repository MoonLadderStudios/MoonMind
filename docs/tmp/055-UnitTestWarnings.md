# Unit Test Warnings Remediation Plan

Running the unit tests resulted in several warnings that can be broken down into the following phases to address systematically:

**Current status (2026-04-02):** 2081 passed, 7 skipped, 220 warnings.

## Phase 1: Pydantic V2 Migration (Partially Complete)
These warnings are caused by the upgrade to Pydantic V2 and require straightforward search-and-replace changes.
- [x] **Fields with `env` Argument**: Replace the deprecated `env="VAR"` with `alias="VAR"` or `validation_alias="VAR"` (or via `SettingsConfigDict` features depending on how `settings.py` is configured). Affects many fields in:
  - `moonmind/config/settings.py`
  - `moonmind/config/jules_settings.py`
- [x] **Method Deprecations**:
  - Replace `.dict()` with `.model_dump()` in `api_service/services/profile_service.py` (line 120) and potentially in Temporalio converter code if within project bounds.
  - Replace `.parse_obj()` with `.model_validate()` where found.
- [x] **V1-style validators in `documents_models.py`** (~48 warnings, `PydanticDeprecatedSince20`): `moonmind/schemas/documents_models.py` now uses `@field_validator` and `@model_validator` instead of `@validator` and `@root_validator`.
- [x] **`@model_validator(mode="after")` as classmethod** (~25 warnings, `PydanticDeprecatedSince212`): `moonmind/schemas/manifest_models.py:21` now uses an instance method (`self`) for `mode="after"` on `AuthItem`.

## Phase 2: FastAPI & Dependency Deprecations (Complete)
These are standard library and dependency deprecation changes pointing to future breaking changes.
- **Starlette/FastAPI HTTP Status**: Change occurrences of `HTTP_422_UNPROCESSABLE_ENTITY` to `HTTP_422_UNPROCESSABLE_CONTENT` and `HTTP_413_REQUEST_ENTITY_TOO_LARGE` to `HTTP_413_CONTENT_TOO_LARGE` in:
  - `api_service/api/routers/temporal_artifacts.py`
  - `api_service/api/routers/executions.py`
- **SQLAlchemy/SQLModel**: The `EncryptedType` behavior is changing. Switch from using `LargeBinary` to `String` under the hood by migrating to `StringEncryptedType` in `api_service/db/models.py`.

*Phase 2 is Complete.*

## Phase 3: Temporalio Configuration (Complete)
- [x] **Dictionary-based Search Attributes** (~35 warnings, `DeprecationWarning`): `moonmind/workflows/temporal/client.py` now passes typed search attributes via `TypedSearchAttributes` for both workflow starts and schedule creation.
- [x] **Pydantic V2 Converter**: `temporalio.contrib.pydantic.pydantic_data_converter` is used for Temporal client and worker runtime data conversion.

## Phase 4: Async/Await Runtime Warnings (Complete)
These are actual bugs in test cases or application code where asynchronous functions are ignored.
- [x] **Unawaited `AsyncMockMixin._execute_mock_call`** (3 `RuntimeWarning`s): Occurs in `moonmind/workflows/temporal/service.py:352`. The mock is set up as async but the coroutine is never awaited. Fix the test setup to properly await the mock or use `AsyncMock` correctly.
- [x] **`api_service/main.py` Unawaited Coroutines**: Previously noted at lines 529, 633 — no longer appearing in current test run.

*Phase 4 is Complete.*

## Phase 5: Third-party Library Upgrades (Complete)
- [x] **Qdrant Version Mismatch**: `qdrant_client` version 1.17.1 is complaining that the server is on an incompatible version (1.14.1). Either downgrade the client, upgrade the Qdrant server container, or explicitly suppress the warning with `check_compatibility=False`.

*Phase 5 is Complete.*

## Phase 6: Incorrect `@pytest.mark.asyncio` on Sync Tests (Complete)
38 `PytestWarning`s from sync test functions that carry the `@pytest.mark.asyncio` mark (likely inherited from a class-level or module-level `pytestmark`). Remove the mark from each affected function or convert the function to `async def` if it should actually be async.

Affected files and tests:
- `tests/unit/agents/codex_worker/test_worker.py` (10 tests):
  - `test_load_step_log_offsets_checkpoint_ignores_large_payload` (line 3723)
  - `test_persist_step_log_offsets_checkpoint_safely_skips_symlinked_parent` (line 3754)
  - `test_persist_step_log_offsets_checkpoint_handles_temp_path_directory` (line 3790)
  - `test_resolve_skills_cache_root_uses_worker_workdir_for_relative_paths` (line 7151)
  - `test_parse_git_status_paths_collects_renamed_source_paths` (line 7345)
  - `test_is_source_code_change_path_preserves_dotfile_classes` (line 7362)
  - `test_resolve_publish_verification_skip_reason_rejects_legacy_fields` (line 7383)
  - `test_collect_verification_evidence_ignores_non_prefixed_stdout_lines` (line 7423)
  - `test_collect_verification_evidence_records_log_read_errors` (line 7458)
  - `test_collect_verification_evidence_prefers_structured_report_records` (line 7509)
- `tests/unit/workflows/temporal/workflows/test_agent_run_auto_answer.py` (13 tests)
- `tests/unit/workflows/adapters/test_managed_agent_adapter.py` (5 tests)
- `tests/unit/workflows/adapters/test_jules_client.py` (5 tests)
- `tests/unit/workflows/adapters/test_base_external_agent_adapter.py` (4 tests)
- `tests/unit/mcp/test_jules_tool_registry.py` (1 test)

## Phase 7: Third-party Library Deprecations (Informational)
These warnings originate inside third-party library code and cannot be fixed directly in this codebase. Track for future dependency upgrades.
- **OpenAI SDK Pydantic warnings** (~24 warnings): `moonmind/rag/embedding.py:9` triggers Pydantic deprecation warnings from within the OpenAI library's internal models (`__fields__`, `__fields_set__`). These will resolve when the OpenAI SDK upgrades its internal Pydantic usage.
- **`unittest.mock` Pydantic V2.11 instance-access warnings**: When `unittest.mock` introspects Pydantic models to set up `AsyncMock`/`MagicMock` specs, it accesses `model_fields` and `model_computed_fields` on instances instead of the class (deprecated since Pydantic 2.11). These originate from `unittest/mock.py:529` and `unittest/mock.py:2810` and are not actionable until the standard library is updated.
