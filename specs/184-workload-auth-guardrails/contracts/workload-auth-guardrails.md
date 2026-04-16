# Contract: Workload Auth-Volume Guardrails

## Story Boundary

Source story: `STORY-006` from Jira issue `MM-360` and source design `docs/ManagedAgents/OAuthTerminal.md`.

## Inputs

- Jira traceability: `MM-360` and preset brief `MM-360: Workload Auth-Volume Guardrails`.
- Source design coverage: DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-020.
- Dependency requirements: None.

## Required Behavior

- Enforce workload profile mount allowlists.
- Reject implicit managed-runtime auth-volume inheritance.
- Require explicit justification/profile declaration for any credential mount.
- Keep workload containers separate from managed session identity fields.

## Outputs

- Deterministic validation or state outcome for this story.
- Secret-free metadata only.
- Test evidence from both unit strategy and integration strategy when the integration environment is available.

## Failure Behavior

- Unsupported, blank, unsafe, or secret-bearing values fail fast.
- Validation failures must be actionable and must not include raw credential contents.
- Integration blockers such as missing Docker socket must be recorded explicitly rather than treated as success.
