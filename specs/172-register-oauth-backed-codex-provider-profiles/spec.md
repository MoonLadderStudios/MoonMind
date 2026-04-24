# Feature Specification: Register OAuth-backed Codex Provider Profiles

**Feature Branch**: `172-register-oauth-backed-codex-provider-profiles`  
**Created**: 2026-04-15  
**Status**: Implemented  
**Input**: MM-332: Register OAuth-backed Codex provider profiles. As an operator, I can enroll or repair Codex OAuth credentials into a durable auth volume and have MoonMind verify and register the resulting OAuth-backed Provider Profile without treating the auth volume as task state or an artifact. Source: `docs/ManagedAgents/OAuthTerminal.md` sections 1, 3.1, 4, 6, and 8. Coverage IDs: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-014, DESIGN-REQ-017. Breakdown Story ID: STORY-001. Breakdown JSON: `artifacts/story-breakdowns/mm-318-breakdown-docs-managedagents-oauthtermina-74125184/stories.json`.

## User Story

As an operator, I can enroll or repair Codex OAuth credentials into a durable auth volume and have MoonMind verify and register the resulting OAuth-backed Provider Profile without treating the auth volume as task state or an artifact.

## Acceptance Scenarios

1. **Given** an operator creates a Codex OAuth session without explicit volume details, **when** MoonMind persists and starts the session, **then** the session targets `codex_auth_volume` at `/home/app/.codex` and records compact provider metadata for `codex_cli` plus `openai`.
2. **Given** a Codex OAuth session is finalized after credentials are written to the durable auth volume, **when** verification succeeds, **then** MoonMind registers or updates an enabled Provider Profile with `runtime_id=codex_cli`, `provider_id=openai`, `credential_source=oauth_volume`, `runtime_materialization_mode=oauth_home`, `volume_ref=codex_auth_volume`, and `volume_mount_path=/home/app/.codex`.
3. **Given** a Codex OAuth session is finalized but durable volume verification fails or cannot run, **when** MoonMind handles finalization, **then** the session is marked failed and no successful Provider Profile registration is claimed.
4. **Given** Codex credentials live in the durable auth volume mounted at `/home/app/.codex`, **when** MoonMind verifies the volume, **then** it checks credential files relative to that mounted Codex home and does not copy credential contents into workflow payloads, logs, artifacts, or UI responses.

## Functional Requirements

- **FR-001**: OAuth session creation MUST default Codex sessions to the durable auth volume `codex_auth_volume` and enrollment/verification path `/home/app/.codex` when the caller does not provide explicit values. Maps to DESIGN-REQ-001, DESIGN-REQ-003.
- **FR-002**: OAuth session creation MUST preserve compact provider metadata for Codex OAuth-backed profiles, defaulting to `provider_id=openai` and `provider_label=OpenAI`. Maps to DESIGN-REQ-002, DESIGN-REQ-004.
- **FR-003**: OAuth finalization MUST require successful durable auth-volume verification before reporting success or registering the Provider Profile. Maps to DESIGN-REQ-014, DESIGN-REQ-017.
- **FR-004**: Registered Codex OAuth Provider Profiles MUST use `credential_source=oauth_volume` and `runtime_materialization_mode=oauth_home`. Maps to DESIGN-REQ-004.
- **FR-005**: Registered Codex OAuth Provider Profiles MUST store only refs and metadata, including `volume_ref` and `volume_mount_path`, never credential file contents. Maps to DESIGN-REQ-009, DESIGN-REQ-017.
- **FR-006**: Codex auth-volume verification MUST evaluate expected credential file presence relative to the mounted Codex home, including root-level Codex auth files. Maps to DESIGN-REQ-003, DESIGN-REQ-014.
- **FR-007**: The OAuth/Profile boundary MUST remain separate from task state, artifacts, and managed-session runtime homes. Maps to DESIGN-REQ-009, DESIGN-REQ-017.

## Non-Goals

- Implementing browser PTY terminal UX.
- Changing managed-session launch volume targeting.
- Treating auth volumes, runtime homes, or terminal scrollback as artifacts.

## Success Criteria

- Unit tests prove Codex OAuth session defaults, verification failure handling, successful profile registration shape, activity registration shape, and Codex volume path verification.
- No workflow payload, response, or profile row contains raw credential contents.

