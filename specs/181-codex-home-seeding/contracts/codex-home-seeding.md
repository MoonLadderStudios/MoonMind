# Contract: Per-Run Codex Home Seeding

## Story Boundary

Source story: `STORY-003` from Jira issue `MM-357` and source design `docs/ManagedAgents/OAuthTerminal.md`.

## Inputs

- Jira traceability: `MM-357` and preset brief `MM-357: Per-Run Codex Home Seeding`.
- Source design coverage: DESIGN-REQ-005, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-019, DESIGN-REQ-020.
- Dependency requirements: STORY-002.

## Required Behavior

- Create the per-run codexHomePath under the task workspace.
- Copy only eligible auth entries from MANAGED_AUTH_VOLUME_PATH into codexHomePath.
- Start Codex App Server with CODEX_HOME = codexHomePath.
- Keep runtime home directories out of operator/audit presentation.

## Outputs

- Deterministic validation or state outcome for this story.
- Secret-free metadata only.
- Test evidence from both unit strategy and integration strategy when the integration environment is available.

## Failure Behavior

- Unsupported, blank, unsafe, or secret-bearing values fail fast.
- Validation failures must be actionable and must not include raw credential contents.
- Integration blockers such as missing Docker socket must be recorded explicitly rather than treated as success.
