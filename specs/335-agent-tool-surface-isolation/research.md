# Research: Agent Tool-Surface Isolation

## Setup Script

Decision: Planning proceeded manually against `specs/335-agent-tool-surface-isolation`.
Evidence: `.specify/scripts/bash/setup-plan.sh --json` failed because the managed branch is `change-jira-issue-mm-680-to-status-in-pr-6478e030`, not a numeric MoonSpec branch.
Rationale: `.specify/feature.json` already points at the active feature directory, and the feature has a valid single-story `spec.md`.
Alternatives considered: Renaming branches or regenerating the spec was rejected because this managed run should not alter branch identity or valid upstream artifacts.
Test implications: None beyond verifying generated artifacts exist.

## Runtime Identity and Connector Isolation

Decision: Status is `partial`; add explicit launch/session guards rejecting operator-account OAuth or account-level connector grants in managed agent sessions.
Evidence: Managed runtime and provider profile code exists, but repo search did not find a single fail-closed launch guard covering operator-account connectors across managed runtimes. `docs/ManagedAgents/ManagedAgentsGit.md` describes managed publish credential ownership, not account-level connector exclusion.
Rationale: The incident in MM-680 was caused by agent-visible account-level Atlassian access; preventing this must be structural, not prompt-only.
Alternatives considered: Tool-name denylists were rejected by the spec and Constitution XIII because they are brittle compatibility shims.
Test implications: Unit tests for guard validation and integration_ci launch tests with simulated disallowed connector grants.

## Skill Contract Surface Metadata

Decision: Status is `partial`; extend resolved skill contract metadata to include closed tool, MCP, connector, and egress declarations.
Evidence: `moonmind/workflows/skills/tool_plan_contracts.py` validates executable tool definitions and `moonmind/workflows/skills/run_projection.py` verifies selected active skill projection, but neither evidence set proves full runtime tool/MCP/egress closure.
Rationale: Existing selected-skill projection is necessary but not sufficient; MM-680 requires the runtime launcher to know exactly which external surfaces are allowed.
Alternatives considered: Reusing only selected skill names was rejected because names do not encode allowed MCPs, connector scopes, or egress destinations.
Test implications: Unit tests for schema validation, manifest resolution, fail-closed missing fields, and rejected mismatched runtime surfaces.

## Runtime Launcher Enforcement

Decision: Status is `partial`; add a shared launcher validation step that compares requested runtime surfaces with the resolved skill contract before starting the agent.
Evidence: `AgentRunWorkflow` resolves selected skills and `verify_skill_projection()` fails fast on projection errors; `moonmind/workflows/temporal/runtime/launcher.py` materializes and verifies selected skill projection. No complete diff was found for allowed tools, MCPs, connector surfaces, and egress.
Rationale: Enforcement must happen before agent process startup to prevent bypass opportunities.
Alternatives considered: Runtime-specific command-line hints alone were rejected because future runtimes may expose different flags or shell paths.
Test implications: Unit tests for the shared validator and integration_ci tests proving launch denial before runtime work begins.

## Egress Mediation

Decision: Status is `partial`; add per-skill egress policy materialization and blocked-egress diagnostics.
Evidence: `moonmind/workflows/temporal/workers.py` assigns coarse `restricted-sandbox-egress` to agent runtime fleets, but MM-680 requires an allowlist keyed to the skill contract.
Rationale: A fleet-level label is too broad to prove skill-specific external-service isolation.
Alternatives considered: A global static allowlist was rejected because skills need different allowed services and over-broad allowlists recreate the original problem.
Test implications: Unit tests for allowlist rendering and integration_ci tests for denied non-contract hosts.

## Publish Authority Isolation

Decision: Status is `partial`; remove usable publish authority from agent runtime sessions and add direct publish denial evidence.
Evidence: `docs/ManagedAgents/ManagedAgentsGit.md` states `PublishActivity` commits and pushes for managed agents. `moonmind/workflows/temporal/activity_runtime.py` contains MoonMind-owned post-agent push behavior. Tests also filter skill projection from publish staging. The repo evidence does not prove the agent session itself cannot use `git push`, `gh pr create`, or raw provider calls.
Rationale: MM-680 requires the agent deliverable to end at the working tree and all publish side effects to occur in a MoonMind boundary.
Alternatives considered: Prompt instructions telling agents not to publish were rejected because the original incident shows agents can choose alternate surfaces.
Test implications: Integration_ci tests with representative direct publish attempts and assertions that no external mutation occurs.

## Pull Request Adoption

Decision: Status is `missing`; implement existing pull request lookup/adoption before creation.
Evidence: `GitHubService.create_pull_request()` directly POSTs to `https://api.github.com/repos/{repo}/pulls`; on HTTP errors it returns a failed result. `MoonMind.Run` calls `repo.create_pr` and raises when no URL returns except for current no-commits handling.
Rationale: A pre-existing PR for the intended head/base is a successful reconciled state, not a terminal duplicate failure.
Alternatives considered: Treating HTTP 422 as no-op was rejected because the service needs a URL/head SHA and should distinguish duplicate PR from no commits or other validation failures.
Test implications: Unit tests for GitHubService adoption and workflow/activity tests asserting adopted PR output is accepted.

## Branch Publish Lease Handling

Decision: Status is `missing`; implement lease-aware branch publishing with structured retryable conflict outcomes.
Evidence: `_push_workspace_changes_if_needed()` uses `git push -u origin <branch>` and returns generic `push_status: failed` with stderr on failure. No activity-recorded remote SHA or `--force-with-lease` flow was found.
Rationale: MM-680 requires races to recover or surface retryable conflicts, not non-retryable terminal failures.
Alternatives considered: Plain push retry was rejected because it can overwrite or repeatedly fail without a structured operator path.
Test implications: Unit tests around push command construction/outcome classification and integration-style tests for lease miss handling.

## Diagnostics and Telemetry

Decision: Status is `partial`; define a compact `IsolationDiagnostic` event/ref shape for blocked runtime surfaces and publish reconciliation.
Evidence: Existing runtime projection and publish code emit some status fields/logs, but there is no unified sanitized envelope for blocked egress, rejected tool loads, direct publish attempts, adopted PRs, or lease conflicts.
Rationale: Operators need attributable evidence without secrets, and downstream verification needs deterministic outputs.
Alternatives considered: Raw logs were rejected because they may be noisy, non-deterministic, or secret-bearing.
Test implications: Unit tests for redaction/shape and integration_ci tests for event emission at runtime/activity boundaries.

## Test Strategy

Decision: Use both unit and hermetic integration tests; provider verification is not required for merge.
Evidence: Repo instructions require `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for `integration_ci`; workflow boundary changes require boundary-level coverage.
Rationale: This story crosses Temporal activity/workflow contracts and runtime adapter boundaries, so isolated unit tests alone are insufficient.
Alternatives considered: Live provider tests were rejected for required CI because they depend on credentials and are not hermetic.
Test implications: Add targeted unit tests first, then integration_ci coverage for runtime launch denial, egress denial, and direct publish denial using local/mocked surfaces.
