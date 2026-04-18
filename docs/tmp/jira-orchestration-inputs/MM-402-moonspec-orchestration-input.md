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
