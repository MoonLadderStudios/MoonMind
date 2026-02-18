# Data Model: Live Task Handoff

## Entity: TaskRunLiveSession (new persisted queue task-run live state)

### Fields
- `id` (UUID): primary key.
- `task_run_id` (UUID FK -> `agent_jobs.id`, unique): owning task run.
- `provider` (enum): live provider (`tmate`).
- `status` (enum): `disabled`, `starting`, `ready`, `revoked`, `ended`, `error`.
- `ready_at`, `ended_at`, `expires_at` (timestamps): lifecycle timing.
- `worker_id`, `worker_hostname`: provenance metadata.
- `tmate_session_name`, `tmate_socket_path`: worker-side session identity.
- `attach_ro` (text): RO attach command/string.
- `attach_rw_encrypted` (encrypted text): RW attach command/string.
- `web_ro` (text): optional RO web URL.
- `web_rw_encrypted` (encrypted text): optional RW web URL.
- `rw_granted_until` (timestamp): TTL-bound RW reveal window.
- `last_heartbeat_at` (timestamp): most recent worker heartbeat.
- `error_message` (text): setup/runtime diagnostic detail.
- `created_at`, `updated_at` (timestamps).

### Validation Rules
- Exactly one live-session row per task run.
- `worker_id` must be non-empty for worker report/heartbeat operations.
- `grant-write` is valid only when status is `ready` and RW attach metadata exists.
- Terminal statuses (`revoked`, `ended`, `error`) set end-of-lifecycle semantics.

## Entity: TaskRunControlEvent (new append-only operator control audit)

### Fields
- `id` (UUID): primary key.
- `task_run_id` (UUID FK -> `agent_jobs.id`): owning run.
- `actor_user_id` (UUID FK -> `user.id`, nullable): initiating operator when available.
- `action` (string): e.g., `pause`, `resume`, `takeover`, `grant_rw`, `revoke_session`, `send_message`.
- `metadata_json` (JSON): structured payload (TTL, reason, message body, etc.).
- `created_at`, `updated_at` (timestamps).

### Validation Rules
- `action` must be non-empty.
- Operator messages are bounded in size and stored as structured metadata.
- Events are append-only records for auditability.

## Entity: LiveControlPayload (job payload extension)

### Shape
- Stored under `agent_jobs.payload.liveControl`.
- Fields: `paused` (bool), `takeover` (bool), `lastAction` (string), `updatedAt` (ISO timestamp).

### Behavior
- API control actions update this payload.
- Worker heartbeat reads payload and toggles pause checkpoint behavior.

## Entity: TaskRunLiveSessionWriteGrant (API response projection)

### Fields
- `session`: serialized `TaskRunLiveSession` view.
- `attach_rw`: revealed RW attach string.
- `web_rw`: optional revealed RW web URL.
- `granted_until`: expiration timestamp for RW reveal window.

### Behavior
- Exists as a response contract only; no separate persisted table.
