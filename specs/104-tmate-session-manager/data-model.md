# Data Model: TmateSessionManager

**Feature**: 104-tmate-session-manager

## Entities

### TmateEndpoints

Value object holding extracted tmate session endpoints.

| Field | Type | Description |
|---|---|---|
| `session_name` | `str` | Named session identifier (e.g., `mm-abc123def456`) |
| `socket_path` | `str` | Filesystem path to the tmate socket |
| `attach_ro` | `str \| None` | Read-only SSH attach string |
| `attach_rw` | `str \| None` | Read-write SSH attach string |
| `web_ro` | `str \| None` | Read-only web viewer URL |
| `web_rw` | `str \| None` | Read-write web viewer URL |

### TmateServerConfig

Configuration for connecting to a self-hosted tmate relay.

| Field | Type | Default | Env Var |
|---|---|---|---|
| `host` | `str` | (required) | `MOONMIND_TMATE_SERVER_HOST` |
| `port` | `int` | `22` | `MOONMIND_TMATE_SERVER_PORT` |
| `rsa_fingerprint` | `str` | `""` | `MOONMIND_TMATE_SERVER_RSA_FINGERPRINT` |
| `ed25519_fingerprint` | `str` | `""` | `MOONMIND_TMATE_SERVER_ED25519_FINGERPRINT` |

### TmateSessionManager

Manages a single tmate session lifecycle.

| Field | Type | Description |
|---|---|---|
| `_session_name` | `str` | Session identifier |
| `_socket_dir` | `Path` | Directory for socket and config files |
| `_server_config` | `TmateServerConfig \| None` | Self-hosted server settings |
| `_process` | `Process \| None` | Running tmate subprocess |
| `_endpoints` | `TmateEndpoints \| None` | Last extracted endpoints |
| `_exit_code_path` | `Path \| None` | Path to exit code file |
| `_socket_path` | `Path` | Computed: `socket_dir / f"{session_name}.sock"` |
| `_config_path` | `Path` | Computed: `socket_dir / f"{session_name}.conf"` |

## State Transitions

```
(not started) → start() → READY
READY → teardown() → ENDED
start() timeout → ERROR
```

## Filesystem Artifacts (per session)

| File | Path | Lifecycle |
|---|---|---|
| Socket | `<socket_dir>/<session_name>.sock` | Created by tmate, removed by teardown |
| Config | `<socket_dir>/<session_name>.conf` | Written before start, removed by teardown |
| Exit code | `<socket_dir>/<session_name>.exit` | Written by wrapped command, removed by teardown |
