# Tasks: Register OAuth-backed Codex Provider Profiles

## Tests First

- [X] T001 Add API regression coverage for Codex OAuth session default `volume_ref`, `volume_mount_path`, and provider metadata. (FR-001, FR-002)
- [X] T002 Add API regression coverage that failed volume verification fails finalization and persists the failure reason. (FR-003)
- [X] T003 Add API regression coverage that successful finalization registers an OAuth-backed Codex Provider Profile with `oauth_volume` and `oauth_home`. (FR-004, FR-005)
- [X] T004 Add activity-boundary regression coverage for `oauth_session.register_profile` Provider Profile shape. (FR-004, FR-005)
- [X] T005 Add verifier regression coverage for Codex credential files relative to `/home/app/.codex`. (FR-006)

## Implementation

- [X] T006 Add optional OAuth request fields for `volume_mount_path`, `provider_id`, and `provider_label`.
- [X] T007 Apply Codex OAuth defaults during OAuth session creation.
- [X] T008 Require successful durable auth-volume verification before API finalization can succeed.
- [X] T009 Register API-finalized Provider Profiles with Codex provider metadata and `RuntimeMaterializationMode.OAUTH_HOME`.
- [X] T010 Register activity-finalized Provider Profiles with Codex provider metadata and `RuntimeMaterializationMode.OAUTH_HOME`.
- [X] T011 Update Codex volume verifier credential paths for root-level Codex home files.

## Verification

- [X] T012 Run targeted unit tests.
- [X] T013 Run full unit test suite with `./tools/test_unit.sh`.
- [X] T014 Run final Moon Spec verification.

