# Research: Menu Action Availability and Unavailable Presentation

## Repo Gap Analysis

Decision: Treat all THOR-403 implementation requirements as missing in the current workspace.
Evidence: `git remote -v` points at `MoonLadderStudios/MoonMind`; scans found no `.uproject`, `*.Build.cs`, `*.Target.cs`, `Source/ThorTactics`, `FTacticsMenuActionEntry`, generated menu button source, Online Co-op menu action, or THOR automation tests.
Rationale: The trusted Jira brief targets THOR Tactics menu runtime work, while the active workspace contains MoonMind orchestration code. Planning can define the target behavior and proposed paths, but implementation must occur in the actual THOR repository.
Alternatives considered: Treat similarly named MoonMind frontend disabled-action behavior as partial evidence; rejected because MoonMind task UI is not the THOR Tactics runtime.
Test implications: Unit and integration automation are required in the target THOR repository.

## FR-001 / Eligibility Outcomes

Decision: Missing; add a shared eligibility outcome model with enabled, disabled-visible, and hidden-by-window states.
Evidence: No target menu action model exists in this workspace.
Rationale: The story depends on distinguishing unavailable-but-visible actions from actions that should not be rendered in the current window.
Alternatives considered: A boolean enabled flag plus separate visibility flag; rejected unless wrapped in one explicit outcome because tests and renderers need one unambiguous decision.
Test implications: Unit tests should cover all three outcomes and precedence when an action is both ineligible and outside the current window.

## FR-002 and FR-009 / Unavailable Reason Text

Decision: Missing; expose player-facing unavailable reason text through the action entry or eligibility result, with deterministic fallback copy.
Evidence: No unavailable reason field or result was found.
Rationale: Disabled-visible buttons need consistent copy across generated panels, and missing authored copy should not produce an empty or confusing disabled state.
Alternatives considered: Only log developer diagnostics for unavailable actions; rejected because the story requires player-facing messaging.
Test implications: Unit tests for authored reason, eligibility-produced reason, and fallback reason.

## FR-003, FR-004, and FR-005 / Generated Button Rendering

Decision: Missing; generated menu buttons should render enabled actions, disabled-visible actions, and hidden actions according to the shared eligibility outcome.
Evidence: No generated menu button renderer exists in this workspace.
Rationale: Central rendering behavior avoids divergent Play/Home/Options behavior and lets future panels inherit the contract.
Alternatives considered: Per-panel custom handling; rejected because the story requires the same generated-button behavior across panels.
Test implications: Unit tests for render models and integration tests for visible/hidden controls.

## FR-006 and FR-007 / Online Co-op Blocked Selection

Decision: Missing; Online Co-op should remain visible while blocked and selection should show feedback without travel or session side effects.
Evidence: No Online Co-op menu action, travel, matchmaking, or session entry point exists in this workspace.
Rationale: Online Co-op is the required concrete blocked-action case, and side-effect prevention is safety-critical for user flow correctness.
Alternatives considered: Hide Online Co-op until available; rejected because the acceptance criteria require it to remain visible while blocked.
Test implications: Integration automation should assert the visible disabled state, feedback, and zero calls to travel/session operations.

## FR-008 / Cross-Panel Reuse

Decision: Missing; Play, Home navigation, Options, and future generated-button panels should route through the same generated action rendering path.
Evidence: No target generated-button panel path exists in this workspace.
Rationale: Shared behavior prevents inconsistent disabled messaging and supports future panels without new bespoke handling.
Alternatives considered: Implement the behavior only in the Play menu; rejected because the story requires Play, Home navigation, Options, and future panels.
Test implications: Integration tests should use fixtures or panel instances for the named panels and one future-compatible generated panel path.

## FR-010 and Success Criteria / Automation

Decision: Missing; add deterministic unit and automation coverage before implementation.
Evidence: No THOR automation tests are present in this workspace.
Rationale: Completion depends on proving enabled, disabled-visible, hidden-by-window, and blocked-selection behavior.
Alternatives considered: Manual QA only; rejected because automation is explicit acceptance criteria.
Test implications: Required unit tests and game automation tests must run in the actual THOR repository.
