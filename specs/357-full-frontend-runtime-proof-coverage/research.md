# Research: Full Frontend Runtime Proof Coverage

## Workspace Classification

Decision: The input is a single-story runtime feature request, not a broad design and not an existing feature directory.
Evidence: The trusted Jira brief contains one user story and one cohesive acceptance set for runtime proof coverage. Repository scans found no existing THOR-406 spec, no `.uproject`, no `Source/ThorTactics`, no `TacticsEditor`, no `MainMenu` map, and no THOR frontend runtime files.
Rationale: The story can be validated independently by the three-tier evidence workflow, but the target implementation cannot run in this MoonMind repository.
Alternatives considered: Treat as a broad design; rejected because it does not request multiple independently deliverable stories. Treat as docs-only; rejected because the brief requires runtime validation behavior.
Test implications: All runtime implementation and validation tests must run in a THOR Tactics workspace.

## FR-001 and FR-002 / Tier 1 Compile Evidence

Decision: Missing; add a compile validation harness that records TacticsEditor command, exit code, and concise output evidence.
Evidence: No Unreal project or TacticsEditor target exists in this checkout.
Rationale: Compile evidence is the first acceptance criterion and establishes that runtime proof starts from a buildable editor target.
Alternatives considered: Rely on CI compile status only; rejected because the story requires exact command and exit-code evidence.
Test implications: Integration validation must cover successful and failing compile evidence records where target tooling permits.

## FR-003 through FR-009 / Tier 2 Frontend Automation

Decision: Missing; add automation covering Home startup, generated Home navigation, Play panel, Options panel, modal behavior, Online Co-op blocking, and generated selection telemetry.
Evidence: Adjacent specs describe THOR menu runtime behavior, but no THOR runtime or automation source exists in this checkout.
Rationale: Unit-level widget construction is insufficient for the requested proof; the automation must exercise integrated runtime flows.
Alternatives considered: Add isolated widget tests only; rejected because the Jira brief explicitly asks for runtime proof beyond unit-level widget construction.
Test implications: Integration automation is required for the flow set, with unit coverage only for telemetry/evidence formatting seams.

## FR-010 / Tier 3 Entry Smoke

Decision: Missing; add a runtime smoke through `/Game/Maps/MainMenu` or the active frontend entry route.
Evidence: No map assets or route definitions exist in this checkout.
Rationale: A map or entry smoke proves the frontend is reachable from a real runtime entry point rather than only from constructed fixtures.
Alternatives considered: Hardcode `/Game/Maps/MainMenu`; rejected because the brief allows the active frontend entry route when that is the correct target.
Test implications: Integration smoke must record the route used and whether it is the configured active entry.

## FR-011 / Evidence Record Contents

Decision: Missing; define a deterministic evidence record with command, exit code, output summary, and key `LogTactics` lines.
Evidence: No THOR evidence schema exists in this checkout.
Rationale: Deterministic evidence allows reviewers to evaluate runtime proof without rerunning the workflow.
Alternatives considered: Paste full logs; rejected because concise key lines are more reviewable and avoid noisy PR descriptions.
Test implications: Unit tests should cover evidence record formatting and log extraction.

## FR-012 / Local Tooling and Docker Fallback

Decision: Missing; add fallback decision logic that attempts Docker when local tooling is unavailable before declaring CI-only validation.
Evidence: No target validation wrapper exists in this checkout.
Rationale: The acceptance criteria require fallback behavior, and it keeps validation resilient across developer environments.
Alternatives considered: Immediately declare CI-only when local Unreal tooling is missing; rejected by the Jira brief.
Test implications: Unit tests should simulate missing local tools and verify Docker fallback is attempted before CI-only classification.

## FR-013 / Quickstart and PR-Ready Reporting

Decision: Missing; update the feature quickstart and PR-ready reporting output with all validation tier results.
Evidence: This spec has a new quickstart, but no target PR evidence output exists yet.
Rationale: Reviewers need evidence in both the spec validation guide and PR summary.
Alternatives considered: Store evidence only in local logs; rejected because the brief requires quickstart and PR description reporting.
Test implications: Integration validation should assert the generated evidence summary includes all three tiers.

## FR-014 / Non-Goal Guard

Decision: Implemented unverified at artifact level; the spec and tasks constrain the work to proof coverage, but target source cannot be inspected here.
Evidence: `spec.md` FR-014 and tasks preserve this as a non-goal.
Rationale: The proof coverage story must not expand into frontend feature implementation.
Alternatives considered: Include menu feature repair tasks; rejected because THOR-406 is validation-only unless tests reveal missing evidence seams.
Test implications: Final verification must confirm implementation changes are limited to validation/evidence surfaces unless target tests prove a minimal support seam is necessary.
