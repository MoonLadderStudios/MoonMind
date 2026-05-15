# Research: Native Options Menu Surface

## Repo Gap Analysis

Decision: Treat all THOR-402 implementation requirements as missing in the current workspace.
Evidence: `git remote -v` points at `MoonLadderStudios/MoonMind`; scans found no `.uproject`, `*.Build.cs`, `*.Target.cs`, C++ menu source, `frontend.nav.options`, or `frontend.options.*` identifiers outside this feature spec.
Rationale: The Jira brief targets THOR Tactics menu work, while the active workspace contains MoonMind orchestration code. Planning can define the target behavior and propose concrete THOR paths, but implementation must confirm those paths against the actual THOR repository before editing code.
Alternatives considered: Mark behavior as implemented_unverified based on similar wording in docs or tests; rejected because no target runtime code exists here.
Test implications: Both unit and integration tests are required in the target THOR repository.

## FR-001 / Home Options Entry

Decision: Missing; add a Home navigation action that opens Options.
Evidence: No `frontend.nav.options` or Home menu implementation was found in current workspace.
Rationale: The user must be able to reach Options from Home as the primary story path.
Alternatives considered: Open Options only through debug/temporary routes; rejected because the story requires Home reachability.
Test implications: Unit coverage for navigation action binding; integration coverage for Home -> Options.

## FR-002 / Stable Category Identifiers

Decision: Missing; define stable identifiers for Video, Audio, and Input.
Evidence: No `frontend.options.video`, `frontend.options.audio`, or `frontend.options.input` identifiers were found in current workspace.
Rationale: Stable identifiers let authored data, fallback entries, tests, and final presentation agree on the same category contract.
Alternatives considered: Display-name-only categories; rejected because display names are not stable enough for automation or authored data.
Test implications: Unit tests should assert all required identifiers are produced.

## FR-003 and FR-004 / Authored Data with Fallback Entries

Decision: Missing; category actions should prefer authored data and fill required missing categories from fallback entries.
Evidence: No Options category data source or fallback list was found.
Rationale: The Options surface must work before final authored data exists and continue to support authored data later.
Alternatives considered: Require authored data before Options opens; rejected because missing data must still show a usable menu.
Test implications: Unit tests for full authored data, empty authored data, and partial authored data; integration test for missing authored data.

## FR-005 / Missing Presentation Assets

Decision: Missing; baseline surface must render without final authored presentation assets.
Evidence: No baseline Options surface exists in current workspace.
Rationale: The story explicitly requires a native baseline before final presentation is authored.
Alternatives considered: Block Options until authored assets are available; rejected because that violates fallback usability.
Test implications: Integration automation must run with authored Options presentation assets absent.

## FR-006 and FR-007 / Back and Focus Restoration

Decision: Missing; Back/Cancel must return to Home and restore focus to the Options navigation action.
Evidence: No Options back/cancel or focus restoration code was found.
Rationale: Players need predictable controller/keyboard navigation and a stable return point.
Alternatives considered: Return to Home without focus restoration; rejected because the acceptance criteria require restored focus.
Test implications: Unit test navigation state; integration test actual Back/Cancel flow and focused action.

## FR-008 / No Settings Persistence

Decision: Missing as an explicit boundary; tasks should avoid adding save/load behavior.
Evidence: No target settings persistence code is present in this workspace.
Rationale: The Jira brief explicitly excludes persistence unless a separate story adds it.
Alternatives considered: Add placeholder saved values; rejected as out of scope.
Test implications: Unit tests can assert category actions are displayed without requiring saved settings state.

## FR-009 and Success Criteria / Automation

Decision: Missing; add end-to-end automation for Home -> Options -> Back using only the baseline surface.
Evidence: No THOR automation tests are present in current workspace.
Rationale: The story's completion evidence depends on automation through the player-visible flow.
Alternatives considered: Manual QA only; rejected because automation is an acceptance criterion.
Test implications: Integration/automation coverage is required before implementation is considered complete.
