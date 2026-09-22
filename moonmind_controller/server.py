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


def execute_operation(
    state_path,
    record: dict,
    *,
    lock_dir,
    stack: str = "moonmind",
    run=None,
    service_statuses: dict | None = None,
    operator_access_ok: bool | None = True,
    artifact_store_ok: bool | None = None,
    previous_release: dict | None = None,
    previous_compatible: bool | None = None,
) -> dict:
    """Execute one reserved operation toward its persisted target.

    The ``run`` boundary executes Compose commands and stays injectable so
    unit tests exercise the serving path without Docker. Post-apply
    observations (``service_statuses``/``operator_access_ok``/
    ``artifact_store_ok``) default to the orchestrator's own defaults; live
    Docker/operator probes can supply them without changing this boundary.
    Returns the updated record persisted to ``state_path``.
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
    try:
        updated = _apply.locked_orchestrate_apply(
            lock_dir=lock_dir,
            stack=stack,
            record=record,
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
    operator_access_ok: bool | None = True,
    artifact_store_ok: bool | None = None,
    previous_release: dict | None = None,
    previous_compatible: bool | None = None,
) -> tuple[dict, str | None, int]:
    """Parse, reserve, then execute one controller submission.

    Returns ``(record, error, status_code)`` where ``record`` is the persisted
    status to render, ``error`` is a redacted failure (or ``None``), and
    ``status_code`` is the HTTP status the endpoint should return. The run
    boundary stays injectable so tests prove execution without Docker.
    """
    from moonmind_controller import state as _state

    record = parse_operation_payload(raw)
    state_path = _state_path_for(root)
    stored = _state.reserve_record(state_path, record)
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
    operator_access_ok: bool | None = True,
    artifact_store_ok: bool | None = None,
    previous_release: dict | None = None,
    previous_compatible: bool | None = None,
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
        )
    except Exception:
        return "resume-failed"
    return "resumed"


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8099,
    state_dir: str,
    lock_dir: str | None = None,
    stack: str = "moonmind",
    run=None,
) -> None:
    """Serve the authenticated endpoint on loopback (deployment-local)."""
    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from pathlib import Path

    secret = auth.secret_from_environ()
    root = Path(state_dir)
    resolved_lock_dir = _lock_dir_for(root, lock_dir)

    try:
        converge_on_startup(
            root, lock_dir=resolved_lock_dir, stack=stack, run=run
        )
    except Exception:
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
            final, error, status_code = handle_operation_submission(
                root,
                raw,
                lock_dir=resolved_lock_dir,
                stack=stack,
                run=run,
            )
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
                self.wfile.write(body)
                return
            body = (
                json.dumps(status_payload(final), sort_keys=True) + "\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # keep logs readable
            pass

    HTTPServer((host, port), Handler).serve_forever()
