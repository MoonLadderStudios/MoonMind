"""One small authenticated local endpoint for the controller (REQ-02).

Stdlib-only (WSGI). A single bearer secret owned by the deployment (read
from a deployment-owned file, never from an application service) guards every
route; without it the endpoint answers 401. No agents receive sockets or
unrestricted controller access.

On startup :func:`converge_on_restart` inspects the local record and Docker
state and converges only unfinished work toward the same target: a lost
result never repeats a completed apply, and an already-running Compose child
is reconciled (left alone) rather than competed with.
"""
from __future__ import annotations

import hmac
import json
import os
from typing import Any, Callable
from urllib.parse import urlsplit

import engine
import lock as lock_mod
import record as record_mod
from redact import redact_mapping

LEGACY_CONTROL_SERVICE = "temporal-worker-deployment-control"
LEGACY_PROBE_TIMEOUT_SECONDS = 30


class LegacyWriterUnknown(RuntimeError):
    """Docker state could not be read, so legacy ownership is unverified."""


def default_legacy_writer_probe() -> bool:
    """Return True when a legacy application-owned writer may still be active.

    Cutover rule (REQ-07): the controller only takes over after the old
    writer is positively stopped or reconciled. Historical legacy logs are
    kept; obsolete controllers are never restarted automatically and a live
    lock inode is never deleted by this probe (it only observes).
    """
    import subprocess

    try:
        completed = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"name={LEGACY_CONTROL_SERVICE}",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=LEGACY_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LegacyWriterUnknown(f"cannot inspect Docker state: {exc}") from exc
    if completed.returncode != 0:
        raise LegacyWriterUnknown(
            f"docker ps failed (exit {completed.returncode})"
        )
    return bool(completed.stdout.strip())


def _read_secret(secret: str | None, secret_file: str | None) -> str:
    if secret:
        return secret
    if secret_file:
        with open(secret_file, encoding="utf-8") as stream:
            value = stream.read().strip()
        if not value:
            raise ValueError(f"Controller secret file is empty: {secret_file}")
        return value
    raise ValueError("Controller secret is required (secret or secret file).")


def _authorized(environ: dict, secret: str) -> bool:
    header = environ.get("HTTP_AUTHORIZATION", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return False
    return hmac.compare_digest(presented.strip(), secret)


def _json_response(start_response, status: str, payload: Any) -> list:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
    return [body]


def _public_operation(operation: dict) -> dict:
    return redact_mapping(operation)


def _compose_child_alive(state_dir: str) -> int | None:
    pidfile = os.path.join(state_dir, "compose-child.pid")
    try:
        with open(pidfile, encoding="utf-8") as stream:
            pid = int(stream.read().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def converge_on_restart(
    store: record_mod.OperationStore,
    applier: Callable[[dict], Any],
    *,
    stack: str | None = None,
    legacy_writer_probe: Callable[[], bool] | None = None,
) -> dict:
    """Resume unfinished work after a controller restart.

    Completed applies (confirmed installation) are never repeated. When a
    Compose child from before the restart is still running, it is reconciled
    by leaving it alone instead of launching a competing writer. When a
    legacy application-owned writer may still be active, unfinished work is
    deferred with an explicit reason instead of being taken over.
    """
    converged: list[str] = []
    deferred: list[dict] = []
    skipped: list[str] = []
    child_pid = _compose_child_alive(str(store.state_dir))
    for operation in store.list_open(stack=stack):
        if operation.get("installed") is not None:
            skipped.append(operation["operationId"])
            continue
        if child_pid is not None:
            deferred.append(
                {"operationId": operation["operationId"], "reason": f"compose child {child_pid} still running"}
            )
            continue
        if legacy_writer_probe is not None:
            try:
                if legacy_writer_probe():
                    deferred.append(
                        {
                            "operationId": operation["operationId"],
                            "reason": (
                                "legacy application-owned writer may still be active; "
                                "stop or reconcile it before cutover"
                            ),
                        }
                    )
                    continue
            except LegacyWriterUnknown as exc:
                deferred.append(
                    {"operationId": operation["operationId"], "reason": str(exc)}
                )
                continue
        applier(operation)
        converged.append(operation["operationId"])
    return {"converged": converged, "deferred": deferred, "skippedInstalled": skipped}


def check_legacy_cutover(
    legacy_writer_probe: Callable[[], bool] | None,
) -> str | None:
    """Return a deferral reason when the legacy writer still owns the stack."""
    if legacy_writer_probe is None:
        return None
    try:
        if legacy_writer_probe():
            return (
                "legacy application-owned writer may still be active; stop or "
                "reconcile it before the controller takes over (REQ-07)"
            )
    except LegacyWriterUnknown as exc:
        return str(exc)
    return None


def build_app(
    *,
    store: record_mod.OperationStore,
    secret: str | None = None,
    secret_file: str | None = None,
    applier: Callable[[dict], Any] | None = None,
    legacy_writer_probe: Callable[[], bool] | None = None,
):
    """Build the WSGI application. The secret is deployment-owned."""
    bearer = _read_secret(secret, secret_file)
    run_apply = applier or (lambda operation: production_apply(store, operation))

    def app(environ, start_response):
        if not _authorized(environ, bearer):
            return _json_response(start_response, "401 Unauthorized", {"error": "unauthorized"})
        method = environ.get("REQUEST_METHOD", "GET")
        path = urlsplit(environ.get("PATH_INFO", "") or "").path.rstrip("/") or "/"
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        raw = environ["wsgi.input"].read(length) if length > 0 else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            return _json_response(start_response, "400 Bad Request", {"error": "invalid JSON body"})
        if not isinstance(body, dict):
            return _json_response(start_response, "400 Bad Request", {"error": "JSON object required"})

        if method == "POST" and path == "/v1/operations":
            return _submit(start_response, body)
        if path.startswith("/v1/operations/"):
            rest = path[len("/v1/operations/") :]
            operation_id, _, action = rest.partition("/")
            if not operation_id or "/" in action:
                return _json_response(start_response, "404 Not Found", {"error": "unknown route"})
            if method == "GET" and action == "":
                return _status(start_response, operation_id)
            if method == "POST" and action == "retry":
                return _retry(start_response, operation_id)
            if method == "GET" and action == "logs":
                return _logs(start_response, operation_id)
            return _json_response(start_response, "404 Not Found", {"error": "unknown route"})
        if method == "GET" and path == "/v1/healthz":
            return _json_response(start_response, "200 OK", {"status": "ok"})
        return _json_response(start_response, "404 Not Found", {"error": "unknown route"})

    def _submit(start_response, body):
        stack = body.get("stack")
        desired_image = body.get("desiredImage")
        source_revision = body.get("sourceRevision", "")
        if not stack or not desired_image:
            return _json_response(
                start_response, "400 Bad Request", {"error": "stack and desiredImage are required"}
            )
        try:
            lock_mod.ensure_no_competing_writer(store.state_dir, stack)
            cutover_block = check_legacy_cutover(legacy_writer_probe)
            if cutover_block is not None:
                return _json_response(start_response, "409 Conflict", {"error": cutover_block})
            operation = store.begin(
                stack=stack,
                desired_image=desired_image,
                source_revision=source_revision,
                reason=body.get("reason", ""),
                target=body.get("target") if isinstance(body.get("target"), dict) else None,
            )
            already_installed = (operation.get("installed") or {}).get("image") == desired_image and operation.get(
                "status"
            ) in ("succeeded", "partially_verified")
            if not already_installed and operation.get("status") in ("pending", "staged", "applying"):
                candidate = lock_mod.StackLock(store.state_dir, stack)
                with candidate.acquire():
                    run_apply(store.load(operation["operationId"]))
                operation = store.load(operation["operationId"])
        except lock_mod.LockBusyError as exc:
            return _json_response(start_response, "409 Conflict", {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - surfaced as a 500 with context
            return _json_response(start_response, "500 Internal Server Error", {"error": f"{type(exc).__name__}: {exc}"})
        return _json_response(start_response, "202 Accepted", _public_operation(operation))

    def _status(start_response, operation_id):
        try:
            return _json_response(start_response, "200 OK", _public_operation(store.load(operation_id)))
        except KeyError:
            return _json_response(start_response, "404 Not Found", {"error": "unknown operation"})

    def _retry(start_response, operation_id):
        try:
            operation = store.begin_retry(operation_id)
        except KeyError:
            return _json_response(start_response, "404 Not Found", {"error": "unknown operation"})
        except RuntimeError as exc:
            return _json_response(start_response, "409 Conflict", {"error": str(exc)})
        try:
            candidate = lock_mod.StackLock(store.state_dir, operation["stack"])
            with candidate.acquire():
                run_apply(store.load(operation_id))
            operation = store.load(operation_id)
        except lock_mod.LockBusyError as exc:
            return _json_response(start_response, "409 Conflict", {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return _json_response(start_response, "500 Internal Server Error", {"error": f"{type(exc).__name__}: {exc}"})
        return _json_response(start_response, "202 Accepted", _public_operation(operation))

    def _logs(start_response, operation_id):
        try:
            operation = store.load(operation_id)
        except KeyError:
            return _json_response(start_response, "404 Not Found", {"error": "unknown operation"})
        logs = {
            "operationId": operation_id,
            "status": operation.get("status"),
            "errorSummary": operation.get("errorSummary", ""),
            "attempts": operation.get("attempts", []),
            "verification": operation.get("verification", []),
            "reportingFailures": operation.get("reportingFailures", []),
        }
        return _json_response(start_response, "200 OK", redact_mapping(logs))

    return app


def write_image_overlay(state_dir: str, operation_id: str, image: str) -> str:
    """Persist the controller-owned image selection as a Compose env overlay.

    Only the controller-owned ``MOONMIND_IMAGE`` field is written. The
    operator's ``.env``, overrides, and infrastructure version selections are
    never modified, so unchanged Postgres/Temporal/MinIO definitions see no
    application-release churn.
    """
    overlay_dir = os.path.join(state_dir, "image-overlays")
    os.makedirs(overlay_dir, exist_ok=True)
    if "/" in operation_id or ".." in operation_id:
        raise ValueError(f"Refusing unsafe operation id: {operation_id!r}")
    path = os.path.join(overlay_dir, f"{operation_id}.env")
    tmp_path = f"{path}.{os.getpid()}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as stream:
        stream.write(f"MOONMIND_IMAGE={image}\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp_path, path)
    return path


def production_apply(store: record_mod.OperationStore, operation: dict) -> dict:
    """Default applier: stage images, apply, verify, and record the result."""
    from engine import pre_apply_checks  # local import keeps module import light

    target = operation.get("target") or {}
    project = target.get("project", operation.get("stack"))
    checks = pre_apply_checks(
        config_valid=bool(target.get("projectDir") and target.get("composeFiles")),
        storage_ok=True,
        access_preserved=True,
        old_service_health={},
    )
    if not checks["admitted"]:
        store.record_attempt_error(operation["operationId"], error="; ".join(checks["problems"]))
        return {"status": "refused", "problems": checks["problems"]}
    store.mark_stage(operation["operationId"], stage="staged")
    runner = engine.subprocess_runner()
    overlay = write_image_overlay(
        str(store.state_dir), operation["operationId"], operation["desired"]["image"]
    )
    try:
        outcome = engine.apply(
            runner,
            project=project,
            project_dir=target.get("projectDir", ""),
            compose_files=tuple(target.get("composeFiles", ("docker-compose.yaml",))),
            services=tuple(target.get("services", ())),
            images=(operation["desired"]["image"],),
            env_file=target.get("envFile") or overlay,
        )
    except engine.StageError as exc:
        store.record_attempt_error(operation["operationId"], error=f"staging failed: {exc} {exc.output}")
        raise
    except engine.ApplyError as exc:
        store.record_attempt_error(operation["operationId"], error=f"apply failed: {exc} {exc.output}")
        raise
    store.confirm_installed(operation["operationId"], image=operation["desired"]["image"])
    return {"status": "succeeded", "outcome": outcome}


def main(argv=None) -> int:
    import argparse
    from wsgiref.simple_server import make_server

    parser = argparse.ArgumentParser(description="Standalone MoonMind deployment controller.")
    parser.add_argument("--state-dir", default=os.environ.get("MOONMIND_CONTROLLER_STATE_DIR", "/var/lib/moonmind-controller"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MOONMIND_CONTROLLER_PORT", "8472")))
    parser.add_argument("--secret-file", default=os.environ.get("MOONMIND_CONTROLLER_SECRET_FILE"))
    parser.add_argument(
        "--no-legacy-probe",
        action="store_true",
        help="Skip the legacy-writer cutover probe (only for tests without Docker).",
    )
    args = parser.parse_args(argv)
    secret_file = args.secret_file or os.path.join(args.state_dir, "secrets", "controller-bearer")
    store = record_mod.OperationStore(args.state_dir)
    probe = None if args.no_legacy_probe else default_legacy_writer_probe
    converge_on_restart(store, lambda operation: production_apply(store, operation), legacy_writer_probe=probe)
    app = build_app(store=store, secret_file=secret_file, legacy_writer_probe=probe)
    httpd = make_server("127.0.0.1", args.port, app)
    print(f"moonmind-controller listening on 127.0.0.1:{args.port}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
