# Contract: Codex Managed Session Volume Targeting

## Story Boundary

Source story: `STORY-002` from Jira issue `MM-356` and source design `docs/ManagedAgents/OAuthTerminal.md`.

## Inputs

- Jira traceability: `MM-356` and preset brief `MM-356: Codex Managed Session Volume Targeting`.
- Source design coverage: DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-017, DESIGN-REQ-020.
- Dependency requirements: STORY-001.

## Required Behavior

- Mount agent_workspaces into every managed Codex session container.
- Conditionally mount codex_auth_volume only when explicitly set by selected profile or launcher policy.
- Reject auth-volume targets that equal codexHomePath.
- Pass reserved session environment values into the container.

## Outputs

- Deterministic validation or state outcome for this story.
- Secret-free metadata only.
- Test evidence from both unit strategy and integration strategy when the integration environment is available.

## Failure Behavior

- Unsupported, blank, unsafe, or secret-bearing values fail fast.
- Validation failures must be actionable and must not include raw credential contents.
- Integration blockers such as missing Docker socket must be recorded explicitly rather than treated as success.
