# Research: Dashboard Queue Task Default Pre-Population

## Decision 1: Source defaults from backend settings, not client constants

- **Decision**: Use `moonmind.config.settings` as the single default source for queue runtime/model/effort/repository values.
- **Rationale**: Keeps behavior centralized and aligned across API, worker execution, and dashboard UI.
- **Alternatives considered**:
  - Hardcode defaults only in `dashboard.js`: rejected because API and non-UI clients would diverge.
  - Resolve defaults only in worker layer: rejected because dashboard would not pre-populate fields and API validation would still fail for missing repository.

## Decision 2: Apply default resolution in Agent Queue service for canonical task submissions

- **Decision**: Before canonical normalization, fill missing `task` payload fields for `type=task` in `AgentQueueService.create_job` using settings defaults.
- **Rationale**: Ensures all clients (dashboard, scripts, tests, APIs) benefit from consistent fallback behavior.
- **Alternatives considered**:
  - Modify every API caller to always send explicit values: rejected because omission fallback is a required behavior.
  - Only patch task contract model defaults: rejected because repository is required at top-level and needs settings-aware enrichment.

## Decision 3: Extend dashboard runtime config with default model/effort metadata

- **Decision**: Add `defaultTaskModel` and `defaultTaskEffort` to `build_runtime_config` and render those as initial values in queue submit form inputs.
- **Rationale**: UI boxes should be pre-populated from settings and remain easy to adjust per submission.
- **Alternatives considered**:
  - Keep placeholders only: rejected because user asked for pre-populated editable values.

## Decision 4: Keep user overrides authoritative

- **Decision**: If users type runtime/model/effort/repository values, preserve those values and do not overwrite with defaults.
- **Rationale**: Feature goal is sensible defaults with easy adjustment, not forced configuration.
- **Alternatives considered**:
  - Force settings defaults even when user supplies values: rejected as a UX regression.

## Decision 5: Add focused unit coverage for settings, runtime config, and service normalization

- **Decision**: Add/adjust unit tests in settings/view-model/queue service suites and validate through `./tools/test_unit.sh`.
- **Rationale**: Covers both UX-facing and backend defaulting paths while honoring repository test execution policy.
- **Alternatives considered**:
  - Browser e2e-first validation: deferred; unit coverage is sufficient for this scoped behavior.
