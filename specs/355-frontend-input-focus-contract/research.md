# Research: Frontend Input and Focus Contract

## Repo Gap Analysis

Decision: Treat all THOR-404 implementation requirements as missing in the current workspace.
Evidence: `git remote -v` points at `MoonLadderStudios/MoonMind`; scans found no `.uproject`, `*.Build.cs`, `*.Target.cs`, `Source/ThorTactics`, `CommonUI`, `TacticsMenu`, generated menu button source, menu coordinator, or THOR automation tests.
Rationale: The trusted Jira brief targets THOR Tactics menu runtime work, while the active workspace contains MoonMind orchestration code. Planning can define the target behavior and proposed paths, but implementation must occur in the actual THOR repository.
Alternatives considered: Treat similarly named MoonMind frontend focus or button behavior as partial evidence; rejected because MoonMind task UI is not the THOR Tactics runtime.
Test implications: Unit and integration automation are required in the target THOR repository.

## FR-001 / Shared Input Behavior Contract

Decision: Missing; add shared menu surface defaults for confirm and cancel/back while a screen or panel is active.
Evidence: No target menu screen or panel base class exists in this workspace.
Rationale: Input behavior must be consistent across fallback screens and panels rather than defined independently per surface.
Alternatives considered: Configure input per panel only; rejected because the story requires shared menu base behavior.
Test implications: Unit tests for default input config and integration tests proving active surfaces respond consistently.

## FR-002 and FR-003 / Focusable Generated Buttons and Initial Focus

Decision: Missing; generated buttons must be focusable and activation must assign a valid initial focus target.
Evidence: No target generated action button or focus assignment code exists in this workspace.
Rationale: Keyboard and controller navigation cannot work reliably unless generated actions participate in focus and a starting target is selected.
Alternatives considered: Rely on authored widget focus defaults only; rejected because native fallback widgets must work without authored presentation assets.
Test implications: Unit tests for focus candidate selection and integration tests for initial focus on Home, Play, and Options fallback widgets.

## FR-004 / Shared Coordinator Activation

Decision: Missing; pointer click, keyboard confirm, and controller confirm should route through one coordinator behavior for the selected generated action.
Evidence: No menu coordinator or activation path exists in this workspace.
Rationale: Separate activation paths can drift and create inconsistent menu behavior between pointer and controller/keyboard users.
Alternatives considered: Keep pointer and confirm handlers separate with duplicated logic; rejected because the story requires the same coordinator path.
Test implications: Unit tests using coordinator call spies and integration tests exercising mouse, keyboard, and controller input.

## FR-005 / Back and Cancel Navigation

Decision: Missing; child panels should dismiss or return to the prior frontend state on Back/Cancel.
Evidence: No target frontend state stack or cancel/back handling exists in this workspace.
Rationale: Native fallback menus must be fully navigable without relying on authored Blueprint behavior.
Alternatives considered: Leave Back/Cancel undefined on fallback panels; rejected because the acceptance criteria require dismissal or previous-state navigation.
Test implications: Integration tests for Play and Options child surfaces, plus unit tests for root no-op or exit behavior preserving valid focus.

## FR-006, FR-007, and FR-008 / Home Focus Restoration

Decision: Missing; returning from Play or Options should restore focus to the originating Home navigation action, with a valid fallback when that target is unavailable.
Evidence: No Home focus restoration code exists in this workspace.
Rationale: Restoring focus keeps controller and keyboard users oriented after leaving child surfaces.
Alternatives considered: Always focus the first Home action; rejected because the story specifically requires Play and Options return targets when valid.
Test implications: Integration tests for Play-to-Home and Options-to-Home flows, plus unit tests for unavailable return target fallback.

## FR-009 and FR-010 / Native Fallback Automation

Decision: Missing; add deterministic automation covering native-only fallback widgets for the full input/focus contract.
Evidence: No THOR automation tests or native fallback widgets are present in this workspace.
Rationale: The story's completion depends on proof that fallback widgets are navigable before final authored presentation assets exist.
Alternatives considered: Manual QA only; rejected because automation is explicit acceptance criteria.
Test implications: Required game automation must cover initial focus, confirm parity, cancel/back, focus restoration, and authored-asset-absent fallback flows.
