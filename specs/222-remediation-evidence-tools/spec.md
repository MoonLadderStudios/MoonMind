# Feature Specification: Remediation Evidence Tools

**Feature Branch**: `run-jira-orchestrate-for-mm-433-expose-t-1a03b536`
**Created**: 2026-04-21
**Status**: Draft
**Input**: Jira Orchestrate for MM-433.

Source story: STORY-003.
Source summary: Expose typed evidence tools and live follow for remediators.
Source Jira issue: unknown.
Original brief reference: not provided.

Use the existing Jira Orchestrate workflow for this Jira issue. Do not run implementation inline inside the breakdown task.

## User Story - Typed Remediation Evidence Access

**Summary**: As a remediation runtime, I want typed MoonMind evidence tools for context, target artifacts, target logs, and optional live follow so I can investigate a target execution without scraping UI pages or receiving raw storage access.

**Goal**: A remediation execution with a linked `remediation.context` artifact can call a bounded service surface to read only context-declared evidence and follow live target logs only when the context marks live follow as supported and policy-allowed.

**Independent Test**: Create a target execution and remediation execution with a generated remediation context, then verify the evidence tool service returns the context, allows only declared artifact refs and taskRunIds, bounds log tails, rejects undeclared evidence, and gates live follow on context support.

## Requirements

- **FR-001**: The system MUST expose a typed remediation evidence tool service for `remediation.get_context`, `remediation.read_target_artifact`, `remediation.read_target_logs`, and `remediation.follow_target_logs`.
- **FR-002**: The service MUST require a persisted remediation link with a linked `remediation.context` artifact before returning any target evidence.
- **FR-003**: `get_context` MUST return the parsed bounded context artifact and verify that the context target matches the persisted remediation link.
- **FR-004**: `read_target_artifact` MUST only read artifact IDs declared by the context evidence refs.
- **FR-005**: `read_target_logs` MUST only read taskRunIds declared by the context evidence or selected steps and MUST bound requested tail lines by the context policy.
- **FR-006**: `follow_target_logs` MUST require `liveFollow.supported = true`, a follow-capable mode, and a context-declared taskRunId.
- **FR-007**: Live follow results MUST return a resume cursor handoff so the caller can persist progress outside raw log bodies.
- **FR-008**: The service MUST NOT expose action execution, raw host shell, raw SQL, raw Docker, raw storage paths, or raw credentials.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/Tasks/TaskRemediation.md` section 9.5): Remediation tasks need typed MoonMind-owned evidence tools. Scope: in scope.
- **DESIGN-REQ-002** (`docs/Tasks/TaskRemediation.md` section 9.6): Live follow is optional, policy-gated, and never the only evidence path. Scope: in scope.
- **DESIGN-REQ-003** (`docs/Tasks/TaskRemediation.md` sections 6 and 9.4): Evidence access must stay server-mediated and bounded. Scope: in scope.
- **DESIGN-REQ-004** (`docs/Tasks/TaskRemediation.md` section 11): Typed administrative actions are a later action-registry story. Scope: out of scope.

## Success Criteria

- **SC-001**: Unit tests prove context and target artifact reads are allowed only through the linked remediation context.
- **SC-002**: Unit tests prove log reads are taskRunId-scoped and tail-line bounded.
- **SC-003**: Unit tests prove live follow is rejected when unsupported and allowed when context policy enables it.
- **SC-004**: Unit tests prove undeclared artifacts and taskRunIds are rejected.
