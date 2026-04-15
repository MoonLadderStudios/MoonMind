# Story Breakdown: OAuth Terminal and Managed Session Auth Volumes

**Source design:** `docs/ManagedAgents/OAuthTerminal.md`
**Original source reference path:** `docs/ManagedAgents/OAuthTerminal.md`
**Story extraction date:** 2026-04-15T07:13:42Z
**Coverage gate:** PASS - every major design point is owned by at least one story.

## Design Summary

OAuth Terminal and Managed Session Auth Volumes defines the desired-state contract for enrolling OAuth credentials into durable provider-profile auth volumes, verifying those credentials, and safely targeting them into task-scoped managed Codex sessions. The design separates short-lived interactive enrollment from ordinary managed task execution, keeps per-run CODEX_HOME under the shared task workspace, requires explicit auth-volume mounts and one-way credential seeding, and treats logs, artifacts, summaries, diagnostics, and provider-profile metadata as the operator-visible truth. It is Codex-focused today while preserving boundaries for future Claude and Gemini parity, workload containers, security, verification, and transport-neutral OAuth session state.

## Coverage Points

- **DESIGN-REQ-001 - Provide first-party OAuth enrollment and auth-volume targeting** (requirement, 1. Purpose; 2. Scope): MoonMind needs a first-party way to enroll OAuth credentials for managed CLI runtimes and target the resulting credential volume into managed runtime containers.
- **DESIGN-REQ-002 - Keep Codex as the current managed-session target** (constraint, 1. Purpose; 2. Scope): The fully updated task-scoped managed-session plane is Codex-only today; Claude Code and Gemini CLI may have auth volumes and provider profiles but are not yet parity targets.
- **DESIGN-REQ-003 - Persist reusable credentials in provider-profile auth volumes** (state-model, 3.1 Durable auth volume): Codex OAuth credentials live in a durable Docker auth volume such as codex_auth_volume with profile fields including volume_ref, volume_mount_path, credential_source, and runtime_materialization_mode.
- **DESIGN-REQ-004 - Keep auth volumes separate from task workspaces and artifacts** (constraint, 1. Purpose; 3.1 Durable auth volume; 10. Operator Behavior): The auth volume is a credential backing store, not a task workspace, presentation artifact, execution record, or audit artifact.
- **DESIGN-REQ-005 - Place per-run Codex home under the shared task workspace** (state-model, 3.2 Shared task workspace volume): Managed Codex sessions receive agent_workspaces at /work/agent_jobs, and the per-run codexHomePath lives under the workspace as the CODEX_HOME for Codex App Server.
- **DESIGN-REQ-006 - Mount auth volumes only at explicit managed-session targets** (security, 3.3 Explicit auth-volume target; 4. Volume Targeting Rules): A managed-session container receives the durable auth volume only through MANAGED_AUTH_VOLUME_PATH when policy requires it, and that target must be absolute and distinct from codexHomePath.
- **DESIGN-REQ-007 - Seed credentials one way into per-run runtime homes** (security, 3.3 Explicit auth-volume target; 4. Volume Targeting Rules): Eligible auth entries may be copied from MANAGED_AUTH_VOLUME_PATH into the per-run Codex home at startup, but session-local runtime state is not provider-profile source of truth.
- **DESIGN-REQ-008 - Prevent workload containers from inheriting managed auth by default** (security, 4. Volume Targeting Rules; 11. Required Boundaries): Docker-backed workload containers launched from managed sessions receive only declared workspace/cache mounts unless a credential mount is explicitly declared and justified.
- **DESIGN-REQ-009 - Keep raw credentials out of workflow history and operator artifacts** (security, 4. Volume Targeting Rules; 8. Verification; 9. Security Model): Workflow payloads may carry compact refs, but credential file contents, token values, environment dumps, and raw auth-volume listings must not appear in history, logs, artifacts, or UI responses.
- **DESIGN-REQ-010 - Use MoonMind-owned OAuth terminal infrastructure** (integration, 5. OAuth Terminal Contract): Interactive OAuth should flow through Mission Control, an OAuth Session API, a MoonMind.OAuthSession workflow, a short-lived auth runner, a MoonMind PTY/WebSocket bridge, xterm.js, verification, and Provider Profile registration.
- **DESIGN-REQ-011 - Limit auth runner containers to short-lived enrollment** (state-model, 5.1 Auth runner container): The auth runner mounts the target auth volume at the provider enrollment path, runs the provider bootstrap command in a PTY, exposes I/O only through MoonMind, stops on terminal outcomes, and leaves credentials in the durable auth volume.
- **DESIGN-REQ-012 - Provide an authenticated PTY WebSocket bridge without generic Docker exec** (security, 5.2 Terminal bridge): The terminal bridge allocates or attaches to the runner PTY, proxies authenticated browser I/O, handles resize and heartbeat, enforces ownership and TTL, records connection metadata, and does not expose generic Docker exec or task-run terminal attachment.
- **DESIGN-REQ-013 - Model OAuth sessions with transport-neutral state** (state-model, 5.3 Session transport state): OAuth sessions progress through explicit states from pending to succeeded, failed, cancelled, or expired, and provider semantics should not depend on the old tmate URL model.
- **DESIGN-REQ-014 - Register OAuth success as Provider Profile state** (state-model, 6. Provider Profile Registration): After verification, MoonMind registers or updates a Provider Profile preserving runtime/provider identifiers, oauth_volume source, oauth_home materialization, volume refs, enrollment mount path, and slot policy.
- **DESIGN-REQ-015 - Launch managed Codex sessions with defined mounts and reserved env** (integration, 7. Managed Codex Session Launch): The launcher builds containers with required workspace mounts, conditional auth volume mounts, optional workload/cache volumes, and reserved MOONMIND_SESSION_* environment values.
- **DESIGN-REQ-016 - Validate session startup before marking sessions ready** (state-model, 7. Managed Codex Session Launch; 8. Verification): Session runtime validates workspace paths, auth path separation, per-run home creation, credential seeding, Codex App Server startup, and selected provider profile materialization before readiness.
- **DESIGN-REQ-017 - Verify credentials at OAuth/profile and launch boundaries** (security, 8. Verification): Credential verification runs before profile registration/update and before managed-session readiness, using file presence or CLI fingerprint/status signals without copying credential contents.
- **DESIGN-REQ-018 - Restrict OAuth management to authorized provider-profile operators** (security, 9. Security Model): Only authenticated users with provider-profile management permission can create, attach to, cancel, finalize, select, or mutate OAuth sessions and auth volumes.
- **DESIGN-REQ-019 - Expose only safe OAuth status and terminal information to browsers** (security, 9. Security Model): Browsers may see session status, terminal I/O from the provider CLI, timestamps, failure reason, and registered profile summary, but not credential files, token values, environment dumps, or raw volume listings.
- **DESIGN-REQ-020 - Make logs, artifacts, summaries, diagnostics, and metadata the execution record** (observability, 1. Purpose; 10. Operator Behavior): Operators inspect Live Logs, artifacts, session summaries, diagnostics, and reset/control-boundary artifacts for ordinary task execution, not terminal scrollback, runtime homes, or auth volumes.
- **DESIGN-REQ-021 - Preserve ownership boundaries across auth, profiles, sessions, runtime, and workloads** (constraint, 11. Required Boundaries): OAuth terminal code owns enrollment; Provider Profile code owns credential refs and profile metadata; managed-session controller owns mounts; Codex runtime owns seeding and App Server startup; workload orchestration owns non-agent containers.
- **DESIGN-REQ-022 - Respect explicit non-goals for terminals and auth inheritance** (non-goal, 2. Scope; 5. OAuth Terminal Contract): The design does not define PTY attach for ordinary task runs, Live Logs transport, a generic remote shell product, Claude/Gemini managed-session parity, or Docker workload auth inheritance.

## Ordered Story Candidates

### STORY-001: Register OAuth-backed Codex provider profiles

- **Short name:** `codex-oauth-profile`
- **Source reference:** `docs/ManagedAgents/OAuthTerminal.md` (1. Purpose; 3.1 Durable auth volume; 4. Volume Targeting Rules; 6. Provider Profile Registration; 8. Verification)
- **Why:** Provider Profile state is the durable contract every later terminal and managed-session story depends on.
- **Description:** As an operator, I can enroll or repair Codex OAuth credentials into a durable auth volume and have MoonMind verify and register the resulting OAuth-backed Provider Profile without treating the auth volume as task state or an artifact.
- **Independent test:** Run a provider-profile enrollment workflow against a fixture Codex auth volume, verify success and failure cases, then assert the stored profile contains only refs and metadata while no workflow payload, artifact, log, or API response contains credential contents.
- **Dependencies:** None
- **Needs clarification:** None
- **Scope:**
  - Codex OAuth provider profile shape and validation
  - Durable auth volume creation/reuse by volume_ref
  - oauth_volume credential_source and oauth_home runtime_materialization_mode
  - Profile slot policy fields such as max_parallel_runs, cooldown, and lease duration
  - Credential verification before profile registration or update
- **Out of scope:**
  - Interactive browser terminal transport
  - Managed Codex session container launch
  - Claude/Gemini task-scoped managed-session parity
  - Raw credential display or artifact export
- **Acceptance criteria:**
  - Codex OAuth profiles preserve runtime_id, provider_id, credential_source, runtime_materialization_mode, volume_ref, volume_mount_path, and slot policy fields.
  - OAuth/profile verification completes before a profile is registered or updated.
  - The auth volume is modeled as a credential backing store and is never listed as a task workspace or audit artifact.
  - Claude and Gemini auth profiles may be represented only as auth-volume/profile records unless their managed-session parity is explicitly implemented later.
  - Workflow history, logs, artifacts, and UI responses carry compact credential refs only and never credential file contents.
- **Owned coverage:**
  - **DESIGN-REQ-001:** Establishes the provider-profile half of first-party OAuth enrollment.
  - **DESIGN-REQ-002:** Scopes the concrete managed-session target to Codex while allowing non-parity profile records for other runtimes.
  - **DESIGN-REQ-003:** Owns durable auth volume and profile field persistence.
  - **DESIGN-REQ-004:** Owns the credential-store, not workspace/artifact, interpretation for profiles.
  - **DESIGN-REQ-009:** Owns secret-free provider-profile payload and artifact behavior.
  - **DESIGN-REQ-014:** Owns Provider Profile registration after OAuth success.
  - **DESIGN-REQ-017:** Owns verification at the OAuth/profile boundary.
- **Risks or open questions:**
  - Provider-specific CLI verification signals may vary and should remain adapter-bound.

### STORY-002: Run authenticated OAuth terminal sessions

- **Short name:** `oauth-terminal-session`
- **Source reference:** `docs/ManagedAgents/OAuthTerminal.md` (5. OAuth Terminal Contract; 5.1 Auth runner container; 5.2 Terminal bridge; 5.3 Session transport state; 9. Security Model)
- **Why:** Interactive OAuth is the user-facing enrollment path and must be constrained before managed runtime execution can rely on reusable credentials.
- **Description:** As an authorized operator, I can start a browser-based OAuth terminal session that runs a provider login command inside a short-lived auth runner, streams PTY I/O through MoonMind, verifies the resulting credentials, and closes deterministically on success, cancellation, expiry, or failure.
- **Independent test:** Create OAuth sessions for authorized and unauthorized users with a fake provider login command, drive PTY input/output over the WebSocket bridge, simulate resize, heartbeat loss, cancellation, expiry, and successful verification, and assert state transitions plus connection metadata without exposing Docker exec or task-run terminals.
- **Dependencies:** STORY-001
- **Needs clarification:** None
- **Scope:**
  - OAuth Session API and MoonMind.OAuthSession workflow state
  - Short-lived auth runner container lifecycle
  - PTY allocation and authenticated WebSocket bridge for browser terminal I/O
  - Resize, heartbeat, TTL, ownership, disconnect, close, cancellation, expiry, and failure metadata
  - Transport-neutral session states and MoonMind-owned transport identifiers
- **Out of scope:**
  - Generic Docker exec terminal product
  - PTY attach for ordinary managed task runs
  - Live Logs transport for managed runs
  - Codex App Server task execution
- **Acceptance criteria:**
  - Only users with provider-profile management permission can create, attach to, cancel, or finalize OAuth terminal sessions.
  - The auth runner mounts the target auth volume at the provider enrollment path and stops after success, cancellation, expiry, or failure.
  - Browser terminal I/O is proxied only through an authenticated MoonMind PTY WebSocket bridge with ownership and TTL enforcement.
  - Resize, heartbeat, disconnect, reconnect, close reason, and terminal outcome metadata are persisted for diagnostics.
  - OAuth session state uses pending, starting, bridge_ready, awaiting_user, verifying, registering_profile, succeeded, failed, cancelled, and expired states.
  - The bridge does not expose generic Docker exec access or terminal attachment for ordinary managed task runs.
  - Provider semantics do not depend on tmate URLs; enabled transport uses a MoonMind-owned identifier such as moonmind_pty_ws.
- **Owned coverage:**
  - **DESIGN-REQ-001:** Owns the first-party interactive enrollment surface.
  - **DESIGN-REQ-010:** Owns the Mission Control to workflow to auth runner to PTY bridge architecture.
  - **DESIGN-REQ-011:** Owns auth runner container lifecycle and enrollment-path mounting.
  - **DESIGN-REQ-012:** Owns the authenticated PTY WebSocket bridge and no-generic-exec guardrail.
  - **DESIGN-REQ-013:** Owns OAuth session state and transport naming.
  - **DESIGN-REQ-018:** Owns authorization for OAuth session operations.
  - **DESIGN-REQ-019:** Owns browser-visible terminal and status boundaries.
  - **DESIGN-REQ-022:** Owns non-goals for generic shell and ordinary task-run PTY attach.
- **Risks or open questions:**
  - Browser terminal output is provider-generated and must still be treated as sensitive operational output in retention and redaction paths.

### STORY-003: Launch managed Codex sessions with explicit auth materialization

- **Short name:** `codex-auth-materialization`
- **Source reference:** `docs/ManagedAgents/OAuthTerminal.md` (3.2 Shared task workspace volume; 3.3 Explicit auth-volume target; 4. Volume Targeting Rules; 7. Managed Codex Session Launch; 8. Verification)
- **Why:** The managed-session launch path is where durable OAuth credentials become usable by a task-scoped Codex runtime without turning the auth volume into the live runtime home.
- **Description:** As a task operator, I can launch a managed Codex session using a selected OAuth-backed Provider Profile, with the durable auth volume mounted only at an explicit auth target and eligible credentials copied one way into the per-run CODEX_HOME under the task workspace before Codex App Server starts.
- **Independent test:** Launch fixture managed Codex session containers with and without an OAuth-backed profile, assert mount targets and reserved env values, validate rejection when MANAGED_AUTH_VOLUME_PATH equals codexHomePath or is relative, and verify only eligible auth entries are seeded into the workspace CODEX_HOME before readiness.
- **Dependencies:** STORY-001
- **Needs clarification:** None
- **Scope:**
  - Managed Codex launcher mount plan for agent_workspaces and conditional auth volume
  - Reserved MOONMIND_SESSION_* environment values
  - Validation of workspace-root paths and auth target separation
  - Per-run codexHomePath creation under the shared workspace
  - One-way eligible auth entry seeding from MANAGED_AUTH_VOLUME_PATH
  - Codex App Server startup with CODEX_HOME set to the per-run home
- **Out of scope:**
  - Interactive OAuth terminal enrollment
  - Provider Profile UI
  - Workload container credential inheritance
  - Making the durable auth volume the live CODEX_HOME
- **Acceptance criteria:**
  - Every managed Codex session receives the shared task workspace volume at /work/agent_jobs.
  - The per-run CODEX_HOME is created under the task workspace and is writable by the managed-session container user.
  - The durable auth volume is mounted only when the selected provider profile and launcher policy require it.
  - MANAGED_AUTH_VOLUME_PATH must be absolute and must not equal the per-run codexHomePath.
  - Eligible auth entries are copied one way from MANAGED_AUTH_VOLUME_PATH into the per-run Codex home at startup.
  - Codex App Server starts with CODEX_HOME set to the per-run codexHomePath, not the durable auth volume.
  - The session is not marked ready until workspace paths, auth path separation, profile materialization, credential seeding, and App Server startup validation pass.
- **Owned coverage:**
  - **DESIGN-REQ-005:** Owns shared workspace and per-run CODEX_HOME placement.
  - **DESIGN-REQ-006:** Owns explicit auth-volume target validation and mounting.
  - **DESIGN-REQ-007:** Owns one-way credential seeding into per-run homes.
  - **DESIGN-REQ-015:** Owns launcher mount classes and reserved environment values.
  - **DESIGN-REQ-016:** Owns session startup validation and readiness.
  - **DESIGN-REQ-017:** Owns verification at managed-session launch boundary.
- **Risks or open questions:**
  - File ownership and permissions for copied auth entries must match the runtime container user across Docker volume implementations.

### STORY-004: Enforce auth security boundaries for workloads and browser surfaces

- **Short name:** `auth-security-boundaries`
- **Source reference:** `docs/ManagedAgents/OAuthTerminal.md` (4. Volume Targeting Rules; 8. Verification; 9. Security Model; 11. Required Boundaries)
- **Why:** Credential safety spans multiple surfaces, so this story gives the design one focused acceptance gate for leakage and ownership boundaries.
- **Description:** As a security reviewer, I can verify that OAuth credentials never leak into workflow history, browser responses, logs, artifacts, raw volume listings, or Docker-backed workload containers unless a workload credential mount is explicitly declared and justified.
- **Independent test:** Execute boundary tests that inject secret-like fixture credential files and environment values, exercise OAuth status APIs, profile APIs, managed-session launch metadata, artifact/log publishing, and workload container launch, then assert secret values and raw volume listings never appear and undeclared workload auth mounts are rejected.
- **Dependencies:** STORY-001, STORY-002, STORY-003
- **Needs clarification:** None
- **Scope:**
  - Secret-free workflow payload, artifact, log, and UI response contracts
  - Browser-visible OAuth status and terminal output restrictions
  - Provider-profile management authorization checks
  - Workload container mount policy that denies implicit auth inheritance
  - Boundary tests for OAuth terminal, Provider Profile, managed-session controller, Codex runtime, and workload orchestration responsibilities
- **Out of scope:**
  - Implementing the OAuth terminal UI itself
  - Implementing Codex App Server protocol behavior
  - Declaring new credential-requiring workload profiles
- **Acceptance criteria:**
  - Workflow payloads carry profile_id, volume_ref, and mount target refs only, never credential file contents.
  - Logs, diagnostics, artifacts, and browser responses redact or omit token values, credential files, environment dumps, and raw auth-volume listings.
  - OAuth management actions require provider-profile management permission at every control surface.
  - Workload containers launched from a managed session do not inherit auth volumes by default.
  - Any workload credential mount requires an explicit workload profile declaration and justification.
  - Tests cover the real adapter or service boundary where OAuth terminal, Provider Profile, managed-session controller, Codex runtime, and workload orchestration exchange metadata.
- **Owned coverage:**
  - **DESIGN-REQ-008:** Owns no implicit workload auth inheritance.
  - **DESIGN-REQ-009:** Owns credential leakage prevention across history, logs, artifacts, and UI.
  - **DESIGN-REQ-017:** Owns no-secret verification evidence at both boundaries.
  - **DESIGN-REQ-018:** Owns authorization enforcement for OAuth/profile management.
  - **DESIGN-REQ-019:** Owns browser response safety.
  - **DESIGN-REQ-021:** Owns cross-component boundary enforcement tests.
  - **DESIGN-REQ-022:** Owns non-goal enforcement around auth inheritance and generic shell exposure.
- **Risks or open questions:**
  - Redaction tests must avoid committing real secrets and should use deterministic fake secret patterns.

### STORY-005: Project operator-visible managed auth diagnostics

- **Short name:** `auth-operator-diagnostics`
- **Source reference:** `docs/ManagedAgents/OAuthTerminal.md` (1. Purpose; 8. Verification; 10. Operator Behavior; 11. Required Boundaries)
- **Why:** Operators need reliable troubleshooting signals while the design intentionally hides raw credential stores and separates auth enrollment from task execution.
- **Description:** As an operator, I can understand OAuth enrollment, Provider Profile registration, managed Codex auth materialization, and ordinary task execution through safe statuses, summaries, diagnostics, logs, artifacts, and session metadata without inspecting auth volumes, runtime homes, or terminal scrollback as execution records.
- **Independent test:** Simulate successful and failed enrollment plus successful and failed managed Codex session launch, then assert Mission Control/API projections show safe statuses, profile summaries, validation failures, diagnostics refs, and artifact/log pointers while omitting raw credentials, auth-volume listings, runtime-home contents, and terminal scrollback from ordinary task records.
- **Dependencies:** STORY-001, STORY-003
- **Needs clarification:** None
- **Scope:**
  - Safe OAuth enrollment and Provider Profile status summaries
  - Managed-session auth materialization diagnostics and failure reasons
  - Session metadata for profile refs, volume refs, mount targets, readiness, and validation outcomes
  - Operator guidance that ordinary task execution records are Live Logs, artifacts, summaries, diagnostics, and control-boundary artifacts
  - Clear distinction between enrollment terminal output and managed task execution observability
- **Out of scope:**
  - Displaying credential files or raw volume listings
  - Making runtime home directories browseable artifacts
  - Using OAuth terminal scrollback as the durable task execution record
  - Building Live Logs transport
- **Acceptance criteria:**
  - OAuth enrollment surfaces show session status, timestamps, failure reason, and registered profile summary where applicable.
  - Managed Codex session metadata records selected profile refs, volume refs, auth mount target, workspace Codex home path, readiness, and validation failure reasons without credential contents.
  - Ordinary task execution views direct operators to Live Logs, artifacts, summaries, diagnostics, and reset/control-boundary artifacts.
  - Runtime home directories and auth volumes are not exposed as presentation artifacts.
  - Enrollment terminal scrollback is not treated as the durable execution record for managed task runs.
  - Diagnostic events make it clear which component owns enrollment, profile metadata, session mounts, runtime seeding, or workload container behavior.
- **Owned coverage:**
  - **DESIGN-REQ-004:** Owns operator-facing distinction between credentials, workspaces, and artifacts.
  - **DESIGN-REQ-016:** Owns projection of startup validation and readiness diagnostics.
  - **DESIGN-REQ-020:** Owns durable execution record expectations.
  - **DESIGN-REQ-021:** Owns diagnostics that preserve component responsibility boundaries.
  - **DESIGN-REQ-022:** Owns non-goals around Live Logs transport and task-run PTY attach in operator docs/projections.
- **Risks or open questions:**
  - Operators may still need enough failure detail to repair local volume permissions without exposing sensitive file names or values.

## Coverage Matrix

- **DESIGN-REQ-001 - Provide first-party OAuth enrollment and auth-volume targeting:** STORY-001, STORY-002
- **DESIGN-REQ-002 - Keep Codex as the current managed-session target:** STORY-001
- **DESIGN-REQ-003 - Persist reusable credentials in provider-profile auth volumes:** STORY-001
- **DESIGN-REQ-004 - Keep auth volumes separate from task workspaces and artifacts:** STORY-001, STORY-005
- **DESIGN-REQ-005 - Place per-run Codex home under the shared task workspace:** STORY-003
- **DESIGN-REQ-006 - Mount auth volumes only at explicit managed-session targets:** STORY-003
- **DESIGN-REQ-007 - Seed credentials one way into per-run runtime homes:** STORY-003
- **DESIGN-REQ-008 - Prevent workload containers from inheriting managed auth by default:** STORY-004
- **DESIGN-REQ-009 - Keep raw credentials out of workflow history and operator artifacts:** STORY-001, STORY-004
- **DESIGN-REQ-010 - Use MoonMind-owned OAuth terminal infrastructure:** STORY-002
- **DESIGN-REQ-011 - Limit auth runner containers to short-lived enrollment:** STORY-002
- **DESIGN-REQ-012 - Provide an authenticated PTY WebSocket bridge without generic Docker exec:** STORY-002
- **DESIGN-REQ-013 - Model OAuth sessions with transport-neutral state:** STORY-002
- **DESIGN-REQ-014 - Register OAuth success as Provider Profile state:** STORY-001
- **DESIGN-REQ-015 - Launch managed Codex sessions with defined mounts and reserved env:** STORY-003
- **DESIGN-REQ-016 - Validate session startup before marking sessions ready:** STORY-003, STORY-005
- **DESIGN-REQ-017 - Verify credentials at OAuth/profile and launch boundaries:** STORY-001, STORY-003, STORY-004
- **DESIGN-REQ-018 - Restrict OAuth management to authorized provider-profile operators:** STORY-002, STORY-004
- **DESIGN-REQ-019 - Expose only safe OAuth status and terminal information to browsers:** STORY-002, STORY-004
- **DESIGN-REQ-020 - Make logs, artifacts, summaries, diagnostics, and metadata the execution record:** STORY-005
- **DESIGN-REQ-021 - Preserve ownership boundaries across auth, profiles, sessions, runtime, and workloads:** STORY-004, STORY-005
- **DESIGN-REQ-022 - Respect explicit non-goals for terminals and auth inheritance:** STORY-002, STORY-004, STORY-005

## Dependencies

- **STORY-001:** None
- **STORY-002:** STORY-001
- **STORY-003:** STORY-001
- **STORY-004:** STORY-001, STORY-002, STORY-003
- **STORY-005:** STORY-001, STORY-003

## Out-of-Scope Items And Rationale

- **No `spec.md` generation or `specs/` directory creation:** Breakdown only produces story candidates under docs/tmp; specify owns future spec creation.
- **No PTY attach for ordinary managed task runs:** The OAuth terminal is only for credential enrollment or repair.
- **No generic remote shell product or Docker exec bridge:** The terminal bridge is scoped to authenticated OAuth runner PTYs.
- **No Live Logs transport implementation:** Ordinary task execution observability uses existing Live Logs/artifact/diagnostic surfaces; this design only references them.
- **No Claude/Gemini task-scoped managed-session parity:** Codex is the current concrete managed-session target for this document.
- **No default Docker workload auth inheritance:** Credential mounts for workloads require explicit declaration and justification.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
