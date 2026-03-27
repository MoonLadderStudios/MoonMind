# Tmate Session Architecture

Status: Draft
Owners: MoonMind Engineering
Last Updated: 2026-03-26

---

## 1. Purpose

This document is the single authoritative reference for tmate session management in MoonMind. It consolidates design decisions previously scattered across `LiveTaskManagement.md` (section 4) and `TmateArchitecture.md` (sections 4–8), and defines the shared `TmateSessionManager` abstraction that both use cases consume or will align with.

### Related Documents

- `docs/Temporal/LiveTaskManagement.md` — live log tailing and terminal handoff UX
- `docs/ManagedAgents/TmateArchitecture.md` — tmate as a tool, Mission Control integration, OAuth session UX, and provider registry
- `docs/tmp/050-TmatePlan.md` — phased implementation status and diagnostics
- `docs/Security/ProviderProfiles.md` — provider profile management, OAuth volumes, and profile assignment
- `docs/tmp/SharedManagedAgentAbstractions.md` — strategy pattern and supervisor boundary

---

## 2. Use Cases

Tmate serves two distinct roles in MoonMind:

| Use Case | Description | Consumer |
|---|---|---|
| **Runtime wrapping** | Every managed agent run (Gemini, Codex, Claude, Cursor) is wrapped in a tmate session to enable live log tailing and terminal handoff from Mission Control. | `ManagedRuntimeLauncher.launch()` |
| **OAuth sessions** | Short-lived Docker containers with tmate give users an interactive terminal to complete provider OAuth login flows. | `oauth_session_activities.start_auth_runner()` |

Both share identical lifecycle concerns (session creation, readiness detection, endpoint extraction, teardown). The `TmateSessionManager` defined in section 4 **implements** runtime wrapping today; the OAuth path still uses a separate Docker-exec workflow and should converge on the same command and config semantics (see section 4.4).

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

If tmate crashes during startup, the launcher **logs a warning and falls back to a plain subprocess**. The agent executes normally without live terminal access. See section 4.5 for details.

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
- The `ERROR` lifecycle state (section 3) represents this scenario — the agent continues headless.
- Operator-facing live session status shows `DISABLED` or `ERROR` (no live output panel available).

---

## 5. Endpoint persistence for live runs

### 5.1 Data model

The **`task_run_live_sessions`** table (SQLAlchemy model **`TaskRunLiveSession`**) stores tmate endpoint metadata for a **task run**, enabling the Mission Control dashboard to render the Live Output panel. Contracts and field-level detail are in [`specs/024-live-task-handoff`](../../specs/024-live-task-handoff/).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `task_run_id` | UUID | Owning task run (unique) |
| `provider` | Enum | `tmate` (extensible) |
| `status` | Enum | Per lifecycle state machine (section 3) |
| `ready_at`, `ended_at`, `expires_at` | Timestamp | Lifecycle |
| `last_heartbeat_at` | Timestamp | Worker heartbeat |
| `created_at`, `updated_at` | Timestamp | Row metadata |
| `worker_id`, `worker_hostname` | String | Reporting worker |
| `tmate_session_name`, `tmate_socket_path` | String | Worker-local session identity |
| `attach_ro`, `web_ro` | Text | RO endpoints (safe for authorized viewers) |
| `attach_rw_encrypted`, `web_rw_encrypted` | Encrypted string | RW endpoints at rest |
| `rw_granted_until` | Timestamp | RW disclosure TTL (Phase 5 handoff) |
| `error_message` | Text | Failure diagnostics |

### 5.2 Worker reporting (implemented)

Live sessions are **not** updated via a dedicated Temporal activity in the current stack. The **managed agent queue worker** HTTP client (`moonmind/agents/codex_worker`; all managed CLI runtimes) calls the API service:

| HTTP | Purpose |
|---|---|
| `POST /api/task-runs/{id}/live-session/report` | Create or update session (e.g. `starting` → `ready` → `ended` / `error`) |
| `POST /api/task-runs/{id}/live-session/heartbeat` | Heartbeat while the session is alive |
| `GET /api/task-runs/{id}/live-session/worker` | Worker fetch of current payload (including encrypted RW material) |

Router: `api_service/api/routers/task_runs.py`.

### 5.3 Operator API

```
GET /api/task-runs/{id}/live-session
```

Response when ready includes `web_ro` and `attach_ro` for the Live Output panel (iframe to tmate’s web viewer). Full response shape is defined in the OpenAPI under [`specs/024-live-task-handoff`](../../specs/024-live-task-handoff/).

---

## 6. Cleanup and Garbage Collection

### 6.1 Socket File Cleanup

`TmateSessionManager.teardown()` removes the socket, config, and exit-code files. The supervisor calls `teardown()` in its `finally` block.

### 6.2 Orphaned Socket GC

On worker startup, the supervisor reconciliation phase scans `/tmp/moonmind/tmate/` and removes socket files for runs not in the active process table. This handles crashes where `teardown()` was never called.

### 6.3 OAuth container cleanup

The `oauth_session.cleanup_stale` activity transitions DB status and, when `container_name` is set, calls **`docker stop`** + **`docker rm`** for stale OAuth runner containers.

---

## 7. Security Model

- **RO by default**: The `web_ro` and `attach_ro` endpoints are safe to display to authorized viewers.
- **RW time-bounded**: RW endpoints are stored encrypted and only revealed via explicit operator grant with a TTL (default: 15 minutes). See `LiveTaskManagement.md` section 9 for details.
- **Self-hosted server**: For production, configure `MOONMIND_TMATE_SERVER_HOST` to use a private relay server. Sessions on `tmate.io` traverse public infrastructure.
- **Short-lived sessions**: OAuth sessions default to 30-minute TTL with auto-expire. Runtime sessions live only as long as the agent process.

---

## 8. Image Requirements

The worker Docker image must include:

- `tmate` (installed in `Dockerfile` — confirmed present)
- `openssh-client` (tmate dependency for outbound SSH)
- `ca-certificates` (for TLS to relay server)

---

## 9. Implementation phases

Authoritative phased status, open items (OAuth integration test, OAuth + managed queue worker tmate consolidation, Phase 5 RW handoff), and production diagnostics live in [`docs/tmp/050-TmatePlan.md`](../tmp/050-TmatePlan.md). Short index: [`docs/tmp/remaining-work/ManagedAgents-UniversalTmateOAuth.md`](../tmp/remaining-work/ManagedAgents-UniversalTmateOAuth.md).
