# Tmate Session Architecture

Status: Draft
Owners: MoonMind Engineering
Last Updated: 2026-03-24

---

## 1. Purpose

This document is the single authoritative reference for tmate session management in MoonMind. It consolidates design decisions previously scattered across `LiveTaskManagement.md` (§4) and `TmateArchitecture.md` (§4–8), and defines the shared `TmateSessionManager` abstraction that both use cases will consume.

### Related Documents

- `docs/Temporal/LiveTaskManagement.md` — live log tailing and terminal handoff UX
- `docs/ManagedAgents/TmateArchitecture.md` — tmate as a tool, Mission Control integration, OAuth session UX, and provider registry
- `docs/Security/AuthProfiles.md` — auth profile management, OAuth volumes, and profile assignment
- `docs/tmp/SharedManagedAgentAbstractions.md` — strategy pattern and supervisor boundary

---

## 2. Use Cases

Tmate serves two distinct roles in MoonMind:

| Use Case | Description | Consumer |
|---|---|---|
| **Runtime wrapping** | Every managed agent run (Gemini, Codex, Claude, Cursor) is wrapped in a tmate session to enable live log tailing and terminal handoff from Mission Control. | `ManagedRuntimeLauncher.launch()` |
| **OAuth sessions** | Short-lived Docker containers with tmate give users an interactive terminal to complete provider OAuth login flows. | `oauth_session_activities.start_auth_runner()` |

Both share identical lifecycle concerns (session creation, readiness detection, endpoint extraction, teardown) but are currently implemented independently. The `TmateSessionManager` defined in §4 is the **target abstraction** that will unify them.

---

## 3. Session Lifecycle

```
DISABLED → STARTING → READY → (ENDED | REVOKED | ERROR)
```

| State | Description |
|---|---|
| `DISABLED` | No live session provisioned (tmate binary not available, or feature disabled). |
| `STARTING` | Socket created, waiting for tmate to connect to relay server. |
| `READY` | Endpoints extracted. RO endpoint available; RW endpoint stored (encrypted at rest for live sessions). |
| `ENDED` | Normal teardown — process completed or session expired. |
| `REVOKED` | Operator explicitly terminated the session. |
| `ERROR` | tmate setup failed; agent continues headless (graceful fallback). |

---

## 4. Shared Abstraction: `TmateSessionManager`

> [!NOTE]
> `TmateSessionManager` is **implemented** in `moonmind/workflows/temporal/runtime/tmate_session.py`. The `ManagedRuntimeLauncher` consumes it for runtime wrapping. The OAuth session path (`oauth_session_activities.py`) still uses a separate Docker-exec polling approach.

### 4.1 Location

```
moonmind/workflows/temporal/runtime/tmate_session.py
```

### 4.2 API

```python
@dataclass
class TmateEndpoints:
    session_name: str
    socket_path: str
    attach_ro: str | None = None
    attach_rw: str | None = None
    web_ro: str | None = None
    web_rw: str | None = None


class TmateSessionManager:
    """Manages a tmate session lifecycle."""

    def __init__(
        self,
        *,
        session_name: str,
        socket_dir: Path = Path("/tmp/moonmind/tmate"),
        server_config: TmateServerConfig | None = None,
    ) -> None: ...

    @staticmethod
    def is_available() -> bool:
        """True if the tmate binary is on PATH."""

    async def start(
        self,
        command: list[str] | str | None = None,
        *,
        env: dict[str, str] | None = None,
        cwd: Path | str | None = None,
        exit_code_capture: bool = True,
        timeout_seconds: float = 30.0,
    ) -> TmateEndpoints:
        """Start a tmate session wrapping the given command.

        1. Creates socket dir and config file
        2. Launches ``tmate -S <sock> -f <conf> -F new-session ...``
        3. Waits for readiness via ``tmate wait tmate-ready``
        4. Extracts all four endpoint types
        5. Returns TmateEndpoints (partial if extraction fails)
        """

    @property
    def endpoints(self) -> TmateEndpoints | None:
        """Last extracted endpoints, or None if not yet started."""

    @property
    def exit_code_path(self) -> Path | None:
        """Path to the exit code file when exit_code_capture is enabled."""

    async def teardown(self) -> None:
        """Kill the tmate session and clean up socket/config/exit-code files."""
```

### 4.3 Self-Hosted Server Configuration

```python
@dataclass
class TmateServerConfig:
    host: str                           # MOONMIND_TMATE_SERVER_HOST
    port: int = 22                      # MOONMIND_TMATE_SERVER_PORT
    rsa_fingerprint: str = ""           # MOONMIND_TMATE_SERVER_RSA_FINGERPRINT
    ed25519_fingerprint: str = ""       # MOONMIND_TMATE_SERVER_ED25519_FINGERPRINT
```

When `TmateServerConfig` is provided, the manager writes the corresponding `set-option` directives into the per-session config file. When `None`, sessions connect to `tmate.io` (default).

Environment variables (already templated in `.env-template`):

```
MOONMIND_TMATE_SERVER_HOST=""
MOONMIND_TMATE_SERVER_PORT=""
MOONMIND_TMATE_SERVER_RSA_FINGERPRINT=""
MOONMIND_TMATE_SERVER_ED25519_FINGERPRINT=""
```

### 4.4 Consumer Wiring

#### Runtime Wrapping (launcher.py) — ✅ Migrated

`ManagedRuntimeLauncher.launch()` uses `TmateSessionManager` with **graceful fallback**:

```python
if TmateSessionManager.is_available():
    mgr = TmateSessionManager(session_name=f"mm-{run_id[:16]}")
    try:
        endpoints = await mgr.start(cmd, env=env_overrides, cwd=workspace)
        process = mgr.process
        # verify process is actually alive ...
    except Exception:
        logger.warning("Tmate failed; falling back to plain subprocess.")
        await mgr.teardown()
        # fall through to plain subprocess

if not use_tmate:
    process = await asyncio.create_subprocess_exec(*cmd, ...)
```

If tmate crashes during startup, the launcher **logs a warning and falls back to a plain subprocess**. The agent executes normally without live terminal access. See §4.5 for details.

#### OAuth Sessions (oauth_session_activities.py) — ⬜ Not Yet Migrated

Still uses a Docker-exec polling approach. Future migration will have the container entrypoint use `TmateSessionManager` directly or write endpoints to a well-known file.

### 4.5 Graceful Fallback on Tmate Failure

Tmate is an **enhancement, not a requirement** for agent execution. If tmate fails to start (binary issue, relay unreachable, socket error, etc.), the launcher falls back to a plain subprocess:

1. The tmate `start()` call is wrapped in `try/except`.
2. After `start()` succeeds, the launcher verifies the tmate-wrapped process is actually alive (non-None returncode check).
3. On any failure:
   - The failed tmate session is torn down (`teardown()`) to clean up socket/config files.
   - `tmate_manager` is set to `None` and `use_tmate` is set to `False`.
   - The launcher falls through to the plain `asyncio.create_subprocess_exec` path.
4. The agent runs normally, but without live terminal access from Mission Control.

This means:
- **Workflows never fail due to tmate infrastructure issues.**
- The `ERROR` lifecycle state (§3) represents this scenario — the agent continues headless.
- Operator-facing live session status shows `DISABLED` or `ERROR` (no live output panel available).

---

## 5. Endpoint Persistence for Live Runs

### 5.1 Data Model

The `workflow_live_sessions` table stores tmate endpoint metadata for running tasks, enabling the Mission Control dashboard to render the Live Output panel.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `workflow_id` | String | Links to owning Temporal Workflow ID |
| `provider` | Enum | `tmate` (extensible) |
| `status` | Enum | Per lifecycle state machine (§3) |
| `created_at`, `ready_at`, `ended_at` | Timestamp | Lifecycle timestamps |
| `worker_identity` | String | Temporal worker ID for audit/support |
| `tmate_session_name` | String | Session name |
| `tmate_socket_path` | String | Worker-local socket path |
| `attach_ro` | String | RO SSH attach string (safe to display) |
| `attach_rw_encrypted` | String | RW SSH string, encrypted at rest |
| `web_ro` | String | Web viewer URL (RO) |
| `web_rw_encrypted` | String | Web viewer URL (RW), encrypted |
| `expires_at` | Timestamp | Session expiry time; NULL for sessions that live until process exit |
| `last_heartbeat_at` | Timestamp | Updated by worker while session is alive |
| `error_message` | String | Optional failure diagnostics |

### 5.2 Activities

| Activity | Queue | Purpose |
|---|---|---|
| `agent_runtime.report_live_session` | `mm.activity.agent_runtime` | Persist `TmateEndpoints` to `workflow_live_sessions` after session becomes `READY` |
| `agent_runtime.end_live_session` | `mm.activity.agent_runtime` | Transition session to `ENDED` when run completes |

### 5.3 API

```
GET /api/workflows/{id}/live-session
```

Response when `status = READY`:
```json
{
  "status": "READY",
  "web_ro": "https://tmate.io/t/ro-xxxxxxxxxxxx",
  "attach_ro": "ssh ro-xxxxxxxxxxxx@nyc1.tmate.io"
}
```

The dashboard uses `web_ro` for the embedded terminal viewer (iframe or xterm.js). No new infrastructure beyond this endpoint is needed for Phase 1 (live log tailing).

---

## 6. Cleanup and Garbage Collection

### 6.1 Socket File Cleanup

`TmateSessionManager.teardown()` removes the socket, config, and exit-code files. The supervisor calls `teardown()` in its `finally` block.

### 6.2 Orphaned Socket GC

On worker startup, the supervisor reconciliation phase scans `/tmp/moonmind/tmate/` and removes socket files for runs not in the active process table. This handles crashes where `teardown()` was never called.

### 6.3 OAuth Container Cleanup

The `oauth_session.cleanup_stale` activity already transitions DB status to `expired`. It should additionally call `docker stop` + `docker rm` for the stale session's `container_name`.

---

## 7. Security Model

- **RO by default**: The `web_ro` and `attach_ro` endpoints are safe to display to authorized viewers.
- **RW time-bounded**: RW endpoints are stored encrypted and only revealed via explicit operator grant with a TTL (default: 15 minutes). See `LiveTaskManagement.md` §9 for details.
- **Self-hosted server**: For production, configure `MOONMIND_TMATE_SERVER_HOST` to use a private relay server. Sessions on `tmate.io` traverse public infrastructure.
- **Short-lived sessions**: OAuth sessions default to 30-minute TTL with auto-expire. Runtime sessions live only as long as the agent process.

---

## 8. Image Requirements

The worker Docker image must include:

- `tmate` (installed in `Dockerfile` — confirmed present)
- `openssh-client` (tmate dependency for outbound SSH)
- `ca-certificates` (for TLS to relay server)

---

## 9. Implementation Phases

The phased rollout plan is tracked in the implementation plan artifact. Summary:

1. ~~**Phase 1**: Extract `TmateSessionManager`, wire self-hosted config, fix dead code and status enum~~ ✅ Complete
2. **Phase 2**: Endpoint persistence (`workflow_live_sessions` table + API)
3. **Phase 3**: Dashboard live output panel (iframe/xterm.js embedding)
4. **Phase 4**: OAuth bootstrap error reporting + container cleanup
5. **Phase 5**: Full live terminal handoff (RW grants, operator messages, pause/resume)
