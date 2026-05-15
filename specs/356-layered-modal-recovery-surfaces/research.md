# Research: Layered Modal Recovery Surfaces

## Repo Gap Analysis

Decision: Treat all THOR-405 implementation requirements as missing in the current workspace.
Evidence: `git remote -v` points at `MoonLadderStudios/MoonMind`; scans found no `.uproject`, `*.Build.cs`, `*.Target.cs`, `Source/ThorTactics`, `UI.Layer.Modal`, `CommonUI`, THOR modal controller, native fallback modal widgets, or THOR automation tests.
Rationale: The trusted Jira brief targets THOR Tactics frontend runtime work, while the active workspace contains MoonMind orchestration code. Planning can define the target behavior and proposed paths, but implementation must occur in the actual THOR repository.
Alternatives considered: Treat MoonMind dashboard modals or React components as partial evidence; rejected because MoonMind Mission Control UI is not the THOR Tactics runtime.
Test implications: Unit and integration automation are required in the target THOR repository.

## FR-001 / Modal Layer Presentation

Decision: Missing; production modal states must route through the target modal layer stack.
Evidence: No target UI manager or modal layer stack exists in this workspace.
Rationale: The acceptance criteria require modal presentation through the UI manager/layout stack rather than ad hoc widget display.
Alternatives considered: Per-modal direct widget creation; rejected because it bypasses the layer stack and would make push/dismiss behavior inconsistent.
Test implications: Unit tests for presentation routing and integration tests proving progress/error/retry/dismiss/confirmation flows appear through the modal layer.

## FR-002 / Shared Native Modal Behavior

Decision: Missing; required modal states need a shared native base or equivalent reusable behavior contract.
Evidence: No target modal base or reusable behavior contract exists in this workspace.
Rationale: Shared interaction behavior reduces drift across progress, blocking error, retry, dismiss, and confirmation surfaces.
Alternatives considered: Implement each modal independently; rejected because the story requires consistent behavior across states.
Test implications: Unit tests for common modal state policy and integration tests proving fallback modal behavior is consistent.

## FR-003 and FR-004 / Progress and Blocking Error Behavior

Decision: Missing; add progress interaction blocking and blocking error acknowledgement/recovery behavior.
Evidence: No target progress or blocking error modal runtime exists in this workspace.
Rationale: Progress and blocking errors are the primary player-visible interruption states and must not allow conflicting interaction.
Alternatives considered: Treat progress and errors as non-modal notifications; rejected because the story requires modal presentation.
Test implications: Unit tests for modal flags and integration tests for interaction blocking and visible blocking error behavior.

## FR-005 and FR-006 / Retry Recovery Action

Decision: Missing; capture optional recovery actions and execute them exactly once per selected retry attempt.
Evidence: No target recovery action capture or retry modal behavior exists in this workspace.
Rationale: Retry must be deterministic and safe: available recovery actions execute, absent actions do not produce undefined behavior.
Alternatives considered: Let Retry re-run the last global frontend command implicitly; rejected because the requirement specifies captured recovery action when available.
Test implications: Unit tests for captured action execution count and absent-action guardrails; integration test for retryable failure flow.

## FR-007 and FR-008 / Dismiss Destination

Decision: Missing; resolve Dismiss to Home by default or an explicit prior state when configured.
Evidence: No target dismiss navigation behavior exists in this workspace.
Rationale: Predictable dismiss behavior is the main recovery guarantee in the user story.
Alternatives considered: Leave dismiss to close the modal only; rejected because the acceptance criteria require Home/default or prior-state navigation.
Test implications: Unit tests for destination selection and integration tests for dismiss-to-Home and dismiss-to-prior-state flows.

## FR-009 / Confirmation Outcome Routing

Decision: Missing; confirmation outcomes must route consistently and remove the modal from the layer stack.
Evidence: No target confirmation modal outcome routing exists in this workspace.
Rationale: Confirmation modals are recovery/decision surfaces and must not leave stale modal layers after an outcome is selected.
Alternatives considered: Let callers close confirmation modals manually after callbacks; rejected because it risks inconsistent stack cleanup.
Test implications: Unit tests for outcome routing and integration tests for confirm/cancel paths.

## FR-010, FR-011, and FR-012 / Native Fallback and Automation

Decision: Missing; add native fallback modals and deterministic automation for modal stack behavior.
Evidence: No THOR fallback modal widgets or modal automation tests are present in this workspace.
Rationale: The story's completion depends on proof that modal recovery surfaces work before final authored presentation assets exist.
Alternatives considered: Manual QA or authored Blueprint-only coverage; rejected because native fallback and automation are explicit acceptance criteria.
Test implications: Required game automation must cover fallback progress, blocking error, retry, dismiss, confirmation, and layer push/dismiss behavior.
