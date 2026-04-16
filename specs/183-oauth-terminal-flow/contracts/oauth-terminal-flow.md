# Contract: OAuth Terminal Enrollment Flow

## Story Boundary

Source story: `STORY-004` from the OAuth Terminal design coverage preserved in Jira issue `MM-358`.

## Inputs

- Jira traceability: `MM-358` and preset brief `MM-358: OAuth Terminal Enrollment Flow`.
- Source design coverage: DESIGN-REQ-001, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-020.
- Dependency requirements: STORY-001, STORY-005.

## Required Behavior

- Create OAuth sessions through the API.
- Start short-lived auth runner containers with target auth volume mounted at provider enrollment path.
- Attach Mission Control through authenticated PTY/WebSocket bridge rendered with xterm.js.
- Enforce TTL, ownership, resize/heartbeat handling, close metadata, and cleanup.

## Outputs

- Deterministic validation or state outcome for this story.
- Secret-free metadata only.
- Test evidence from both unit strategy and integration strategy when the integration environment is available.

## Failure Behavior

- Unsupported, blank, unsafe, or secret-bearing values fail fast.
- Validation failures must be actionable and must not include raw credential contents.
- Integration blockers such as missing Docker socket must be recorded explicitly rather than treated as success.
