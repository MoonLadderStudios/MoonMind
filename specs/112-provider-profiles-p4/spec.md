# Feature Specification: Provider Profiles Phase 4 - Runtime Materialization

**Feature Branch**: `112-provider-profiles-p4`  
**Created**: 2026-03-28  
**Status**: Draft  
**Input**: User description: "Fully implement Phase 4 of docs/tmp/005-ProviderProfilesPlan.md: Runtime Materialization and Secret-Resolution Integration"

## Source Document Requirements

- **DOC-REQ-001**: A reusable materialization pipeline must be implemented that constructs agent execution environments. It must execute operations in the following strict order: base environment, runtime defaults, clear_env_keys, secret resolution for launch-only use, file_templates, env_template, home_path_overrides, runtime strategy shaping, and command construction.
- **DOC-REQ-002**: SecretRef-aware resolution must be integrated for `db_encrypted` secrets at launch time. Resolved plaintext secrets must never be written into workflow payloads, durable rows, logs, or diagnostics.
- **DOC-REQ-003**: The legacy `auth_mode`/`api_key_ref` logic in `ManagedAgentAdapter.start()` must be replaced with logic driven by `credential_source` and `runtime_materialization_mode` from the Provider Profile contract.
- **DOC-REQ-004**: Runtime strategies for Gemini, Claude Code, and Codex CLI must be updated to consume `command_behavior`, `default_model`, and `model_overrides` rather than hard-coded logic. Generated config files containing secrets must be ephemeral and cleaned up properly.

## User Scenarios & Testing

### User Story 1 - Secure Provider Authentication (Priority: P1)

Operators need to configure agents using encrypted credentials without exposing them in logs or workflow history.

**Why this priority**: Correctly loading secrets safely is the foundational security requirement of Phase 4 and blocks all other agent behaviors using new profiles.

**Independent Test**: Can be tested by executing an agent run using a Profile with `secret_refs`, and verifying the agent process receives the decrypted key in its environment while workflow payloads show only references.

**Acceptance Scenarios**:

1. **Given** a valid Provider Profile utilizing `secret_refs`, **When** the agent runtime materializer prepares the launch, **Then** the process environment contains the decrypted values.
2. **Given** the same run, **When** inspecting Temporal history or application logs, **Then** all secret values are redacted or entirely absent.

---

### User Story 2 - Profile-Driven Agent Shaping (Priority: P1)

Operators must rely on the materialization pipeline applying template files, configuration paths, and clearing specific environment variables before agent start.

**Why this priority**: Different models and providers require entirely different file structures (like Anthropic vs Claude-compatible MiniMax). This is the core functionality that deprecates `auth_mode`.

**Independent Test**: Can be tested by running an agent with `clear_env_keys` and `file_templates`, ensuring the environment variables are clear and the temporary config file is physically generated.

**Acceptance Scenarios**:

1. **Given** a profile with `clear_env_keys` configured to clear `ANTHROPIC_API_KEY`, **When** the agent launches via the new pipeline, **Then** the resulting process environment completely lacks the cleared key.
2. **Given** a profile with `file_templates`, **When** the materializer runs, **Then** the files are structured correctly in the container and the paths are injected via `env_template`.

## Requirements

### Functional Requirements

- **FR-001**: System MUST process runtime environment assembly strictly following the 9-step materialization pipeline order. (Addresses DOC-REQ-001)
- **FR-002**: System MUST resolve `secret_refs` just-in-time when constructing the container launch command/environment. (Addresses DOC-REQ-002)
- **FR-003**: System MUST NOT serialize plaintext results of `secret_refs` into Temporal workflow payloads, artifacts, or execution traces. (Addresses DOC-REQ-002)
- **FR-004**: System MUST launch managed agents using `credential_source` and `runtime_materialization_mode` rather than legacy `auth_mode` branching. (Addresses DOC-REQ-003)
- **FR-005**: System MUST clean up any generated secret-bearing file templates automatically when the agent session ends. (Addresses DOC-REQ-004)
- **FR-006**: System MUST allow configuring specific environment variables to be explicitly erased from the base image via `clear_env_keys`. (Addresses DOC-REQ-001)

### Key Entities

- **ManagedRuntimeProfile**: The data transfer object loaded from the Provider Profile containing templating directives, secrets, and shaping rules.
- **ProviderProfileMaterializer**: The new subsystem responsible for orderly transforming a base environment plus a profile into a final launch command and environment payload.
- **SecretResolverBoundary**: The programmatic secure boundary used to resolve references to physical values at launch.

## Success Criteria

### Measurable Outcomes

- **SC-001**: 100% of managed agent launches execute using the new generic materializer instead of the legacy adapter branching logic.
- **SC-002**: 0 plaintext secrets created from `secret_refs` are written to Temporal execution payloads or logs.
- **SC-003**: The execution environment behaves identically to the older version for existing agents that are migrated transparently, verified by automated test suites.
