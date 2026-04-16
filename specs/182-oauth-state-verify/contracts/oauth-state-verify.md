# Contract: OAuth Session State and Verification Boundaries

## Story Boundary

Source story: `STORY-005` from Jira preset brief `MM-359: OAuth Session State and Verification Boundaries`.

## Inputs

- Jira traceability: `MM-359` and preset brief `MM-359: OAuth Session State and Verification Boundaries`.
- Source design coverage: DESIGN-REQ-010, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-018, DESIGN-REQ-020.
- Dependency requirements: STORY-001, STORY-002.

## Required Behavior

- Use transport-neutral OAuth statuses.
- Allow session_transport = none while the interactive bridge is disabled.
- Verify durable auth volume credentials before Provider Profile registration.
- Verify selected profile materialization at managed-session launch.
- Keep verification outputs compact and secret-free.

## Outputs

- Deterministic validation or state outcome for this story.
- Secret-free metadata only.
- Test evidence from both unit strategy and integration strategy when the integration environment is available.

## Failure Behavior

- Unsupported, blank, unsafe, or secret-bearing values fail fast.
- Validation failures must be actionable and must not include raw credential contents.
- Integration blockers such as missing Docker socket must be recorded explicitly rather than treated as success.
