# Implementation Tasks: Provider Profiles Phase 5

## Dependencies & Completion Order

- **Phase 1: Setup**: Schema updates.
- **Phase 2: TerminalPTYBridge**: Infrastructure for dropping clients into terminal securely.
- **Phase 3: OAuthSessionWorkflow Updates [US1]**: Depends on Phase 1 & 2.
- **Phase 4: Provider Verification [US1]**: Checking the volume actually has a logged-in provider.

## Phase 1: Setup

- [x] T001 Define `OAuthSession` SQLAlchemy columns (`terminal_session_id`, `terminal_bridge_id`, `connected_at`, `disconnected_at`) and remove legacy URLs in `src/moonmind/models/oauth.py` (DOC-REQ-003)
- [x] T002 Generate Alembic migration for the column changes in `src/moonmind/migrations/versions/` (DOC-REQ-003 validation)

## Phase 2: TerminalPTYBridge (Foundational)

- [x] T003 Implement `TerminalPTYBridge` container startup logic in `src/moonmind/agent_runtime/terminal_bridge.py` replacing the dead `oauth_launch` (DOC-REQ-001)
- [ ] T004 Implement connection authorization logic enforcing session attachment via JWT or similar in `src/moonmind/api/websockets.py` (DOC-REQ-005)

## Phase 3: OAuthSessionWorkflow Updates [US1]

- [x] T005 [US1] Replace Temporal state transitions removing `oauth_runner_ready` in favor of `bridge_ready`, listening to connect/disconnect signals in `src/moonmind/workflows/oauth_session.py` (DOC-REQ-002)
- [x] T006 [P] [US1] Update finalization to issue `CreateManagedAgentProviderProfile` with `oauth_volume` source in `src/moonmind/workflows/oauth_session.py` (DOC-REQ-004)
- [ ] T007 [P] [US1] Write Temporal regression tests proving bridging and persistence triggers correctly in `tests/integration/workflows/test_oauth_session.py` (DOC-REQ-002 validation)

## Phase 4: Provider Verification [US1]

- [ ] T008 [US1] Implement `verify_cli_fingerprint` activity to assert standard tools like `claude`, `gcloud`, etc exist and are logged in correctly on the volume in `src/moonmind/activities/verification.py` (DOC-REQ-006)
