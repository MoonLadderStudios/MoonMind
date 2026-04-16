# Data Model: Codex Auth Volume Profile Contract

## ProviderProfile

- Purpose: Durable runtime profile metadata for a managed agent provider.
- Required story fields: profile id, runtime id, provider id, credential source, runtime materialization mode, volume ref, volume mount path, enabled/default flags, priority, and slot policy metadata when present.
- Validation:
  - Codex OAuth profiles require `runtime_id = codex_cli`.
  - Codex OAuth profiles require `credential_source = oauth_volume`.
  - Codex OAuth profiles require `runtime_materialization_mode = oauth_home`.
  - `volume_ref` and `volume_mount_path` must be present, non-blank, and safe to serialize.
  - Unsupported runtime/profile combinations fail fast.
- Relationships: May be created or updated from OAuth verification evidence and may be read by profile manager or workflow code as a secret-free snapshot.
- State transitions:
  - Missing or unsafe refs -> rejected before profile is selectable.
  - Verified OAuth evidence -> create or update profile metadata.
  - Existing profile repair -> preserve refs, materialization mode, provider identity, and slot policy without credential contents.

## OAuthVerificationEvidence

- Purpose: Secret-free evidence that an OAuth auth volume is ready for Provider Profile registration or repair.
- Fields: session id, profile id when known, runtime id, provider id, status, volume ref, volume mount path, verified timestamp, and sanitized failure reason when verification fails.
- Validation:
  - Evidence used for profile registration must not include credential file contents, token values, raw environment data, or raw auth-volume listings.
  - Failed verification must not create or update a selectable profile.
- Relationships: Produced by OAuth/session verification flow and consumed by profile registration/update logic.
- State transitions:
  - pending verification -> verified evidence.
  - pending verification -> sanitized failure.
  - verified evidence -> ProviderProfile create/update.

## ProfileSnapshot

- Purpose: Secret-free representation of Provider Profile metadata exposed to operators, workflows, or profile manager code.
- Fields: profile id, runtime id, provider id, provider label, credential source, runtime materialization mode, volume ref, volume mount path, enabled/default state, priority, model metadata, file/template metadata, env template refs, slot policy, and timestamps when already exposed by current profile surfaces.
- Validation:
  - Must exclude raw credential file contents, token values, auth file payloads, private key blocks, raw auth-volume listings, and environment dumps.
  - Nested provider metadata must be checked for secret-like or raw credential-bearing values before exposure.
- Relationships: Derived from ProviderProfile records and workflow/activity payloads.
- State transitions:
  - stored profile -> operator-facing snapshot.
  - stored profile -> workflow-facing snapshot.
  - unsafe nested value detected -> rejected or redacted with an actionable non-secret failure.

## SlotPolicy

- Purpose: Scheduling and concurrency metadata attached to a provider profile.
- Fields: maximum parallel run policy, cooldown policy, lease-duration policy, and any existing profile-manager priority/default metadata.
- Validation:
  - Values must remain metadata only and must not encode credential contents.
  - Missing optional policy values keep existing defaults rather than inventing credential-bearing state.
- Relationships: Stored with ProviderProfile and consumed by profile manager selection.
- State transitions:
  - profile create/update -> slot policy preserved.
  - profile serialization -> slot policy included only as safe metadata.

## Cross-Cutting Rules

- Preserve Jira issue key `MM-355` as traceability metadata in generated artifacts and verification evidence.
- Never model raw credential contents, token values, private keys, environment dumps, or raw auth-volume listings as persisted, workflow-visible, artifact-visible, log-visible, or browser-visible fields.
- Prefer compact refs and explicit status values over provider-shaped dictionaries at workflow and API boundaries.
- Keep managed-session launch, per-run Codex home seeding, interactive OAuth terminal UI, and Docker workload credential inheritance outside this story.
