# Contract: Codex Auth Volume Profile Contract

## Story Boundary

Source story: `STORY-001` from `artifacts/story-breakdowns/mm-318-breakdown-docs-managedagents-oauthterminal-md/stories.json`.

## Inputs

- Jira traceability: `MM-318` and preset brief `MM-318: breakdown docs\ManagedAgents\OAuthTerminal.md`.
- Source design coverage: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-016, DESIGN-REQ-020.
- Dependency requirements: None.

## Required Behavior

- Define and enforce the Codex OAuth Provider Profile shape for credential_source = oauth_volume and runtime_materialization_mode = oauth_home.
- Preserve volume_ref, volume_mount_path, and slot policy fields during profile registration or update.
- Keep raw credential file contents out of API responses, workflow payloads, logs, and artifacts.
- Keep Claude and Gemini task-scoped managed-session parity out of scope.

## Outputs

- Deterministic validation or state outcome for this story.
- Secret-free metadata only.
- Test evidence from both unit strategy and integration strategy when the integration environment is available.

## Failure Behavior

- Unsupported, blank, unsafe, or secret-bearing values fail fast.
- Validation failures must be actionable and must not include raw credential contents.
- Integration blockers such as missing Docker socket must be recorded explicitly rather than treated as success.
