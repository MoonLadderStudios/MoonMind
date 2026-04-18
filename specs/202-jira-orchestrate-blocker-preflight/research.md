# Research: Jira Orchestrate Blocker Preflight

## Planning Setup

Decision: Generate planning artifacts manually from the active feature directory.

Rationale: `.specify/scripts/bash/setup-plan.sh --json` rejects the managed branch name `mm-398-e3573b0c` because it expects `###-feature-name`. The active feature is already recorded in `.specify/feature.json`, and manual artifact generation preserves the MoonSpec plan template semantics without changing branch state.

Alternatives considered: Renaming or switching branches was rejected because this managed run should preserve the current workspace branch and avoid unrelated git operations.

## Agent Context Update

Decision: Record the agent-context update as blocked by the same managed branch naming mismatch.

Rationale: `.specify/scripts/bash/update-agent-context.sh` looked for `specs/mm-398-e3573b0c/plan.md` from the current branch name instead of the active feature directory in `.specify/feature.json`. The generated planning artifacts remain valid and are located under `specs/202-jira-orchestrate-blocker-preflight`.

Alternatives considered: Creating a duplicate branch-named spec directory was rejected because it would split the canonical MoonSpec artifact location and conflict with global spec numbering rules.

## Implementation Surface

Decision: Implement the story by updating the seeded `jira-orchestrate` task template and the tests that validate seed synchronization and expansion.

Rationale: The current Jira Orchestrate flow is a YAML-composed preset whose behavior is asserted by catalog and startup seeding tests. MM-398 asks for orchestration to stop before implementation when Jira blocker state is unresolved; a pre-implementation preset step is the narrowest place to add that behavior while keeping existing MoonSpec and pull request stages unchanged.

Alternatives considered: Adding a new backend workflow was rejected because Jira Orchestrate is currently expressed as a task preset, not a dedicated workflow. Adding a new database table or stored state was rejected because the decision is per-run and can be represented in the preflight step result.

## Trusted Jira Boundary

Decision: The blocker preflight must instruct the runtime to use trusted Jira tool calls and fail closed when needed data is unavailable.

Rationale: The existing Jira Orchestrate preset already depends on trusted Jira issue updater/fetch behavior. MM-398 explicitly forbids guessing from prompt text alone and requires trusted Jira data. Fail-closed behavior prevents dependent implementation work from starting when blocker status cannot be proven Done.

Alternatives considered: Raw Jira REST calls, browser scraping, or raw credentials in agent shells were rejected because they violate the trusted integration boundary and secret hygiene requirements.

## Blocker Semantics

Decision: Treat only Jira status `Done` as satisfying a blocking prerequisite for this story; any detected blocker that is non-Done or whose status is unavailable blocks orchestration.

Rationale: The Jira preset brief uses "not done yet" as the criterion and the spec records `Done` as the safe conventional default. This keeps behavior deterministic and easy to test.

Alternatives considered: Treating any terminal status category as satisfied was rejected because the brief names Done specifically and Jira status-category details may not always be present in the trusted response. Allowing unknown statuses to proceed was rejected because it risks starting dependent work too early.

## Test Strategy

Decision: Use focused unit tests for preset catalog expansion plus startup seed synchronization integration coverage, then run the repository unit test runner for final unit verification.

Rationale: Existing tests already assert the Jira Orchestrate step sequence, step count, PR handoff guard, and startup seeding. Updating those tests red-first will prove the new preflight step is present before implementation stages and that seed synchronization preserves it.

Alternatives considered: Provider verification tests with real Jira credentials were rejected for required CI because MoonMind separates provider verification from required unit and hermetic integration tests. End-to-end browser tests were rejected for this story because the behavior is seeded preset expansion, not a new browser interaction.
