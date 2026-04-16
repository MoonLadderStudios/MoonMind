# Feature Specification: Claude Decision Pipeline

**Feature Branch**: `185-claude-decision-pipeline`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
Jira issue: MM-344 from MM board
Summary: MoonSpec STORY-003: Normalize Claude decision and hook provenance
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-344 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-344: MoonSpec STORY-003: Normalize Claude decision and hook provenance

User Story
As a security reviewer, I need every Claude tool, file, network, MCP, hook, classifier, and prompt gate normalized as a DecisionPoint with provenance so approval behavior is explainable after the run.

Source Document
docs/ManagedAgents/ClaudeCodeManagedSessions.md

Source Sections
- 2.4 Model deterministic and non-deterministic safety controls explicitly
- 12. Decision pipeline
- 18.5 Work events
- 18.6 Decision events
- 21.5 Hook governance

Coverage IDs
- DESIGN-REQ-011
- DESIGN-REQ-012
- DESIGN-REQ-025
- DESIGN-REQ-028

Story Metadata
- Story ID: STORY-003
- Short name: claude-decision-pipeline
- Dependency mode: none
- Story dependencies from breakdown: STORY-001, STORY-002

Acceptance Criteria
- Decision stages execute in the documented order from session_state_guard through checkpoint_capture.
- Deny, ask, and allow rule precedence is deterministic with first matching rule behavior.
- Protected paths are never silently auto-approved and are recorded with origin_stage = protected_path.
- Classifier outcomes are distinguishable from user approvals and policy outcomes.
- Headless unresolved decisions deny or defer according to policy and hook output.
- Hook executions emit source scope, event type, matcher, outcome, and audit data.

Requirements
- Broaden DecisionPoint beyond simple approval prompts.
- Record provenance for policy, hook, sandbox, classifier, user, and runtime resolutions.
- Emit normalized work and decision events for each stage that materially affects execution.
- Ensure hooks may tighten restrictions but cannot override matching deny or ask policy rules.

Independent Test
Submit representative tool proposals through the decision pipeline with pretool hooks, deny/ask/allow rules, protected paths, sandbox substitution, auto classifier outcomes, interactive prompts, and posttool hooks, then assert emitted DecisionPoint and HookAudit records.

Out of Scope
- Policy source resolution.
- Checkpoint storage payloads.
- Team messaging.

Source Design Coverage
- DESIGN-REQ-011: Owns normalization of Claude tool, file, network, MCP, classifier, and prompt-gate decisions as provenance-bearing DecisionPoint records.
- DESIGN-REQ-012: Owns deterministic deny, ask, and allow rule precedence across the decision pipeline.
- DESIGN-REQ-025: Owns normalized work and decision events for stages that materially affect execution.
- DESIGN-REQ-028: Owns hook governance, including pretool and posttool hook audit data and restrictions that cannot override matching deny or ask policy rules.

Needs Clarification
- None

Notes
This story depends on the Claude managed-session core schema and session launch contract stories so DecisionPoint and HookAudit provenance can attach to the shared session-plane records and runtime boundary.
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Claude Decision And Hook Provenance

**Summary**: As a security reviewer, I want Claude runtime decisions and hook executions normalized with provenance so that approval behavior is explainable after the run.

**Goal**: Security reviewers and operators can inspect each material Claude decision and hook execution as a bounded, normalized record that identifies the stage, origin, outcome, and related session-plane identifiers without treating every safety path as a simple approval prompt.

**Independent Test**: Submit representative Claude tool proposals through the normalized decision boundary with pretool hooks, deny/ask/allow rules, protected paths, sandbox substitution, auto classifier outcomes, interactive prompts, headless resolution, runtime execution, posttool hooks, and checkpoint capture, then assert emitted DecisionPoint and HookAudit records preserve documented stage order, provenance, outcomes, and event names.

**Acceptance Scenarios**:

1. **Given** a Claude tool proposal passes through the decision pipeline, **when** each material stage records its decision, **then** DecisionPoint records use the documented stage order from `session_state_guard` through `checkpoint_capture`.
2. **Given** deny, ask, and allow policy rules could match the same proposal, **when** the policy decision is recorded, **then** the first matching rule in deny, ask, allow precedence determines the DecisionPoint outcome and provenance.
3. **Given** a proposal touches a protected path, **when** the decision is recorded, **then** the record is not auto-approved and includes `origin_stage = protected_path_guard`.
4. **Given** a sandbox substitutes for command approval, **when** the decision is recorded, **then** the outcome is distinguishable from an explicit allow rule and records sandbox provenance.
5. **Given** auto mode requires classifier review, **when** the classifier resolves the proposal, **then** the DecisionPoint outcome is distinguishable from user approval and policy outcomes.
6. **Given** a decision remains unresolved in interactive or headless mode, **when** resolution occurs, **then** interactive prompts record user provenance while headless flows record deny or defer according to policy and hook output.
7. **Given** pretool or posttool hooks execute, **when** hook audit records are emitted, **then** each HookAudit includes source scope, event type, matcher, outcome, and bounded audit data.

### Edge Cases

- A pretool hook may tighten restrictions to ask, deny, mutate, or defer but must not override a matching policy deny or ask rule.
- `bypassPermissions` may skip prompts and safety checks but must still respect protected-path handling.
- Runtime execution can fail after policy resolution; the final runtime status must be recorded separately from the policy outcome.
- Posttool hooks may block later flow or emit artifacts without changing the already-recorded decision outcome.
- Hook audit data and decision metadata must remain bounded and must not embed large tool payloads or transport envelopes.

## Assumptions

- MM-344 builds on the existing Claude managed-session core contracts from MM-342 and records decisions against canonical `session_id`, `turn_id`, and optional work-item identifiers.
- This story defines runtime-validatable schema contracts and boundary helpers for normalized decisions and hook audit records; adapter-specific policy source resolution remains out of scope.
- Existing Codex managed-session contracts and Claude core session records remain unchanged except for exported Claude decision and hook models.

## Source Design Requirements

- **DESIGN-REQ-011**: Source `docs/ManagedAgents/ClaudeCodeManagedSessions.md` sections 2.4 and 12 require Claude tool, file, network, MCP, classifier, and prompt gates to normalize into DecisionPoint records with provenance. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, and FR-007.
- **DESIGN-REQ-012**: Source section 12.2 requires deterministic deny, ask, and allow policy precedence with first matching rule behavior, and requires hooks to tighten restrictions without overriding matching deny or ask policy rules. Scope: in scope. Maps to FR-003 and FR-010.
- **DESIGN-REQ-025**: Source sections 18.5 and 18.6 require normalized work and decision event names for material execution and decision stages. Scope: in scope. Maps to FR-008 and FR-009.
- **DESIGN-REQ-028**: Source section 21.5 requires hook provenance including source scope, event type, matcher, outcome, and audit data. Scope: in scope. Maps to FR-011 and FR-012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a Claude DecisionPoint contract that records a stable decision identifier, `session_id`, `turn_id`, optional work-item identifier, proposal kind, origin stage, outcome, provenance source, event name, and bounded metadata.
- **FR-002**: DecisionPoint origin stages MUST accept only the documented decision pipeline stages: `session_state_guard`, `pretool_hooks`, `permission_rules`, `protected_path_guard`, `permission_mode_baseline`, `sandbox_substitution`, `auto_mode_classifier`, `interactive_prompt_or_headless_resolution`, `runtime_execution`, `posttool_hooks`, and `checkpoint_capture`.
- **FR-003**: Policy DecisionPoint records MUST represent deny, ask, and allow precedence deterministically and expose enough provenance to identify the winning rule and first-match behavior.
- **FR-004**: Protected-path decisions MUST use `origin_stage = protected_path_guard` and MUST NOT be representable as automatic approval.
- **FR-005**: Sandbox-substitution decisions MUST be distinguishable from explicit allow-rule decisions.
- **FR-006**: Classifier decisions MUST be distinguishable from user approvals, policy outcomes, sandbox outcomes, hook outcomes, and runtime outcomes.
- **FR-007**: Headless unresolved decisions MUST support only deny or defer outcomes, while interactive prompt decisions MAY record user approval or denial provenance.
- **FR-008**: DecisionPoint records MUST emit only documented decision event names: `decision.proposed`, `decision.mutated`, `decision.allowed`, `decision.asked`, `decision.denied`, `decision.deferred`, `decision.canceled`, and `decision.resolved`.
- **FR-009**: Claude work-item records MUST accept documented hook event names for `work.hook.started`, `work.hook.completed`, and `work.hook.blocked` without weakening existing work-item validation.
- **FR-010**: Hook-origin decisions MUST NOT override matching policy deny or ask decisions and MUST be able to record that the hook tightened the effective outcome.
- **FR-011**: System MUST expose a Claude HookAudit contract that records a stable hook audit identifier, `session_id`, `turn_id`, optional decision identifier, hook name, source scope, event type, matcher, outcome, and bounded audit data.
- **FR-012**: HookAudit source scope and outcome MUST accept only documented values and reject unknown scopes or outcomes.
- **FR-013**: DecisionPoint metadata and HookAudit audit data MUST remain bounded compact metadata and MUST reject large transport-like payloads.

### Key Entities

- **Claude DecisionPoint**: Normalized record of one material Claude decision pipeline stage, including the stage, proposal kind, outcome, provenance source, event name, and compact metadata.
- **Claude HookAudit**: Normalized audit record for one hook execution, including source scope, event type, matcher, outcome, related decision, and compact audit data.
- **Claude Decision Proposal**: The tool, file, network, MCP, classifier, or prompt gate request being evaluated by the decision pipeline.
- **Claude Decision Provenance**: The source category that resolved or changed a decision, such as policy, hook, sandbox, classifier, user, runtime, protected path, or session state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests cover all 11 documented decision stages and all 8 documented decision event names.
- **SC-002**: Unit tests prove protected-path and classifier decisions cannot be serialized as user approvals or explicit allow-rule outcomes.
- **SC-003**: Boundary tests prove hook source scopes and outcomes reject unknown values and preserve compact audit data for all documented hook fields.
- **SC-004**: Integration-style tests construct a representative end-to-end decision sequence and verify stage order, event names, hook audits, and final runtime status.
- **SC-005**: Source design coverage for DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-025, and DESIGN-REQ-028 maps to passing validation evidence.
