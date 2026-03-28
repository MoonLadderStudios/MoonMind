# Feature Specification: Managed Runtime Strategy Pattern — Phase 1

**Feature Branch**: `095-managed-runtime-strategy`
**Created**: 2026-03-21
**Status**: Draft
**Input**: User description: "Implement Phase 1 from docs/tmp/SharedManagedAgentAbstractions.md — Foundation: ABC + Registry + Gemini Strategy"
**Source Contract**: `docs/tmp/SharedManagedAgentAbstractions.md`

## Source Document Requirements

| ID | Source Section | Requirement Summary |
|---|---|---|
| DOC-REQ-001 | Proposed Strategy Pattern / ManagedRuntimeStrategy Interface | System MUST define a `ManagedRuntimeStrategy` abstract base class with `runtime_id`, `default_command_template`, `default_auth_mode`, `build_command`, `shape_environment`, `prepare_workspace`, `classify_exit`, and `create_output_parser` methods/properties. |
| DOC-REQ-002 | Runtime Registry | System MUST provide a `RUNTIME_STRATEGIES` registry mapping `runtime_id` strings to `ManagedRuntimeStrategy` instances. |
| DOC-REQ-003 | Implementation Phases / Phase 1 | System MUST implement a `GeminiCliStrategy` that extracts the existing Gemini CLI branching logic from `launcher.py` and `adapter.py` into a strategy class. |
| DOC-REQ-004 | Implementation Phases / Phase 1 | System MUST wire `ManagedRuntimeLauncher.build_command()` to delegate to the strategy when a registered strategy exists, falling through to existing `if/elif` for unregistered runtimes. |
| DOC-REQ-005 | Implementation Phases / Phase 1 | System MUST wire `ManagedAgentAdapter.start()` to read `default_command_template` and `default_auth_mode` from the strategy when available. |
| DOC-REQ-006 | All Runtime-Specific Branching Sites / _runtime_env_keys | `GeminiCliStrategy.shape_environment()` MUST extract and pass through `GEMINI_HOME` and `GEMINI_CLI_HOME` environment variables. |
| DOC-REQ-007 | Supervisor vs Strategy Boundary | Supervisor MUST retain cross-cutting process lifecycle concerns (heartbeats, timeouts, log streaming, reconciliation). Strategy MUST NOT absorb supervisor responsibilities. |

## User Scenarios & Testing

### User Story 1 — New Strategy Registration (Priority: P1)

A developer adds a `GeminiCliStrategy` to the registry and the launcher uses it to construct CLI commands instead of the hardcoded `if/elif` block.

**Why this priority**: This is the foundational pattern — if this doesn't work, no strategy refactoring can proceed.

**Independent Test**: Can be fully tested by registering a `GeminiCliStrategy`, calling `build_command` through the launcher, and verifying the same CLI arguments are produced as the current `if/elif` path.

**Acceptance Scenarios**:

1. **Given** a `GeminiCliStrategy` is registered in `RUNTIME_STRATEGIES`, **When** `ManagedRuntimeLauncher.build_command()` is called with a `gemini_cli` profile, **Then** the command is built by the strategy and matches the existing output.
2. **Given** a runtime with no registered strategy, **When** `build_command()` is called, **Then** the existing `if/elif` fallback path is used.

---

### User Story 2 — Strategy-Driven Adapter Defaults (Priority: P1)

The `ManagedAgentAdapter.start()` method reads `default_command_template` and `default_auth_mode` from the registered strategy instead of its own `if/elif` block.

**Why this priority**: Eliminates a second branching site and ensures the adapter and launcher stay consistent.

**Independent Test**: Can be tested by calling `ManagedAgentAdapter.start()` with a `gemini_cli` request and verifying the resolved `command_template` and `auth_mode` match the strategy's properties.

**Acceptance Scenarios**:

1. **Given** a `GeminiCliStrategy` is registered, **When** `ManagedAgentAdapter.start()` processes a `gemini_cli` request with no explicit `command_template` in the profile, **Then** it uses `["gemini"]` from the strategy.
2. **Given** a `GeminiCliStrategy` is registered, **When** `ManagedAgentAdapter.start()` processes a `gemini_cli` request, **Then** the resolved auth mode is `"api_key"` from the strategy.

---

### User Story 3 — Environment Shaping via Strategy (Priority: P2)

The `GeminiCliStrategy.shape_environment()` method handles Gemini-specific env passthrough so the hardcoded `_runtime_env_keys` list can be partially replaced.

**Why this priority**: Environment shaping is the third branching concern. For Phase 1 it only needs to work for Gemini — remaining runtimes still use the hardcoded list.

**Independent Test**: Can be tested by invoking `GeminiCliStrategy.shape_environment()` with a base env and verifying `GEMINI_HOME` and `GEMINI_CLI_HOME` are passed through when present.

**Acceptance Scenarios**:

1. **Given** `GEMINI_HOME` is set in the worker's environment, **When** `shape_environment()` is called, **Then** the output env includes `GEMINI_HOME`.
2. **Given** `GEMINI_HOME` is not set, **When** `shape_environment()` is called, **Then** the output env does not inject a spurious `GEMINI_HOME` value.

---

### Edge Cases

- What happens when a strategy is registered for a runtime_id that already exists in `RUNTIME_STRATEGIES`? Latest registration wins; log a warning.
- What happens when `build_command` receives a profile with a `runtime_id` that differs from the strategy's `runtime_id` property? The launcher should raise a `ValueError`.
- What happens when both a strategy `shape_environment()` and the existing `_runtime_env_keys` list try to set the same key? During Phase 1 fallthrough, the strategy result takes precedence for registered runtimes.

## Requirements

### Functional Requirements

- **FR-001**: System MUST define `ManagedRuntimeStrategy` as an ABC with abstract methods `build_command` and properties `runtime_id`, `default_command_template`. (DOC-REQ-001)
- **FR-002**: System MUST define `shape_environment`, `prepare_workspace`, `classify_exit`, and `create_output_parser` as concrete methods with sensible defaults on the ABC. (DOC-REQ-001)
- **FR-003**: System MUST define `default_auth_mode` as a concrete property defaulting to `"api_key"`. (DOC-REQ-001)
- **FR-004**: System MUST expose a `RUNTIME_STRATEGIES` dict registry mapping `runtime_id` strings to instantiated strategy objects. (DOC-REQ-002)
- **FR-005**: System MUST implement `GeminiCliStrategy` with `build_command()` extracting CLI construction from `launcher.py:342-351`. (DOC-REQ-003)
- **FR-006**: System MUST implement `GeminiCliStrategy.shape_environment()` to pass through `GEMINI_HOME` and `GEMINI_CLI_HOME`. (DOC-REQ-006)
- **FR-007**: `ManagedRuntimeLauncher.build_command()` MUST delegate to `RUNTIME_STRATEGIES[runtime_id]` when a registered strategy exists, falling through to existing branching otherwise. (DOC-REQ-004)
- **FR-008**: `ManagedAgentAdapter.start()` MUST read `default_command_template` and `default_auth_mode` from the strategy when a registered strategy exists. (DOC-REQ-005)
- **FR-009**: All existing unit and integration tests MUST continue to pass without modification. (DOC-REQ-007)
- **FR-010**: Phase 1 MUST NOT modify supervisor logic (heartbeats, timeouts, log streaming, reconciliation). (DOC-REQ-007)

### Key Entities

- **ManagedRuntimeStrategy**: Abstract base class defining the per-runtime strategy contract.
- **GeminiCliStrategy**: Concrete strategy for Gemini CLI runtime.
- **RUNTIME_STRATEGIES**: Module-level dict registry of strategy instances keyed by `runtime_id`.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Adding a new managed runtime requires only implementing a new `ManagedRuntimeStrategy` subclass and registering it — no changes to launcher or adapter core logic.
- **SC-002**: `GeminiCliStrategy.build_command()` produces identical CLI arguments to the current `if/elif` path for all existing test inputs.
- **SC-003**: All existing unit tests pass without modification after the refactor.
- **SC-004**: The `launcher.py` Gemini CLI `elif` block is reachable only via fallthrough (when strategy is not registered) — demonstrating the delegation pattern works.

Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.
