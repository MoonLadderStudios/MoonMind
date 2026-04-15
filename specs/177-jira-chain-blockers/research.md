# Research: Jira Chain Blockers

## Dependency Mode Contract

Decision: Add an explicit Jira dependency mode with supported values `none` and `linear_blocker_chain`.

Rationale: The Jira preset brief names those two modes, and fail-fast validation is required so unsupported values cannot silently change billing-relevant or execution behavior. A small closed mode set keeps the first story independently testable.

Alternatives considered: Boolean `create_links` was rejected because it cannot distinguish future link strategies. Free-form mode strings were rejected because they would shift validation into prompts or provider behavior.

## Link Creation Boundary

Decision: Add issue-link creation to `JiraToolService` and the Jira request models/client path, then invoke it from `story.create_jira_issues`.

Rationale: Existing Jira issue creation already goes through `JiraToolService`, which enforces enabled state, allowed actions, allowed projects, bounded retries, and sanitized errors. Keeping link creation there satisfies the trusted-boundary requirement and avoids raw Jira calls from agent shells.

Alternatives considered: Having `jira-issue-creator` call Jira directly was rejected because it bypasses the trusted tool boundary. Encoding link instructions only in issue descriptions was rejected because the story requires actual Jira dependency links.

## Ordered Story Mapping

Decision: Derive the linear chain from the actual ordered story list used for issue creation and keep a stable per-story mapping in the output.

Rationale: `story.create_jira_issues` already iterates the resolved breakdown order and returns created/reused issues. Using the same ordered list prevents a second ordering format and lets existing issue reuse participate in link creation.

Alternatives considered: Reading only explicit story dependency arrays was rejected for the initial mode because the required behavior is adjacent linear blocking. Ignoring story IDs was rejected because retry diagnostics and future extension need stable mapping.

## Idempotency And Retry Behavior

Decision: Treat created/reused issue mappings and created/reused/skipped/failed link results as first-class output. Check for existing links where the trusted Jira service can support it; otherwise handle Jira duplicate-link responses as reusable existing state.

Rationale: Existing issue reuse uses workflow marker labels and summary matching. Link creation needs equivalent honest reporting so reruns do not claim full fresh creation or silently duplicate links.

Alternatives considered: Failing all reruns was rejected because existing issue reuse already supports retry resilience. Blindly attempting links without classifying duplicate responses was rejected because it would not meet the retry/idempotency acceptance scenario.

## Preset And Agent Skill Alignment

Decision: Add dependency-mode instructions to the seeded Jira Breakdown preset and mirror the same contract in `.agents/skills/jira-issue-creator/SKILL.md` and `.agents/skills/moonspec-breakdown/SKILL.md`.

Rationale: The feature explicitly covers both preset-driven agent skill flow and deterministic structured-output flow. The preset should request the mode, while skills should preserve ordered story IDs/dependencies and route link creation through trusted Jira tools.

Alternatives considered: Updating only deterministic `story.create_jira_issues` was rejected because it would leave the agent-skill path divergent. Updating only prompt text was rejected because the deterministic tool path would not create links.

## Test Strategy

Decision: Use focused unit tests for the pure and adapter-boundary behavior, plus the existing task-template catalog tests for preset expansion.

Rationale: Jira provider calls are external and credentialed, so required validation should stub the trusted Jira service. Unit tests can prove request shapes, output contracts, partial failure, and retry/reuse behavior without credentials.

Alternatives considered: Provider verification tests were rejected for required CI because they need real Jira credentials. Full Temporal time-skipping tests were rejected for this story because no workflow signature or Temporal payload shape changes are planned.
