# Feature Specification: Finish Codex OAuth Terminal Flow

**Feature Branch**: `205-finish-codex-oauth-terminal`  
**Created**: 2026-04-18  
**Status**: Draft  
**Input**:

```text
# MM-402 MoonSpec Orchestration Input

## Source

- Jira issue: MM-402
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Finish Codex provider profile OAuth terminal flow
- Labels: None
- Trusted fetch tool: `jira.get_issue`
- Normalized detail source: `/api/jira/issues/MM-402`
- Canonical source: `recommendedImports.presetInstructions` from the normalized trusted Jira issue detail response.

## Canonical MoonSpec Feature Request

Jira issue: MM-402 from MM project
Summary: Finish Codex provider profile OAuth terminal flow
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-402 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-402: Finish Codex provider profile OAuth terminal flow

## Summary
Finish Codex provider-profile OAuth terminal flow in Settings using xterm.js and device-code auth

## Description
Complete the remaining work needed for a user to select a provider profile on the Settings page, click **Auth**, open an embedded xterm.js terminal, complete the **Codex device-code authorization** flow, verify the resulting auth material, and register/update the provider profile so it can be used by managed workloads.

This should build on the existing OAuth Terminal architecture and implementation already in the repo rather than introducing a parallel auth system.

## Background
The repo already includes:
- OAuth Terminal architecture centered on Mission Control -> OAuth Session API -> OAuth workflow -> auth runner container -> PTY/WebSocket bridge -> xterm.js -> mounted auth volume -> verification -> Provider Profile registration.
- An OAuth terminal frontend entrypoint using xterm.js.
- OAuth session API endpoints for create / attach / websocket / cancel / finalize.
- A Temporal OAuth session workflow and related activities.
- Provider profiles backed by `oauth_volume` + `oauth_home` semantics.

The remaining gaps are what prevent this from functioning end-to-end for Codex.

## Scope / Remaining Work

### 1. Settings page integration
- Add provider-profile selection UX on the Settings page for OAuth-capable runtimes.
- Add an **Auth** action that creates an OAuth session for the selected profile.
- Launch the embedded xterm.js OAuth terminal window/modal from the settings flow.
- Surface session states clearly: starting, bridge ready, awaiting user, verifying, succeeded, failed, cancelled, expired.
- Support reconnect / retry / cancel from the settings UI.
- Wire UI finalization behavior so the user can complete the flow without manual API calls.

### 2. Ensure interactive transport is actually activated
- Fix session creation / workflow startup so Codex OAuth sessions use the MoonMind terminal transport (`moonmind_pty_ws`) end-to-end.
- Verify the OAuth workflow launches the auth runner for interactive sessions instead of silently staying on `session_transport = none`.
- Confirm bridge-ready state, attach token issuance, websocket attach, and status polling all line up with the UI.

### 3. Make Codex bootstrap explicitly use device-code auth
- Update the Codex provider bootstrap command to use the device-code login path for the first iteration.
- Keep the provider registry shape compatible with future providers, but make the Codex path explicit and deterministic.
- Add any runtime-specific environment shaping required for Codex OAuth mode.

### 4. Replace placeholder auth runner assumptions with a real Codex-capable runner
- Use an auth-runner image that actually contains the Codex CLI and the expected runtime environment.
- Ensure the profile’s durable auth volume is mounted at the correct Codex auth-home path.
- Remove the ambiguous “bootstrap at container start and again via docker exec” behavior.
- Standardize on one PTY ownership model:
- either an idle runner container + PTY-owned exec of the login command, or
- a container whose primary PTY process is the login command.
- Keep the runner tightly scoped to OAuth enrollment only.

### 5. Strengthen verification beyond file existence
- Replace the current MVP-style verification fallback with a stronger Codex-specific success check.
- Verify that the resulting auth material is actually usable for Codex, not merely present on disk.
- Keep all verification output sanitized so secrets/tokens never enter logs, DB rows, or UI output.
- Optionally extract safe account metadata for `account_label` when available.

### 6. Complete Provider Profile registration / update semantics
- Ensure successful finalize creates or updates the target Provider Profile with:
- `credential_source = oauth_volume`
- `runtime_materialization_mode = oauth_home`
- correct `volume_ref`
- correct `volume_mount_path`
- enabled state and policy values preserved
- Ensure the profile manager sync path runs after successful registration.
- Preserve the current durable model where the **auth volume** is the source of truth, not a long-lived shared task runtime home.

### 7. Tighten workflow/session lifecycle and observability
- Ensure terminal connect/disconnect events are reflected consistently in workflow/session state.
- Confirm cancel / expire / fail paths always tear down the terminal bridge and auth runner container.
- Preserve enough metadata for audit/support without leaking secret material.
- Make reconnect behavior reliable for interrupted but still-active OAuth sessions.
- Verify stale-session cleanup does not block legitimate retries.

### 8. End-to-end validation and test coverage
- Add Docker-backed integration coverage for the real terminal + auth-runner path.
- Add an end-to-end verification path that proves a successful Codex device-code auth writes usable auth material into the mounted profile volume.
- Add UI coverage for Settings -> select profile -> Auth -> terminal attach -> success/failure handling.
- Add regression coverage for:
- missing/invalid transport state
- attach token misuse / reuse
- container startup failure
- verification failure
- cleanup on cancel / timeout

## Acceptance Criteria
1. From the Settings page, a user can select a Codex provider profile and click **Auth**.
2. Clicking **Auth** creates an OAuth session for that profile and opens an xterm.js terminal surface.
3. The terminal connects through MoonMind’s PTY/WebSocket bridge and shows the Codex **device-code** login flow.
4. The user can complete the flow interactively inside the embedded terminal experience.
5. Successful auth writes usable Codex auth material into the selected profile’s durable auth volume.
6. Verification uses a Codex-specific success check stronger than “expected files exist”.
7. Finalization creates or updates the Provider Profile with `oauth_volume` / `oauth_home` semantics and the correct volume references.
8. Cancelled, failed, and expired sessions clean up the auth runner container and expose clear terminal/session status to the UI.
9. Automated Docker-backed verification exists, or a documented repeatable verification path is added and exercised, proving the end-to-end Codex OAuth terminal flow works.

## Out of Scope
- Converting ordinary managed task execution into an interactive terminal product.
- Replacing provider-specific OAuth flows for non-Codex providers in this story.
- Redesigning the durable auth model away from provider-profile-backed volumes.
- Broad generic remote-shell access to workers or containers.

## Implementation Notes
- Keep the existing OAuth Terminal architecture as the canonical direction.
- Keep enrollment separate from managed task execution.
- Later managed Codex runs can continue to seed task-scoped `CODEX_HOME` material from the durable auth volume; that compatibility must not be broken by this story.
- Prefer finishing the current architecture over introducing another temporary auth path.

## Normalized Jira Detail

Acceptance criteria: Included in the recommended preset instructions above.

Recommended step instructions:

Complete Jira issue MM-402: Finish Codex provider profile OAuth terminal flow

Description
## Summary
Finish Codex provider-profile OAuth terminal flow in Settings using xterm.js and device-code auth

## Description
Complete the remaining work needed for a user to select a provider profile on the Settings page, click **Auth**, open an embedded xterm.js terminal, complete the **Codex device-code authorization** flow, verify the resulting auth material, and register/update the provider profile so it can be used by managed workloads.

This should build on the existing OAuth Terminal architecture and implementation already in the repo rather than introducing a parallel auth system.

## Background
The repo already includes:
- OAuth Terminal architecture centered on Mission Control -> OAuth Session API -> OAuth workflow -> auth runner container -> PTY/WebSocket bridge -> xterm.js -> mounted auth volume -> verification -> Provider Profile registration.
- An OAuth terminal frontend entrypoint using xterm.js.
- OAuth session API endpoints for create / attach / websocket / cancel / finalize.
- A Temporal OAuth session workflow and related activities.
- Provider profiles backed by `oauth_volume` + `oauth_home` semantics.

The remaining gaps are what prevent this from functioning end-to-end for Codex.

## Scope / Remaining Work

### 1. Settings page integration
- Add provider-profile selection UX on the Settings page for OAuth-capable runtimes.
- Add an **Auth** action that creates an OAuth session for the selected profile.
- Launch the embedded xterm.js OAuth terminal window/modal from the settings flow.
- Surface session states clearly: starting, bridge ready, awaiting user, verifying, succeeded, failed, cancelled, expired.
- Support reconnect / retry / cancel from the settings UI.
- Wire UI finalization behavior so the user can complete the flow without manual API calls.

### 2. Ensure interactive transport is actually activated
- Fix session creation / workflow startup so Codex OAuth sessions use the MoonMind terminal transport (`moonmind_pty_ws`) end-to-end.
- Verify the OAuth workflow launches the auth runner for interactive sessions instead of silently staying on `session_transport = none`.
- Confirm bridge-ready state, attach token issuance, websocket attach, and status polling all line up with the UI.

### 3. Make Codex bootstrap explicitly use device-code auth
- Update the Codex provider bootstrap command to use the device-code login path for the first iteration.
- Keep the provider registry shape compatible with future providers, but make the Codex path explicit and deterministic.
- Add any runtime-specific environment shaping required for Codex OAuth mode.

### 4. Replace placeholder auth runner assumptions with a real Codex-capable runner
- Use an auth-runner image that actually contains the Codex CLI and the expected runtime environment.
- Ensure the profile’s durable auth volume is mounted at the correct Codex auth-home path.
- Remove the ambiguous “bootstrap at container start and again via docker exec” behavior.
- Standardize on one PTY ownership model:
- either an idle runner container + PTY-owned exec of the login command, or
- a container whose primary PTY process is the login command.
- Keep the runner tightly scoped to OAuth enrollment only.

### 5. Strengthen verification beyond file existence
- Replace the current MVP-style verification fallback with a stronger Codex-specific success check.
- Verify that the resulting auth material is actually usable for Codex, not merely present on disk.
- Keep all verification output sanitized so secrets/tokens never enter logs, DB rows, or UI output.
- Optionally extract safe account metadata for `account_label` when available.

### 6. Complete Provider Profile registration / update semantics
- Ensure successful finalize creates or updates the target Provider Profile with:
- `credential_source = oauth_volume`
- `runtime_materialization_mode = oauth_home`
- correct `volume_ref`
- correct `volume_mount_path`
- enabled state and policy values preserved
- Ensure the profile manager sync path runs after successful registration.
- Preserve the current durable model where the **auth volume** is the source of truth, not a long-lived shared task runtime home.

### 7. Tighten workflow/session lifecycle and observability
- Ensure terminal connect/disconnect events are reflected consistently in workflow/session state.
- Confirm cancel / expire / fail paths always tear down the terminal bridge and auth runner container.
- Preserve enough metadata for audit/support without leaking secret material.
- Make reconnect behavior reliable for interrupted but still-active OAuth sessions.
- Verify stale-session cleanup does not block legitimate retries.

### 8. End-to-end validation and test coverage
- Add Docker-backed integration coverage for the real terminal + auth-runner path.
- Add an end-to-end verification path that proves a successful Codex device-code auth writes usable auth material into the mounted profile volume.
- Add UI coverage for Settings -> select profile -> Auth -> terminal attach -> success/failure handling.
- Add regression coverage for:
- missing/invalid transport state
- attach token misuse / reuse
- container startup failure
- verification failure
- cleanup on cancel / timeout

## Acceptance Criteria
1. From the Settings page, a user can select a Codex provider profile and click **Auth**.
2. Clicking **Auth** creates an OAuth session for that profile and opens an xterm.js terminal surface.
3. The terminal connects through MoonMind’s PTY/WebSocket bridge and shows the Codex **device-code** login flow.
4. The user can complete the flow interactively inside the embedded terminal experience.
5. Successful auth writes usable Codex auth material into the selected profile’s durable auth volume.
6. Verification uses a Codex-specific success check stronger than “expected files exist”.
7. Finalization creates or updates the Provider Profile with `oauth_volume` / `oauth_home` semantics and the correct volume references.
8. Cancelled, failed, and expired sessions clean up the auth runner container and expose clear terminal/session status to the UI.
9. Automated Docker-backed verification exists, or a documented repeatable verification path is added and exercised, proving the end-to-end Codex OAuth terminal flow works.

## Out of Scope
- Converting ordinary managed task execution into an interactive terminal product.
- Replacing provider-specific OAuth flows for non-Codex providers in this story.
- Redesigning the durable auth model away from provider-profile-backed volumes.
- Broad generic remote-shell access to workers or containers.

## Implementation Notes
- Keep the existing OAuth Terminal architecture as the canonical direction.
- Keep enrollment separate from managed task execution.
- Later managed Codex runs can continue to seed task-scoped `CODEX_HOME` material from the durable auth volume; that compatibility must not be broken by this story.
- Prefer finishing the current architecture over introducing another temporary auth path.
```

**Implementation Intent**: Runtime. The Jira preset brief is the canonical Moon Spec orchestration input, and any implementation-document references in the brief are treated as runtime source requirements.

## User Story - Codex OAuth Terminal Enrollment

**Summary**: As an authorized MoonMind operator, I want to authenticate a Codex provider profile from Settings through an embedded terminal so that managed Codex workloads can use verified OAuth credentials without manual API calls or a parallel auth path.

**Goal**: Operators can select a Codex provider profile, start OAuth authentication, complete the device-code flow in the first-party terminal, and finish with a usable OAuth-backed Provider Profile.

**Independent Test**: Start with a Codex OAuth-capable provider profile in Settings, run the Auth flow through the terminal session lifecycle, complete or simulate device-code authorization, finalize the session, and verify that the profile is updated with usable OAuth-volume credentials and sanitized lifecycle evidence.

**Acceptance Scenarios**:

1. **Given** an authorized operator views Settings with a Codex OAuth-capable provider profile, **When** they choose that profile and click Auth, **Then** MoonMind creates an OAuth session for that profile and opens an embedded terminal enrollment surface without requiring manual API calls.
2. **Given** the OAuth session is starting, **When** the runtime bridge becomes ready, **Then** the terminal connects through MoonMind's authenticated PTY/WebSocket transport and exposes clear session states from startup through awaiting user input.
3. **Given** the operator completes the Codex device-code login flow in the terminal, **When** verification runs, **Then** MoonMind performs a Codex-specific success check stronger than file existence without exposing credential contents.
4. **Given** verification succeeds, **When** the session is finalized, **Then** MoonMind creates or updates the selected Provider Profile with OAuth-volume and OAuth-home semantics while preserving existing policy values.
5. **Given** the session is cancelled, fails, expires, disconnects, or is retried, **When** MoonMind handles the lifecycle event, **Then** the terminal bridge and auth runner are cleaned up and the UI exposes an accurate recoverable or terminal state.
6. **Given** managed Codex workloads run after enrollment, **When** they use the selected provider profile, **Then** they continue to use the durable auth volume as the credential source without turning the OAuth terminal into a generic task terminal.

### Edge Cases

- The selected provider profile is missing OAuth-volume metadata or is not Codex OAuth-capable.
- OAuth session creation succeeds but the terminal bridge never reaches readiness.
- The browser disconnects during device-code authorization and later reconnects while the session is still active.
- The attach token is reused, expired, or presented by the wrong actor.
- The Codex login command exits without producing usable auth material.
- Verification detects credential-like output or failure details that must be redacted from logs, workflow state, UI responses, and artifacts.
- Finalization races with cancel, expiry, or cleanup.
- A previous failed or cancelled session left stale runner or bridge state.

## Assumptions

- MM-402 is the umbrella completion story for the remaining end-to-end Codex Settings OAuth flow; previously completed Moon Specs remain valid source context and should be reused rather than reimplemented.
- The first runtime target is Codex device-code authentication; non-Codex OAuth providers are out of scope unless needed to keep existing provider abstractions intact.
- Docker-backed verification is expected when the environment has Docker access; if unavailable, the repeatable verification path must document the exact blocker and the highest-confidence local evidence.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/ManagedAgents/OAuthTerminal.md` sections 1 and 5 require a first-party browser terminal path for OAuth credential enrollment that starts from Mission Control or Settings and flows through the OAuth Session API, workflow, auth runner, terminal bridge, verification, and Provider Profile registration. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007.
- **DESIGN-REQ-002**: Source section 5.1 requires the auth runner to be short-lived, scoped to one OAuth session, mounted at the provider enrollment path, and stopped after success, cancellation, expiry, or failure. Scope: in scope. Maps to FR-002, FR-004, FR-011.
- **DESIGN-REQ-003**: Source section 5.2 requires authenticated PTY/WebSocket terminal I/O with resize, heartbeat, ownership, TTL, close metadata, and no generic Docker exec or ordinary task terminal exposure. Scope: in scope. Maps to FR-003, FR-008, FR-011, FR-012.
- **DESIGN-REQ-004**: Source section 5.3 requires transport-neutral OAuth states including starting, bridge ready, awaiting user, verifying, registering profile, succeeded, failed, cancelled, and expired, with `moonmind_pty_ws` used when the bridge is enabled. Scope: in scope. Maps to FR-003, FR-009.
- **DESIGN-REQ-005**: Source sections 6 and 10 require successful Codex OAuth verification to register or update a Provider Profile using OAuth-volume and OAuth-home semantics with durable auth volume references and preserved slot policy. Scope: in scope. Maps to FR-006, FR-007, FR-010.
- **DESIGN-REQ-006**: Source section 8 requires credential verification at the OAuth/Profile boundary without copying credential contents into workflow payloads, artifacts, logs, or UI responses. Scope: in scope. Maps to FR-005, FR-013.
- **DESIGN-REQ-007**: Source sections 2, 7, 10, and 11 require the OAuth terminal to remain credential enrollment or repair only; managed Codex task execution must use the existing managed-session plane and not OAuth terminal scrollback or generic shell access. Scope: in scope. Maps to FR-010, FR-012.
- **DESIGN-REQ-008**: MM-402 acceptance criteria require Settings-page profile selection, Auth action, embedded terminal launch, reconnect/retry/cancel/finalization behavior, stronger Codex verification, profile registration, cleanup, and end-to-end validation. Scope: in scope. Maps to FR-001 through FR-014.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Settings MUST let an authorized operator identify and select a Codex OAuth-capable provider profile and start authentication with an Auth action.
- **FR-002**: Starting Auth MUST create an OAuth session scoped to the selected profile and launch the provider enrollment runtime without requiring manual API calls.
- **FR-003**: The embedded terminal MUST attach through MoonMind's authenticated terminal transport and support input, output, resize, heartbeat, reconnect, and close behavior for the owning OAuth session.
- **FR-004**: Codex OAuth sessions MUST use a deterministic device-code login path for the first supported Codex enrollment flow.
- **FR-005**: Verification MUST perform a Codex-specific usable-auth check stronger than expected-file presence before success is reported.
- **FR-006**: Successful finalization MUST create or update the selected Provider Profile with OAuth-volume and OAuth-home credential semantics, including durable volume reference and mount path.
- **FR-007**: Provider Profile finalization MUST preserve existing operator policy values unless the OAuth flow explicitly owns them.
- **FR-008**: The UI MUST expose clear session states covering starting, bridge ready, awaiting user, verifying, registering profile, succeeded, failed, cancelled, and expired.
- **FR-009**: Session creation, workflow startup, and status projection MUST consistently use the active terminal transport when interactive Codex enrollment is requested.
- **FR-010**: Later managed Codex workloads MUST continue to consume credentials through the provider-profile-backed durable auth volume model rather than through terminal state or a parallel auth store.
- **FR-011**: Cancelled, failed, expired, succeeded, and retried sessions MUST clean up auth runner and bridge resources deterministically.
- **FR-012**: The OAuth terminal MUST NOT expose generic Docker exec, ordinary task terminal attachment, or broad remote-shell access.
- **FR-013**: Workflow history, logs, UI responses, verification output, and artifacts MUST NOT expose raw credentials, token values, private keys, auth headers, or raw auth-volume listings.
- **FR-014**: Automated coverage or a documented repeatable verification path MUST prove the end-to-end Settings to Codex OAuth terminal flow, including terminal attach, success/failure handling, profile update, and cleanup behavior.
- **FR-015**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key MM-402 and the original preset brief as traceability evidence.

### Key Entities *(include if feature involves data)*

- **Provider Profile**: Operator-visible runtime credential profile selected from Settings and updated after successful OAuth enrollment.
- **OAuth Session**: Time-bounded credential enrollment workflow scoped to an actor, provider profile, terminal transport, status, and cleanup lifecycle.
- **Auth Runner**: Short-lived enrollment runtime that executes the provider login flow against the durable auth volume.
- **Terminal Attachment**: Authenticated browser connection to the OAuth session terminal with ownership, TTL, resize, heartbeat, reconnect, and close metadata.
- **Verification Result**: Sanitized outcome proving whether Codex auth material is usable and safe to register with the provider profile.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A Settings-driven test can start a Codex profile Auth flow and observe an OAuth session with terminal transport enabled.
- **SC-002**: Terminal boundary tests cover authenticated attach, input/output, resize, heartbeat, reconnect or retry, cancel, and failed attach-token behavior.
- **SC-003**: Verification tests distinguish usable Codex auth material from mere file presence and redact credential-like details.
- **SC-004**: Finalization tests prove the selected Provider Profile is created or updated with OAuth-volume and OAuth-home credential semantics and preserved policy values.
- **SC-005**: Cleanup tests prove success, failure, cancellation, expiry, and retry paths stop or release auth runner and bridge resources.
- **SC-006**: End-to-end verification is automated where environment support exists, or a repeatable verification path records exact blockers and evidence without claiming unproven success.
- **SC-007**: Source traceability checks confirm MM-402 and the canonical Jira preset brief remain present in Moon Spec artifacts and final verification output.
