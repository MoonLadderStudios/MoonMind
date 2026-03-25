# Unit Test Warnings Remediation Plan

Running the unit tests resulted in several warnings that can be broken down into the following phases to address systematically:

## Phase 1: Pydantic V2 Migration
These warnings are caused by the upgrade to Pydantic V2 and require straightforward search-and-replace changes.
- **Fields with `env` Argument**: Replace the deprecated `env="VAR"` with `alias="VAR"` or `validation_alias="VAR"` (or via `SettingsConfigDict` features depending on how `settings.py` is configured). Affects many fields in:
  - `moonmind/config/settings.py`
  - `moonmind/config/jules_settings.py`
- **Method Deprecations**:
  - Replace `.dict()` with `.model_dump()` in `api_service/services/profile_service.py` (line 120) and potentially in Temporalio converter code if within project bounds.
  - Replace `.parse_obj()` with `.model_validate()` where found.

## Phase 2: FastAPI & Dependency Deprecations (Complete)
These are standard library and dependency deprecation changes pointing to future breaking changes.
- **Starlette/FastAPI HTTP Status**: Change occurrences of `HTTP_422_UNPROCESSABLE_ENTITY` to `HTTP_422_UNPROCESSABLE_CONTENT` and `HTTP_413_REQUEST_ENTITY_TOO_LARGE` to `HTTP_413_CONTENT_TOO_LARGE` in:
  - `api_service/api/routers/temporal_artifacts.py`
  - `api_service/api/routers/executions.py`
- **SQLAlchemy/SQLModel**: The `EncryptedType` behavior is changing. Switch from using `LargeBinary` to `String` under the hood by migrating to `StringEncryptedType` in `api_service/db/models.py`.

## Phase 3: Temporalio Configuration 
- **Pydantic V2 Converter**: `temporalio.converter` is warning about Pydantic V2 payloads. The application needs to explicitly opt into `temporalio.contrib.pydantic.pydantic_data_converter` or switch to the new temporalio data converters for Pydantic V2.

## Phase 4: Async/Await Runtime Warnings
These are actual bugs in test cases or application code where asynchronous functions are ignored.
- **Unawaited Coroutines in Tests**: The tests `TestVerifyVolumeCredentials.test_successful_verification` and `TestVerifyVolumeCredentials.test_no_credentials_found` have an unawaited mock coroutine (`mock_communicate`). Apply `await` to fix these. Also, `AsyncMockMixin._execute_mock_call` unawaited in pytest.
- **`api_service/main.py` Unawaited Coroutines**: Likely related to an unawaited async shutdown or startup function (lines 529, 633). 

## Phase 5: Third-party Library Upgrades
- **Qdrant Version Mismatch**: `qdrant_client` version 1.17.1 is complaining that the server is on an incompatible version (1.14.1). Either downgrade the client, upgrade the Qdrant server container, or explicitly suppress the warning with `check_compatibility=False`.
