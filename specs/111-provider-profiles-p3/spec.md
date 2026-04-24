# Feature Specification: provider-profiles-p3

**Feature Name**: Provider Profiles Phase 3: Manager and Selection Path Completion
**Date**: 2026-03-28
**Status**: DRAFT

## 1. Overview and Business Need

**What are we building?**
Phase 3 of the Provider Profiles transition, converting the final workflow orchestration layers. This involves renaming the legacy `AuthProfileManager` to `ProviderProfileManager`, removing outdated concepts like `"auto"`, and hardening the routing decisions around `execution_profile_ref` and `profile_selector`.

**Why is it important?**
The core orchestration engine for task scheduling currently uses legacy naming (`AuthProfile` instead of `ProviderProfile`) and routing methods. This misalignment causes cognitive overhead and prevents the full realization of feature-rich provider profiles, such as `default_model` integration and intelligent `cooldown` retention across crashes.

**Who is this for?**
Internal developers integrating new provider runtimes and Devops engineers maintaining existing integrations securely.

## 2. Source Document Requirements

*Extracted from docs/Tasks/SkillAndPlanContracts.md (Phase 3)*

| Requirement ID | Source Section | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-005 | 8.3 A | Rename the workflow implementation from AuthProfileManager to ProviderProfileManager, including associated callers, activities, and task queue references. |
| DOC-REQ-006 | 8.3 B & C | Support exact-profile resolution when `execution_profile_ref` is present, and dynamic selector-based resolution (matching provider_id, tags_any, tags_all, runtime_materialization_mode) when absent. Remove the `"auto"` sentinel. Tie-break via highest priority, then most available slots. |
| DOC-REQ-007 | 8.3 D & E | Preserve deterministic lease bounds and cooldown behavior during Manager crash-recovery. Ensure profile snapshots fetched by AgentRun encompass the final Provider Profile contract properties rather than legacy auth/volume triads. |

## 3. User Scenarios & Testing

**Scenario 1: Starting a task with an exact profile reference**
- Given a task payload defining `execution_profile_ref="specific-profile-123"`
- When the AgentRun workflow requests an execution profile
- Then the `ProviderProfileManager` strictly grants a slot from `"specific-profile-123"` without dynamic fallback.

**Scenario 2: Starting a task using a profile selector**
- Given a task payload omitting `execution_profile_ref` but defining a selector with `provider_id="anthropic"` and `tags_any=["premium"]`
- When the AgentRun workflow requests an execution profile
- Then the `ProviderProfileManager` queries its enabled profiles and delegates to the profile with the highest priority matching the selector requirements, tie-breaking via slot availability.

**Scenario 3: Manager state recovery**
- Given an active `ProviderProfileManager` maintaining several active slot leases and cooldown periods.
- When the task-worker panics and Temporal replays the Manager history
- Then the reconstructed Manager state faithfully reconstitutes the `cooldown` timers and active leases bounded by the Provider Profile shapes.

## 4. Functional Requirements

- **REQ-01 (Manager Rename)**: Rename `auth_profile_manager.py` implementations, search attributes, and string references across the codebase to `provider_profile_manager` (Maps to DOC-REQ-005).
- **REQ-02 (Routing Selection)**: Implement deterministic provider selection based on `execution_profile_ref` presence or evaluating `ProviderProfileSelector` matching logic. Delete legacy `"auto"` checking. (Maps to DOC-REQ-006).
- **REQ-03 (Snapshot Validation)**: Expand the manager Lease snapshot to supply the full `ManagedRuntimeProfile` required to operate the agent. Modify AgentRun to properly unwrap and preserve 429 logic using the new models rather than legacy auth-only fields. (Maps to DOC-REQ-007).
- **REQ-04 (Testing)**: Unit testing covering new selector logic, exact-profile fallback prevention, and lease/cooldown functionality. (Maps to DOC-REQ-006, DOC-REQ-007).

## 5. Non-Functional Requirements

- **Observability**: Workflow metrics logging must use the `ProviderProfileManager` label.
- **Resilience**: The Manager rename must not cause nondeterminism errors for actively executing child workflows, it must handle replay bounds safely or be accompanied by a restart script.
- **In-flight compatibility**: Compatibility logic via Temporal History replay.

## 6. Success Criteria

- The term `AuthProfileManager` is removed from all workflows.
- Task inputs reliably resolve configuration using final ProviderProfile features.
- Test suites run 100% locally.

## 7. Dependencies and Assumptions

- **Assumptions**: The system is capable of brief downtime for worker restarts executing the new name registration.
- **Dependencies**: Phase 2 data models `default_model` and `model_overrides` exist (Completed).

## 8. Out of Scope

- Removing completely isolated/stale table rows that do no active execution harm (managed via later data migrations).
