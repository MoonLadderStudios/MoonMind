"""One small authenticated local endpoint for the standalone controller.

Stdlib-only (``http.server``). This endpoint is how the host CLI and the
optional UI observer submit or observe the same controller operation; it is
not a general orchestration surface. Every request requires the
deployment-owned bearer secret (see :mod:`auth`). The controller never
replaces itself: there is no self-update route here; controller update is
host-owned (see ``tools/install-moonmind-controller.sh``).
"""

from __future__ import annotations

from moonmind_controller import auth


def is_authorized(headers: dict, secret: str | None) -> bool:
    """Check the Authorization header against the deployment-owned secret."""
    if not secret:
        return False
    presented = str((headers or {}).get("Authorization") or "")
    scheme, _, token = presented.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    return auth.verify_bearer(token.strip(), secret)


def parse_operation_payload(raw: dict) -> dict:
    """Validate a submitted handoff payload (release config as data).

    The payload carries the desired target as data only; it never executes
    application code. Returns a normalized ``new_operation`` record.
    """
    if not isinstance(raw, dict):
        raise ValueError("Controller operation payload must be an object.")
    operation_id = str(raw.get("operationId") or "").strip()
    desired = raw.get("desired") or {}
    target_image = str(desired.get("targetImage") or "").strip()
    if not operation_id:
        raise ValueError("Controller operation payload names no operationId.")
    if not target_image:
        raise ValueError("Controller operation payload names no targetImage.")
    from moonmind_controller import state as _state

    record = _state.new_operation(
        operation_id=operation_id, target_image=target_image
    )
    services = desired.get("services")
    if services is not None:
        record["desired"]["services"] = [str(item) for item in list(services)]
    # Target-project wiring travels as data: the Compose project name plus
    # the controller-side project directory/compose files that scope every
    # ``docker compose`` invocation. Types are validated; absent stays
    # absent and the serving path falls back to its configured project.
    stack = desired.get("stack")
    if stack is not None:
        if not str(stack).strip():
            raise ValueError("Controller target stack must not be empty.")
        record["desired"]["stack"] = str(stack).strip()
    project_directory = desired.get("projectDirectory")
    if project_directory is not None:
        if not str(project_directory).strip():
            raise ValueError("Controller target projectDirectory must not be empty.")
        record["desired"]["projectDirectory"] = str(project_directory).strip()
    compose_files = desired.get("composeFiles")
    if compose_files is not None:
        if (
            not isinstance(compose_files, list)
            or not compose_files
            or not all(str(item).strip() for item in compose_files)
        ):
            raise ValueError("Controller target composeFiles must be a non-empty list.")
        record["desired"]["composeFiles"] = [
            str(item).strip() for item in compose_files
        ]
    concrete = desired.get("concreteImages")
    if concrete is not None:
        if not isinstance(concrete, dict):
            raise ValueError("concreteImages must be an object.")
        record = _state.record_concrete_images(record, dict(concrete))
    # Trusted pre-apply validation data round-trips as plain data so
    # validate_target checks are non-vacuous for real handoffs. Absent
    # fields stay absent; explicitly empty mappings are refused at apply.
    for key in ("authorization", "storage", "accessSettings"):
        value = desired.get(key)
        if value is not None:
            if not isinstance(value, dict):
                raise ValueError(f"{key} must be an object.")
            record["desired"][key] = dict(value)
    previous = raw.get("previousRelease")
    if previous is not None:
        if not isinstance(previous, dict):
            raise ValueError("previousRelease must be an object.")
        compatible = raw.get("previousCompatible", True)
        record = _state.record_previous_release(
            record, previous=dict(previous), compatible=bool(compatible)
        )
    return record


def status_payload(record: dict) -> dict:
    """Render the human-readable operation summary served to operators."""
    desired = record.get("desired") or {}
    installed = record.get("installed")
    attempts = list(record.get("attempts") or [])
    payload = {
        "operationId": record.get("operationId"),
        "status": record.get("status"),
        "desiredImage": desired.get("targetImage"),
        "installedImage": (installed or {}).get("image")
        if isinstance(installed, dict)
        else None,
        "attempt": record.get("attempt", 0),
        "lastError": attempts[-1].get("error") if attempts else None,
    }
    return payload


OPERATION_FILENAME = "operation.json"


def _state_path_for(root) -> object:
    from pathlib import Path as _Path

    return _Path(root) / OPERATION_FILENAME


def _lock_dir_for(state_dir, explicit=None) -> object:
    from pathlib import Path as _Path

    if explicit is not None:
        return _Path(explicit)
    return _Path(state_dir) / "locks"


def build_target_from_record(record: dict) -> dict:
    """Derive the apply target from the persisted desired state (data only)."""
    desired = record.get("desired") or {}
    target: dict = {
        "targetImage": desired.get("targetImage"),
        "services": list(desired.get("services") or []),
    }
    for key in ("stack", "projectDirectory"):
        value = desired.get(key)
        if value is not None:
            target[key] = value
    compose_files = desired.get("composeFiles")
    if compose_files is not None:
        target["composeFiles"] = list(compose_files)
    concrete = desired.get("concreteImages")
    if concrete is not None:
        target["concreteImages"] = dict(concrete)
    for key in ("authorization", "storage", "accessSettings"):
        value = desired.get(key)
        if value is not None:
            target[key] = dict(value) if isinstance(value, dict) else value
    prepared = record.get("prepared") or {}
    if isinstance(prepared, dict) and prepared.get("child") is not None:
        target["child"] = prepared.get("child")
    return target


def _previous_release_for(
    record: dict,
    *,
    previous_release: dict | None = None,
    previous_compatible: bool | None = None,
) -> tuple[dict | None, bool]:
    """Resolve compatibility-gated retention context for the serving path.

    Explicit caller values win. Otherwise the persisted record supplies
    them: an already-recorded ``previousRelease`` is reused, else the
    currently installed image/services become the previous release so a
    second update cannot silently drop the retention baseline. No automatic
    database downgrade is implied; incompatibility clears retention.
    """
    if previous_release is not None:
        compatible = True if previous_compatible is None else bool(previous_compatible)
        return dict(previous_release), compatible
    stored = record.get("previousRelease")
    if isinstance(stored, dict):
        return dict(stored), bool(record.get("previousReleaseCompatible", True))
    installed = record.get("installed")
    if isinstance(installed, dict) and installed.get("image"):
        return (
            {"image": installed.get("image"), "services": dict(installed.get("services") or {})},
            True if previous_compatible is None else bool(previous_compatible),
        )
    return None, True if previous_compatible is None else bool(previous_compatible)


def resolve_target_project(
    target: dict | None, server_project: dict | None, stack: str = "moonmind"
) -> dict:
    """Resolve Compose scoping for one apply (payload > server > default).

    The handoff names the Compose project; the serving process configures
    the controller-side project directory/compose files (bind-mounted
    target checkout). Missing directory/files omit the flags so unit
    boundaries keep historical bare commands; production relies on the
    installer's bind mount, and a missing checkout fails loudly inside
    Compose instead of mutating the wrong directory.
    """
    target = target or {}
    server_project = server_project or {}
    files = target.get("composeFiles")
    if files is None:
        files = server_project.get("files")
    return {
        "name": target.get("stack")
        or server_project.get("name")
        or str(stack or "moonmind"),
        "directory": target.get("projectDirectory") or server_project.get("directory"),
        "files": tuple(files or ()),
    }


def collect_service_statuses(
    *,
    project: dict,
    services: tuple[str, ...],
    run=None,
    env: dict | None = None,
) -> dict | None:
    """Observe service liveness via ``docker compose ps`` (read-only).

    Returns ``{service: state}`` or ``None`` when the deployment cannot be
    observed; ``None`` is unverified, never clean. Any runner failure or
    unparsable output yields ``None`` rather than replacing the caller's
    failure with a probe error.
    """
    import json as _json

    from moonmind_controller import apply as _apply
    from moonmind_controller import compose as _compose

    runner = run or _apply._default_run
    command = _compose.build_ps_command(
        services=tuple(services or ()),
        project=project.get("name"),
        project_directory=project.get("directory"),
        compose_files=tuple(project.get("files") or ()),
    )
    try:
        result = runner(
            command, timeout=_compose.PS_TIMEOUT_SECONDS, env=env or None
        )
    except Exception:
        return None
    if result.returncode:
        return None
    try:
        payload = _json.loads(result.output or "")
    except ValueError:
        return None
    items = (
        payload
        if isinstance(payload, list)
        else ([payload] if isinstance(payload, dict) else None)
    )
    if items is None:
        return None
    statuses: dict = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("Service") or item.get("service") or item.get("Name")
        state = item.get("State") or item.get("state")
        if name:
            statuses[str(name)] = str(state or "unknown")
    return statuses


def execute_operation(
    state_path,
    record: dict,
    *,
    lock_dir,
    stack: str = "moonmind",
    run=None,
    service_statuses: dict | None = None,
    operator_access_ok: bool | None = None,
    artifact_store_ok: bool | None = None,
    previous_release: dict | None = None,
    previous_compatible: bool | None = None,
    target_project: dict | None = None,
) -> dict:
    """Execute one reserved operation toward its persisted target.

    The ``run`` boundary executes Compose commands and stays injectable so
    unit tests exercise the serving path without Docker. Unsupplied
    ``service_statuses`` are collected from the wired target project via
    ``docker compose ps``; observations left unavailable fail every
    mandatory check instead of recording an uninspected success. The
    prepared target (with selected concrete images) is persisted before
    the first Compose side effect so interruption reconciles against the
    selected inputs. Returns the updated record persisted to ``state_path``.
    Already-complete records are not repeated; exhausted records wait for an
    explicit Retry. Failures are recorded with redacted diagnostics before
    re-raising so the controller stays usable and mandatory checks remain
    explicit.
    """
    from moonmind_controller import apply as _apply
    from moonmind_controller import redact as _redact
    from moonmind_controller import state as _state

    if _state.apply_already_complete(record):
        return record
    history = list(record.get("attempts") or [])
    if len(history) >= _state.MAX_ATTEMPTS:
        return record
    target = build_target_from_record(record)
    installed = record.get("installed") if isinstance(record.get("installed"), dict) else {}
    installed_images = dict((installed or {}).get("services") or {})
    installed_config = record.get("installedConfig")
    resolved_previous, resolved_compatible = _previous_release_for(
        record,
        previous_release=previous_release,
        previous_compatible=previous_compatible,
    )
    project = resolve_target_project(target, target_project, stack)
    if "stack" not in target and project.get("name"):
        target = {**target, "stack": project["name"]}
    if project.get("directory") and "projectDirectory" not in target:
        target = {**target, "projectDirectory": project["directory"]}
    if project.get("files") and "composeFiles" not in target:
        target = {**target, "composeFiles": list(project["files"])}
    # Persist the prepared target before the first Compose side effect: a
    # kill during pull/up restarts from the selected concrete images and
    # prepared target instead of the original unprepared record.
    prepared_record, _staged = _apply.prepare_apply_record(
        record=record,
        target=target,
        installed_images=installed_images,
    )
    _state.write_record(state_path, prepared_record)
    if service_statuses is None:
        service_statuses = collect_service_statuses(
            project=project,
            services=tuple(target.get("services") or ()),
            run=run,
        )
    try:
        updated = _apply.locked_orchestrate_apply(
            lock_dir=lock_dir,
            stack=stack,
            record=prepared_record,
            target=target,
            installed_images=installed_images,
            installed_config=installed_config,
            service_statuses=service_statuses,
            operator_access_ok=operator_access_ok,
            artifact_store_ok=artifact_store_ok,
            previous_release=resolved_previous,
            previous_compatible=resolved_compatible,
            run=run,
        )
    except Exception as exc:
        message = _redact.redact(str(exc) or type(exc).__name__)[:2000]
        try:
            current = _state.read_record(state_path)
            next_attempt = int(current.get("attempt") or 0) + 1
            fallback = len(list(current.get("attempts") or [])) + 1
            next_attempt = max(next_attempt, fallback, len(history) + 1)
            _state.record_attempt_error(
                state_path, attempt=next_attempt, error=message
            )
        except (OSError, ValueError):
            # The attempt error is already recorded below via record_attempt_error
            # when the state file is usable; an unreadable state file here only
            # means the failure was already raised to the caller, so skip the
            # bookkeeping rather than replacing the original error.
            pass
        raise
    _state.write_record(state_path, updated)
    return _state.read_record(state_path)


def handle_operation_submission(
    root,
    raw: dict,
    *,
    lock_dir=None,
    stack: str = "moonmind",
    run=None,
    service_statuses: dict | None = None,
    operator_access_ok: bool | None = None,
    artifact_store_ok: bool | None = None,
    previous_release: dict | None = None,
    previous_compatible: bool | None = None,
    target_project: dict | None = None,
) -> tuple[dict, str | None, int]:
    """Parse, reserve, then execute one controller submission.

    Returns ``(record, error, status_code)`` where ``record`` is the persisted
    status to render, ``error`` is a redacted failure (or ``None``), and
    ``status_code`` is the HTTP status the endpoint should return. Invalid
    payloads are answered with a structured 400 instead of escaping the
    handler and closing the connection. The run boundary stays injectable
    so tests prove execution without Docker.
    """
    from moonmind_controller import state as _state

    try:
        record = parse_operation_payload(raw)
    except ValueError as exc:
        return {}, str(exc)[:2000], 400
    state_path = _state_path_for(root)
    try:
        stored = _state.reserve_record(state_path, record)
    except ValueError as exc:
        return {}, str(exc)[:2000], 400
    except OSError as exc:
        return {}, f"Controller state is unavailable: {exc}"[:2000], 500
    try:
        final = execute_operation(
            state_path,
            stored,
            lock_dir=_lock_dir_for(root, lock_dir),
            stack=stack,
            run=run,
            service_statuses=service_statuses,
            operator_access_ok=operator_access_ok,
            artifact_store_ok=artifact_store_ok,
            previous_release=previous_release,
            previous_compatible=previous_compatible,
            target_project=target_project,
        )
    except ValueError as exc:
        try:
            final = _state.read_record(state_path)
        except (OSError, ValueError):
            final = stored
        return final, str(exc)[:2000], 400
    except Exception as exc:
        try:
            final = _state.read_record(state_path)
        except (OSError, ValueError):
            final = stored
        message = str(exc)
        if len(message) > 2000:
            message = message[:2000]
        return final, message, 500
    return final, None, 200


def converge_on_startup(
    state_dir,
    *,
    lock_dir=None,
    stack: str = "moonmind",
    run=None,
    child_running: bool = False,
    child_owner_matches: bool = True,
    service_statuses: dict | None = None,
    operator_access_ok: bool | None = None,
    artifact_store_ok: bool | None = None,
    previous_release: dict | None = None,
    previous_compatible: bool | None = None,
    target_project: dict | None = None,
) -> str:
    """Inspect Docker state on restart and converge unfinished work.

    Returns ``no-record``, ``complete``, ``await_retry``, ``resumed``, or
    ``resume-failed``. Best-effort: failures are recorded with redacted
    diagnostics and never prevent the endpoint from serving.
    """
    from moonmind_controller import service as _service
    from moonmind_controller import state as _state

    state_path = _state_path_for(state_dir)
    try:
        record = _state.read_record(state_path)
    except (OSError, ValueError):
        return "no-record"
    decision = _service.converge_on_restart(
        record, child_running=child_running, child_owner_matches=child_owner_matches
    )
    if decision != "resume":
        return decision
    try:
        execute_operation(
            state_path,
            record,
            lock_dir=_lock_dir_for(state_dir, lock_dir),
            stack=stack,
            run=run,
            service_statuses=service_statuses,
            operator_access_ok=operator_access_ok,
            artifact_store_ok=artifact_store_ok,
            previous_release=previous_release,
            previous_compatible=previous_compatible,
            target_project=target_project,
        )
    except Exception:
        return "resume-failed"
    return "resumed"


def default_compose_files(project_directory: str | None) -> tuple[str, ...]:
    """Default Compose files for the wired target checkout (data only)."""
    from pathlib import Path as _Path

    if not project_directory:
        return ()
    root = _Path(project_directory)
    files = []
    base = root / "docker-compose.yaml"
    if base.exists():
        files.append(str(base))
    else:
        base_yml = root / "docker-compose.yml"
        if base_yml.exists():
            files.append(str(base_yml))
    for name in ("docker-compose.override.yaml", "docker-compose.override.yml"):
        override = root / name
        if override.exists():
            files.append(str(override))
            break
    return tuple(files)


def serve(
    *,
    host: str = "0.0.0.0",
    port: int = 8099,
    state_dir: str,
    lock_dir: str | None = None,
    stack: str = "moonmind",
    run=None,
    project_directory: str | None = None,
    compose_files: tuple[str, ...] | None = None,
) -> None:
    """Serve the authenticated endpoint (container interface, host-loopback published).

    Inside the controller container the server binds ``0.0.0.0``: the
    Compose service publishes the port as loopback-only on the host
    (``127.0.0.1:8099:8099``), so binding the container loopback would
    refuse connections arriving through the container network interface.
    The host publication, not this bind address, is the access control.
    """
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    secret = auth.secret_from_environ()
    root = Path(state_dir)
    resolved_lock_dir = _lock_dir_for(root, lock_dir)
    if project_directory is None:
        import os as _os

        project_directory = _os.environ.get(
            "MOONMIND_TARGET_PROJECT_DIR", "/target/moonmind"
        )
    resolved_files = (
        tuple(compose_files)
        if compose_files is not None
        else default_compose_files(project_directory)
    )
    server_project = {
        "name": stack,
        "directory": project_directory,
        "files": resolved_files,
    }
    # Concurrent submissions serialize here so two POSTs cannot enter the
    # same apply; reads (GET) stay concurrent. Long applies run in their
    # handler thread while status probes keep serving.
    submission_lock = threading.Lock()

    try:
        converge_on_startup(
            root,
            lock_dir=resolved_lock_dir,
            stack=stack,
            run=run,
            target_project=server_project,
        )
    except Exception:
        # Startup convergence is best-effort (see converge_on_startup): a
        # failed resume is recorded, and the endpoint still starts serving.
        pass

    class Handler(BaseHTTPRequestHandler):
        def _deny(self) -> None:
            body = b"unauthorized"
            self.send_response(401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server convention
            if not is_authorized(dict(self.headers), secret):
                self._deny()
                return
            if self.path == "/healthz":
                body = b"ok"
            elif self.path == "/operation":
                try:
                    record = json.loads((root / "operation.json").read_text())
                except (OSError, ValueError):
                    self.send_response(404)
                    self.end_headers()
                    return
                body = (json.dumps(status_payload(record), sort_keys=True) + "\n").encode()
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - http.server convention
            if not is_authorized(dict(self.headers), secret):
                self._deny()
                return
            if self.path != "/operation":
                self.send_response(404)
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            try:
                raw = json.loads(self.rfile.read(length or 0).decode() or "{}")
            except (ValueError, OSError) as exc:
                body = (json.dumps({"error": str(exc)}) + "\n").encode()
                self.send_response(400)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            try:
                with submission_lock:
                    final, error, status_code = handle_operation_submission(
                        root,
                        raw,
                        lock_dir=resolved_lock_dir,
                        stack=stack,
                        run=run,
                        target_project=server_project,
                    )
            except (BrokenPipeError, ConnectionResetError):
                # A timed-out client may be gone while its apply continues on
                # the durable record; it reattaches through GET /operation.
                return
            if error is not None:
                body = (
                    json.dumps(
                        {"error": error, "operation": status_payload(final)},
                        sort_keys=True,
                    )
                    + "\n"
                ).encode()
                self.send_response(status_code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    return
                return
            body = (
                json.dumps(status_payload(final), sort_keys=True) + "\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, *args: object) -> None:  # keep logs readable
            pass

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    httpd.serve_forever()
