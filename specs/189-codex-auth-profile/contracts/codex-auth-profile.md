# Contract: Codex Auth Volume Profile Contract

## Story Boundary

Source story: `STORY-001` from Jira issue `MM-355`.

This contract covers Codex OAuth Provider Profile registration/update, validation, serialization, and workflow-facing profile snapshots. It does not cover interactive OAuth terminal UI, managed-session container launch, Codex App Server startup, auth seeding into per-run homes, or Docker workload credential inheritance.

## Inputs

- Jira traceability: `MM-355` and the original preset brief preserved in `specs/189-codex-auth-profile/spec.md`.
- Source design coverage: `DESIGN-REQ-001`, `DESIGN-REQ-002`, `DESIGN-REQ-003`, `DESIGN-REQ-010`, `DESIGN-REQ-016`, and `DESIGN-REQ-020`.
- OAuth verification evidence:
  - runtime id
  - provider id
  - credential source
  - runtime materialization mode
  - volume ref
  - volume mount path
  - sanitized verification status or failure reason
- Existing Provider Profile data when repairing or updating a profile.
- Slot policy metadata when present.

## Required Behavior

- Define and enforce Codex OAuth Provider Profile shape:
  - `runtime_id = codex_cli`
  - `credential_source = oauth_volume`
  - `runtime_materialization_mode = oauth_home`
  - non-blank `volume_ref`
  - non-blank `volume_mount_path`
  - concrete Codex-supported provider identity
- Register or update a Provider Profile from verified OAuth evidence without creating a parallel durable auth store.
- Preserve `volume_ref`, `volume_mount_path`, provider identity, materialization mode, and slot policy metadata during create, update, repair, serialization, and snapshot construction.
- Keep Claude and Gemini task-scoped managed-session parity out of scope.
- Preserve Provider Profile ownership of credential refs, slot policy, and profile metadata while leaving session launch, runtime startup, OAuth terminal enrollment, and workload orchestration to their own boundaries.

## Outputs

- Deterministic validation result for Codex OAuth Provider Profile input.
- Created or updated Provider Profile metadata containing refs and policy values only.
- Operator-facing profile response that excludes credential contents.
- Workflow-facing profile snapshot that excludes credential contents.
- Sanitized validation failure details when unsafe values are rejected.
- Test evidence from required unit strategy and integration strategy when integration infrastructure is available.

## Failure Behavior

- Missing, blank, unsupported, unsafe, or secret-bearing profile values fail fast before externally visible side effects.
- Validation failures must be actionable and must not include raw credential contents.
- Serialization must reject or redact raw credential-bearing nested metadata rather than exposing it.
- Integration blockers such as missing Docker socket must be recorded explicitly rather than treated as success.

## Redaction Contract

The following values must not appear in API responses, workflow payloads, logs, artifacts, profile snapshots, or operator-visible failure messages:

- raw credential file contents
- token values
- auth file payloads
- private key blocks
- raw auth-volume listings
- environment dumps
- secret-like nested provider metadata

Allowed metadata includes compact refs and non-secret policy fields such as profile id, runtime id, provider id, credential source, materialization mode, volume ref, volume mount path, enabled/default state, priority, and slot policy.

## Test Contract

- Unit tests must cover profile shape validation, blank/unsafe ref rejection, slot policy preservation, profile response redaction, OAuth finalization registration/update behavior, and workflow/profile snapshot redaction.
- Integration tests must cover the OAuth verification to Provider Profile registration boundary through the real application/workflow path when Docker-backed integration infrastructure is available.
- Final verification must preserve `MM-355` traceability and compare behavior against the original Jira preset brief.
