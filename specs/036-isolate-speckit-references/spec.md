# Feature Specification: Isolate Spec Kit References and Skill-First Runtime

**Feature Branch**: `[036-isolate-speckit-references]`  
**Created**: 2026-02-20  
**Status**: Draft  
**Input**: User description: "Create a spec on isolating spec kit references and modernizing to avoid using the installed version in favor of spec kit as a skill. Clean up mentions and remove legacy logic so the codebase is clear and consistent."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run Skill Workflows Without Installed Speckit Dependency (Priority: P1)

A workflow operator can run non-speckit skills even when the Speckit CLI is not installed, because execution depends on the selected skill adapter instead of a global Speckit prerequisite.

**Why this priority**: This removes the highest-impact operational blocker and aligns runtime behavior with the intended skill-first architecture.

**Independent Test**: Can be fully tested by running a workflow that uses a non-speckit skill in an environment without Speckit installed and confirming end-to-end completion.

**Acceptance Scenarios**:

1. **Given** a workflow requests a non-speckit skill and required runtime dependencies are available, **When** the workflow starts, **Then** it completes without requiring Speckit installation.
2. **Given** Speckit is not installed and a service startup or preflight check runs, **When** only non-speckit skills are in scope, **Then** health checks pass without Speckit-specific failure.

---

### User Story 2 - Use Neutral Workflow Naming With Compatibility Coverage (Priority: P2)

A platform maintainer can use neutral workflow naming across API and configuration surfaces while keeping legacy SPEC-prefixed entry points functional during migration.

**Why this priority**: Clear naming reduces confusion and prevents future features from reinforcing legacy terminology while preserving compatibility.

**Independent Test**: Can be tested by calling canonical workflow interfaces and legacy interfaces for the same operation and confirming equivalent behavior plus deprecation signaling.

**Acceptance Scenarios**:

1. **Given** a client calls canonical workflow run interfaces, **When** it creates and reads runs, **Then** responses match expected workflow behavior.
2. **Given** a client calls legacy SPEC-prefixed interfaces, **When** it performs the same workflow operations, **Then** behavior remains compatible and a deprecation signal is returned.

---

### User Story 3 - Fail Fast for Unsupported Skills Instead of Silent Fallbacks (Priority: P3)

A contributor can rely on explicit adapter resolution behavior because unsupported skills fail with clear errors instead of silently executing an unintended direct path.

**Why this priority**: Predictable failure behavior prevents hidden regressions and makes runtime responsibilities explicit.

**Independent Test**: Can be tested by requesting an unregistered skill and verifying a deterministic error is produced before execution.

**Acceptance Scenarios**:

1. **Given** a workflow requests a skill with no registered adapter, **When** dispatch begins, **Then** the run fails immediately with an actionable error that identifies the missing adapter.
2. **Given** logs and workflow state are inspected after the failure, **When** the failure is recorded, **Then** the reason clearly indicates adapter resolution failure and no direct fallback execution occurred.

---

### Edge Cases

- A deployment only defines legacy SPEC-prefixed configuration keys and no canonical replacements yet.
- A mixed-version client fleet uses both canonical and legacy interfaces during the migration window.
- Speckit-specific workflows are requested when Speckit adapter prerequisites are missing.
- Historical persistence remains on SPEC-prefixed tables that must stay stable while external naming is modernized.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST resolve workflow skill stages through an explicit adapter registry and treat adapter execution as the authoritative path.
- **FR-002**: The system MUST allow non-speckit skill workflows to execute when Speckit is not installed.
- **FR-003**: The system MUST restrict Speckit executable checks to operations that explicitly require the Speckit adapter.
- **FR-004**: The system MUST fail fast with a clear, actionable error when a requested skill lacks a registered adapter.
- **FR-005**: The system MUST provide canonical, neutral naming for workflow APIs and configuration while maintaining legacy SPEC-prefixed aliases for backward compatibility.
- **FR-006**: The system MUST mark legacy SPEC-prefixed workflow interfaces and settings as deprecated and include migration guidance.
- **FR-007**: The system MUST preserve access to existing workflow history without requiring destructive renaming of persisted SPEC-prefixed storage structures.
- **FR-008**: The system MUST align default workflow queue/configuration naming across code and environment templates.
- **FR-009**: The system MUST update repository documentation and templates so skill-first workflow execution is the default guidance.
- **FR-010**: The delivered change set MUST include runtime code changes and automated validation tests that verify adapter resolution, dependency checks, compatibility aliases, and failure behavior.
- **FR-011**: The system MUST record when legacy aliases are used so maintainers can track migration progress.

### Key Entities *(include if feature involves data)*

- **Skill Adapter Registration**: Declares which workflow skill IDs are supported and how each is executed.
- **Workflow Interface Alias**: A legacy-compatible entry point mapped to a canonical neutral workflow interface.
- **Workflow Dependency Check**: Validation logic that ensures only the selected skill's required runtime dependencies are enforced.
- **Workflow Run Record**: Persisted workflow state and history that remains accessible through both canonical and legacy naming during migration.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In validation environments without Speckit installed, 100% of non-speckit workflow regression scenarios complete successfully.
- **SC-002**: 100% of workflow requests for unregistered skills fail before execution begins and return a standardized adapter-missing error.
- **SC-003**: 100% of currently supported legacy SPEC-prefixed workflow interfaces continue to function during the migration window while exposing deprecation signaling.
- **SC-004**: 0 runtime startup or preflight failures are caused solely by missing Speckit when the requested workflows are non-speckit.
- **SC-005**: Workflow-related codebase guidance reflects canonical neutral terminology in at least 95% of new and updated references introduced by this feature.

## Scope Boundaries

### In Scope

- Establishing skill-adapter-first execution for workflow stages.
- Removing mandatory global Speckit dependency checks from non-speckit workflow paths.
- Introducing canonical neutral workflow naming with backward-compatible legacy aliases.
- Updating validation coverage and documentation to reflect the modernized workflow model.

### Out of Scope

- Renaming persisted SPEC-prefixed database/storage structures in this feature.
- Removing all legacy aliases in a single release without a migration window.
- Expanding the feature to redesign unrelated workflow capabilities beyond naming and dependency isolation.

## Assumptions & Dependencies

- Existing consumers can transition to canonical naming during a defined deprecation window while retaining temporary alias support.
- Persisted SPEC-prefixed storage names remain stable for this feature; data-model renaming is deferred to a separate migration effort.
- Validation environments can run workflow regression tests with Speckit intentionally absent to confirm dependency decoupling.
- Teams maintaining API and worker surfaces will coordinate on consistent canonical naming and deprecation messaging.
