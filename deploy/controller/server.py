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

import contextlib
import hmac
import json
import os
import re
import socketserver
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit
from wsgiref.simple_server import WSGIServer, make_server

import engine
import lock as lock_mod
import record as record_mod
from redact import redact_mapping, redact_text

LEGACY_CONTROL_SERVICE = "temporal-worker-deployment-control"
LEGACY_PROBE_TIMEOUT_SECONDS = 30
OMNIGENT_OPERATION_LABEL = "moonmind.controller.omnigent.operation"
OMNIGENT_RESULT_PREFIX = "MOONMIND_OMNIGENT_RESULT="
# The deployment checkout bootstrap mounts read-only at its host path.
TARGET_REPO_ENV = "MOONMIND_CONTROLLER_TARGET_REPO"
# The MoonMind Compose project bootstrap recorded in the controller identity
# (--target-project, COMPOSE_PROJECT_NAME, or the checkout name).
TARGET_PROJECT_ENV = "MOONMIND_CONTROLLER_TARGET_PROJECT"
TARGET_CONFIG_TIMEOUT_SECONDS = 120
# The Docker transport substrate is never recreated through an update, the
# same exclusion the host entrypoint applies to its explicit target.
DEFAULT_EXCLUDED_SERVICES = ("docker-proxy", "sandbox-egress-proxy")

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_IMAGE_CHARS = 1024
DEFAULT_LIST_LIMIT = 10
MAX_LIST_LIMIT = 50


class ThreadingWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
    """Serve status and log reads while a submission applies.

    A submission holds its request thread for the whole apply, so a
    single-threaded server would leave every observer (and a client that
    lost its acknowledgment) blocked until the apply ends.
    """

    daemon_threads = True


def make_http_server(host: str, port: int, app) -> WSGIServer:
    """Build the production HTTP server for the controller endpoint."""
    return make_server(host, port, app, server_class=ThreadingWSGIServer)


class LegacyWriterUnknown(RuntimeError):
    """Docker state could not be read, so legacy ownership is unverified."""


def parse_legacy_mutation_ps(
    output: str, *, controller_operations: dict[str, str] | None = None
) -> bool:
    """Return True when `docker ps` output shows an active legacy mutation.

    The steady-state ``temporal-worker-deployment-control`` service runs
    continuously with ``restart: unless-stopped``, so mere container
    existence is not evidence of an active writer. Only a running one-off
    updater container (``com.docker.compose.oneoff=true``) for the legacy
    control service counts as an owned mutation in progress. A controller
    helper is excluded only with its recorded operation and target project.
    """
    for line in (output or "").splitlines():
        fields = line.strip().split("\t")
        name, oneoff = (fields + ["", ""])[:2]
        if not name or oneoff.strip().lower() not in ("true", "1", "yes"):
            continue
        if len(fields) >= 4:
            project, operation_id = fields[2:4]
            if (
                operation_id
                and (controller_operations or {}).get(operation_id) == project
            ):
                continue
        return True
    return False


def default_legacy_writer_probe() -> bool:
    """Return True when a legacy application-owned mutation is in progress.

    Cutover rule (REQ-07): the controller only takes over after the old
    writer is positively stopped or reconciled. The probe lists running
    containers carrying the legacy control-service label and reports an
    active writer only for one-off updater containers, never for the
    always-on service container. Historical legacy logs are kept; obsolete
    controllers are never restarted automatically and a live lock inode is
    never deleted by this probe (it only observes).
    """
    import subprocess

    try:
        completed = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.service={LEGACY_CONTROL_SERVICE}",
                "--format",
                '{{.Names}}\t{{.Label "com.docker.compose.oneoff"}}'
                '\t{{.Label "com.docker.compose.project"}}'
                f'\t{{{{.Label "{OMNIGENT_OPERATION_LABEL}"}}}}',
            ],
            capture_output=True,
            text=True,
            timeout=LEGACY_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LegacyWriterUnknown(f"cannot inspect Docker state: {exc}") from exc
    if completed.returncode != 0:
        raise LegacyWriterUnknown(f"docker ps failed (exit {completed.returncode})")
    store = record_mod.OperationStore(
        os.environ.get("MOONMIND_CONTROLLER_STATE_DIR", "/var/lib/moonmind-controller")
    )
    # A label from an old operation is not authority. Only a current
    # release step protected by the controller's kernel lock is excluded;
    # orphaned, failed and superseded one-offs remain competing writers.
    operations = {
        operation["operationId"]: str(
            (operation.get("target") or {}).get("project") or ""
        )
        for operation in (*store.list_open(), *store.list_terminal())
        if operation.get("omnigentStep") in ("select", "migrate")
        and operation.get("status") in (*record_mod.OPEN_STATUSES, "succeeded")
        and lock_mod.StackLock(store.state_dir, operation["stack"]).probe()
    }
    return parse_legacy_mutation_ps(completed.stdout, controller_operations=operations)


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


def _redact_strings(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_strings(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _public_operation(operation: dict) -> dict:
    """Redact sensitive keys and credential material inside free-form text."""
    return _redact_strings(redact_mapping(operation))


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

    Stale open targets are reconciled before anything runs: only the newest
    open operation per stack survives, and an open operation older than a
    confirmed installation for the same stack is superseded instead of
    replayed, so a restart can never recreate a stale image over a newer
    confirmed one.
    """
    converged: list[str] = []
    deferred: list[dict] = []
    skipped: list[str] = []
    superseded: list[str] = []
    survivors = _reconcile_open_operations(store, stack=stack)
    superseded.extend(
        operation["operationId"]
        for operation in survivors["superseded"]
    )
    child_pid = _compose_child_alive(str(store.state_dir))
    for operation in survivors["open"]:
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
    return {
        "converged": converged,
        "deferred": deferred,
        "skippedInstalled": skipped,
        "superseded": superseded,
    }


def _reconcile_open_operations(
    store: record_mod.OperationStore, *, stack: str | None = None
) -> dict:
    """Supersede stale open operations; return surviving opens + superseded."""
    opens = store.list_open(stack=stack)
    by_stack: dict[str, list[dict]] = {}
    for operation in opens:
        by_stack.setdefault(str(operation.get("stack")), []).append(operation)
    confirmed: dict[str, dict] = {}
    for terminal in store.list_terminal(stack=stack):
        installed = terminal.get("installed") or {}
        if not installed.get("image"):
            continue
        confirmed[str(terminal.get("stack"))] = terminal
    surviving: list[dict] = []
    superseded: list[dict] = []
    for stack_name in sorted(by_stack):
        candidates = sorted(
            by_stack[stack_name],
            key=lambda op: (
                str(op.get("createdAt") or ""),
                store.record_mtime_ns(op["operationId"]),
            ),
        )
        # Only the newest open operation per stack keeps restart intent;
        # older opens are obsolete even before comparing with installations.
        for operation in candidates[:-1]:
            superseded.append(
                store.supersede(
                    operation["operationId"],
                    reason=(
                        "superseded by newer open operation "
                        f"{candidates[-1]['operationId']} for stack "
                        f"{stack_name!r}; restart recovery never replays "
                        "stale open targets"
                    ),
                )
            )
        newest = candidates[-1]
        terminal = confirmed.get(stack_name)
        if terminal is not None and newest.get("installed") is None:
            installed_image = (terminal.get("installed") or {}).get("image")
            if (
                newest.get("desired", {}).get("image") != installed_image
                and str(newest.get("createdAt") or "")
                < str(
                    (terminal.get("installed") or {}).get("confirmedAt") or ""
                )
            ):
                superseded.append(
                    store.supersede(
                        newest["operationId"],
                        reason=(
                            "superseded by confirmed installation "
                            f"{installed_image!r} for stack {stack_name!r}; "
                            "restart recovery never recreates a stale image "
                            "over confirmed intent"
                        ),
                    )
                )
                continue
        surviving.append(store.load(newest["operationId"]))
    return {"open": surviving, "superseded": superseded}


def check_legacy_cutover(
    legacy_writer_probe: Callable[[], bool] | None,
) -> str | None:
    """Return a deferral reason when the legacy writer still owns the stack.

    The reason is a constant string: Docker inspection failures never reach
    the HTTP response, so error detail cannot leak through this 409 body.
    """
    if legacy_writer_probe is None:
        return None
    try:
        if legacy_writer_probe():
            return (
                "legacy application-owned writer may still be active; stop or "
                "reconcile it before the controller takes over (REQ-07)"
            )
    except LegacyWriterUnknown:
        return (
            "legacy writer ownership is unverified; reconcile Docker state "
            "before the controller takes over (REQ-07)"
        )
    return None


def _validate_submission(body: dict) -> str | None:
    """Reject privileged-endpoint submissions outside the safe shape.

    The controller holds a direct Docker socket, so caller-supplied Compose
    targets are validated before persistence: names are restricted to a safe
    alphabet, compose files must be relative basenames inside the project
    directory, and the desired image must be a bounded single token.
    Deployment-owned image/policy allowlists remain the operator's
    configuration; this check keeps the endpoint from accepting
    path-escaping or malformed targets.
    """
    target = body.get("target") if isinstance(body.get("target"), dict) else {}
    project = target.get("project", body.get("stack"))
    if not _SAFE_NAME_RE.match(str(project or "")):
        return f"refusing unsafe project name: {project!r}"
    project_dir = target.get("projectDir", "")
    if project_dir and not os.path.isdir(str(project_dir)):
        return f"refusing unknown project directory: {project_dir!r}"
    for compose_file in target.get("composeFiles", ()) or ():
        name = str(compose_file or "")
        parts = name.split("/")
        if (
            not name
            or name.startswith("/")
            or ".." in parts
            or not all(_SAFE_NAME_RE.match(part) for part in parts)
        ):
            return f"refusing unsafe compose file: {compose_file!r}"
    for service in target.get("services", ()) or ():
        if not _SAFE_NAME_RE.match(str(service or "")):
            return f"refusing unsafe service name: {service!r}"
    image = body.get("desiredImage") or ""
    if (
        not isinstance(image, str)
        or not image
        or len(image) > _MAX_IMAGE_CHARS
        or any(char.isspace() for char in image)
    ):
        return "desiredImage must be a single bounded image reference"
    operation_id = body.get("operationId")
    if operation_id is not None:
        try:
            record_mod.check_operation_id(operation_id)
        except ValueError:
            return "operationId must be a bounded safe identifier"
    return None


def _compose_files_for_repo(repo: str, env: dict) -> list:
    """Resolve the deployment-owned Compose file set inside the checkout."""
    selection = str(env.get("COMPOSE_FILE") or "").strip()
    if selection:
        files = [part.strip() for part in re.split(r"[;:]", selection) if part.strip()]
    else:
        files = ["docker-compose.yaml"]
        for name in ("docker-compose.override.yaml", "docker-compose.override.yml"):
            if os.path.isfile(os.path.join(repo, name)):
                files.append(name)
                break
    for name in files:
        parts = name.split("/")
        if (
            name.startswith("/")
            or ".." in parts
            or not all(_SAFE_NAME_RE.match(part) for part in parts)
            or not os.path.isfile(os.path.join(repo, name))
        ):
            raise ValueError(f"unusable compose file in the deployment checkout: {name!r}")
    return files


def default_target(
    stack: str, *, repo: str, recorded_project: str = "", runner=None
) -> dict:
    """Derive the Compose target from the mounted deployment checkout.

    Callers without host knowledge (Settings Operations) submit only the
    stack and image; the controller reads what the deployment already
    determines: the Compose project bootstrap recorded for the deployment
    (else ``COMPOSE_PROJECT_NAME``), ``COMPOSE_FILE`` from the
    deployment-owned ``.env`` (else ``docker-compose.yaml`` plus its
    override), and the services Compose renders for that selection, minus
    the Docker transport substrate.
    """
    if not repo or not os.path.isdir(repo):
        raise ValueError("no deployment checkout is mounted for the controller")
    env_path = os.path.join(repo, ".env")
    env = read_env_file(env_path)
    files = _compose_files_for_repo(repo, env)
    project = str(
        recorded_project or env.get("COMPOSE_PROJECT_NAME") or stack
    ).strip()
    if not _SAFE_NAME_RE.match(project):
        raise ValueError(f"unsafe Compose project name: {project!r}")
    env_files = [env_path] if os.path.isfile(env_path) else None
    base = engine.compose_base(
        project=project,
        project_dir=repo,
        compose_files=files,
        env_files=env_files,
    )
    try:
        result = engine.run_command(
            runner or engine.subprocess_runner(),
            (*base, "config", "--services"),
            timeout_seconds=TARGET_CONFIG_TIMEOUT_SECONDS,
        )
    except engine.CommandError as exc:
        raise ValueError(
            f"compose config failed (exit {exc.exit_status}): "
            + redact_text(str(exc.output or ""))[-500:]
        ) from None
    except OSError as exc:
        raise ValueError(f"compose is unavailable to the controller: {exc}") from None
    if int(result.get("exit", 0)) != 0:
        raise ValueError(
            "compose config failed: "
            + redact_text(str(result.get("output", "")))[-500:]
        )
    services = [
        name
        for name in str(result.get("output", "")).split()
        if name not in DEFAULT_EXCLUDED_SERVICES
    ]
    if not services:
        raise ValueError("the deployment checkout renders no services to update")
    target = {
        "project": project,
        "projectDir": repo,
        "composeFiles": files,
        "services": services,
    }
    if env_files:
        target["envFile"] = env_path
    return target


def production_target_resolver(stack: str) -> dict:
    return default_target(
        stack,
        repo=os.environ.get(TARGET_REPO_ENV, ""),
        recorded_project=os.environ.get(TARGET_PROJECT_ENV, ""),
    )


def _apply_with_bounded_retries(
    store: record_mod.OperationStore,
    operation_id: str,
    run_apply: Callable[[dict], Any],
) -> dict:
    """Run the apply loop until success or the bounded attempt budget ends.

    Staging/apply failures are recorded per attempt by the applier. The
    controller itself drives retries up to ``MAX_AUTO_ATTEMPTS`` per attempt
    group so a transient first failure reaches a terminal ``failed`` (or a
    later ``succeeded``) instead of staying indefinitely open after one
    recorded error. Unexpected exceptions are never retried here.
    """
    attempts = 0
    while True:
        operation = store.load(operation_id)
        if operation.get("status") == "failed" or operation.get(
            "autoAttemptsExhausted"
        ):
            return operation
        try:
            run_apply(operation)
            return store.load(operation_id)
        except (engine.StageError, engine.ApplyError):
            attempts += 1
            operation = store.load(operation_id)
            if (
                operation.get("status") == "failed"
                or attempts >= record_mod.MAX_AUTO_ATTEMPTS
            ):
                raise


def _apply_recording_failure(
    store: record_mod.OperationStore,
    operation_id: str,
    run_apply: Callable[[dict], Any],
) -> dict:
    """Apply within the bounded budget; a recorded failure is a result.

    Exhausted staging/apply attempts leave a terminal ``failed`` record with
    every attempt error, so the caller receives that operation (not an
    internal error) and can offer an explicit Retry. Unexpected exceptions
    still propagate.
    """
    try:
        return _apply_with_bounded_retries(store, operation_id, run_apply)
    except (engine.StageError, engine.ApplyError):
        operation = store.load(operation_id)
        if operation.get("status") != "failed":
            raise
        return operation


def build_app(
    *,
    store: record_mod.OperationStore,
    secret: str | None = None,
    secret_file: str | None = None,
    applier: Callable[[dict], Any] | None = None,
    legacy_writer_probe: Callable[[], bool] | None = None,
    target_resolver: Callable[[str], dict] | None = None,
):
    """Build the WSGI application. The secret is deployment-owned.

    ``target_resolver`` derives the Compose target for a submission that
    names none (see :func:`default_target`).
    """
    bearer = _read_secret(secret, secret_file)
    run_apply = applier or (lambda operation: production_apply(store, operation))
    submission_guard = threading.Lock()

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
        if method == "GET" and path == "/v1/operations":
            return _list(start_response, environ.get("QUERY_STRING", ""))
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
        rejection = _validate_submission(body)
        if rejection is not None:
            return _json_response(
                start_response, "400 Bad Request", {"error": rejection}
            )
        requested_id = body.get("operationId")
        try:
            with contextlib.ExitStack() as held:
                # Decide ownership under one guard so two concurrent
                # submissions cannot both start an operation for the stack.
                with submission_guard:
                    decision = _submission_decision(
                        stack, desired_image, requested_id
                    )
                    if decision is not None:
                        status_line, payload = decision
                        return _json_response(start_response, status_line, payload)
                    cutover_block = check_legacy_cutover(legacy_writer_probe)
                    if cutover_block is not None:
                        return _json_response(start_response, "409 Conflict", {"error": cutover_block})
                    target = body.get("target") if isinstance(body.get("target"), dict) else None
                    reattachable = any(
                        (op.get("desired") or {}).get("image") == desired_image
                        for op in store.list_open(stack=stack)
                    ) or store.find_completed(stack=stack, desired_image=desired_image)
                    if target is None and target_resolver is not None and not reattachable:
                        try:
                            target = target_resolver(stack)
                        except ValueError as exc:
                            return _json_response(
                                start_response,
                                "400 Bad Request",
                                {"error": f"deployment target could not be derived: {redact_text(str(exc))}"},
                            )
                    operation = store.begin(
                        stack=stack,
                        desired_image=desired_image,
                        source_revision=source_revision,
                        reason=body.get("reason", ""),
                        target=target,
                        operation_id=requested_id,
                    )
                    needs_apply = operation.get("status") in record_mod.OPEN_STATUSES
                    if needs_apply:
                        held.enter_context(
                            lock_mod.StackLock(store.state_dir, stack).acquire()
                        )
                if needs_apply:
                    operation = _apply_recording_failure(
                        store, operation["operationId"], run_apply
                    )
        except lock_mod.LockBusyError:
            # Never expose lock-owner internals: a constant conflict body.
            return _json_response(start_response, "409 Conflict", {"error": "stack is owned by another writer"})
        except Exception:  # noqa: BLE001 - never expose exception detail
            return _json_response(start_response, "500 Internal Server Error", {"error": "internal error"})
        return _json_response(start_response, "202 Accepted", _public_operation(operation))

    def _submission_decision(stack, desired_image, requested_id):
        """Reattach or refuse without starting another writer.

        Returns ``None`` when the submission may start (or resume) its
        operation, otherwise the ``(status line, body)`` to answer with. A
        caller's own identity always reattaches to its record; a duplicate
        for the target the stack is already applying reattaches to that
        operation; a changed target is refused while another writer owns
        the stack, naming the operation that owns it.
        """
        if requested_id is not None:
            try:
                existing = store.load(requested_id)
            except KeyError:
                existing = None
            if existing is not None:
                if existing.get("stack") != stack or (
                    existing.get("desired") or {}
                ).get("image") != desired_image:
                    return "409 Conflict", {"error": "operation id names a different request"}
                if existing.get("status") not in record_mod.OPEN_STATUSES:
                    return "202 Accepted", _public_operation(existing)
        busy = lock_mod.StackLock(store.state_dir, stack).probe()
        if not busy:
            # Nobody is applying: a recorded open operation resumes.
            return None
        opens = store.list_open(stack=stack)
        for operation in opens:
            if (operation.get("desired") or {}).get("image") == desired_image:
                return "202 Accepted", _public_operation(operation)
        active = max(
            opens,
            key=lambda op: (str(op.get("createdAt") or ""), op["operationId"]),
            default=None,
        )
        return "409 Conflict", {
            "error": "stack is owned by another writer",
            "activeOperationId": active["operationId"] if active else None,
        }

    def _list(start_response, query_string):
        query = parse_qs(query_string or "")
        stack = (query.get("stack") or [""])[0] or None
        try:
            limit = int((query.get("limit") or [DEFAULT_LIST_LIMIT])[0])
        except ValueError:
            return _json_response(start_response, "400 Bad Request", {"error": "limit must be an integer"})
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        operations = [*store.list_open(stack=stack), *store.list_terminal(stack=stack)]
        operations.sort(
            key=lambda op: (
                str(op.get("createdAt") or ""),
                store.record_mtime_ns(op["operationId"]),
            ),
            reverse=True,
        )
        return _json_response(
            start_response,
            "200 OK",
            {"operations": [_public_operation(op) for op in operations[:limit]]},
        )

    def _status(start_response, operation_id):
        try:
            return _json_response(start_response, "200 OK", _public_operation(store.load(operation_id)))
        except KeyError:
            return _json_response(start_response, "404 Not Found", {"error": "unknown operation"})

    def _retry(start_response, operation_id):
        try:
            with contextlib.ExitStack() as held:
                with submission_guard:
                    try:
                        operation = store.load(operation_id)
                    except KeyError:
                        return _json_response(start_response, "404 Not Found", {"error": "unknown operation"})
                    # Hold the stack before touching the record, so a Retry
                    # can never rewrite an operation another writer applies.
                    held.enter_context(
                        lock_mod.StackLock(store.state_dir, operation["stack"]).acquire()
                    )
                    try:
                        store.begin_retry(operation_id)
                    except RuntimeError:
                        # Constant body: refusal reasons (status, budget) never reach the response.
                        return _json_response(start_response, "409 Conflict", {"error": "operation cannot be retried in its current state"})
                operation = _apply_recording_failure(
                    store, operation_id, run_apply
                )
        except lock_mod.LockBusyError:
            return _json_response(start_response, "409 Conflict", {"error": "stack is owned by another writer"})
        except Exception:  # noqa: BLE001 - never expose exception detail
            return _json_response(start_response, "500 Internal Server Error", {"error": "internal error"})
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
        return _json_response(start_response, "200 OK", _public_operation(logs))

    return app


def _env_files_for_apply(target: dict, overlay: str) -> list:
    """Layer deployment config under the controller-owned image overlay.

    The deployment-owned `.env` (explicit ``target.envFile`` or
    ``<projectDir>/.env`` when present) stays first so Compose keeps
    operator authentication, bindings, and infrastructure versions; the
    release-owned ``.env.deploy`` follows it, and the generated image
    overlay comes last and overrides only the MoonMind release selection.
    """
    files: list[str] = []
    explicit = (target or {}).get("envFile")
    if explicit:
        files.append(str(explicit))
    else:
        project_dir = (target or {}).get("projectDir", "")
        candidate = os.path.join(str(project_dir), ".env") if project_dir else ""
        if candidate and os.path.isfile(candidate):
            files.append(candidate)
    release_env = _release_env_for_target(target)
    if release_env and os.path.isfile(release_env):
        files.append(release_env)
    files.append(overlay)
    return list(dict.fromkeys(files))


def write_image_overlay(state_dir: str, operation_id: str, image: str) -> str:
    """Persist the controller-owned image selection as a Compose env overlay.

    Only the controller-owned ``MOONMIND_IMAGE`` field is written. The
    operator's ``.env``, overrides, and infrastructure version selections are
    never modified, so unchanged Postgres/Temporal/MinIO definitions see no
    application-release churn.
    """
    overlay_dir = os.path.join(state_dir, "image-overlays")
    os.makedirs(overlay_dir, exist_ok=True)
    record_mod.check_operation_id(operation_id)
    path = os.path.join(overlay_dir, f"{operation_id}.env")
    tmp_path = f"{path}.{os.getpid()}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as stream:
        stream.write(f"MOONMIND_IMAGE={image}\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp_path, path)
    return path


OPERATOR_URL_TIMEOUT_SECONDS = 30

# Deployment env keys whose non-empty presence means the installation
# configures an Omnigent release channel that an ordinary MoonMind update
# must also converge (mirrors the legacy `migrate_omnigent` success path).
OMNIGENT_CHANNEL_KEYS = ("OMNIGENT_IMAGE", "OMNIGENT_IMAGE_TAG")


def check_operator_url(url: str, *, timeout_seconds: int = OPERATOR_URL_TIMEOUT_SECONDS) -> str:
    """Probe an operator origin's health endpoint; return "ok" or raise."""
    import urllib.request

    target = url.rstrip("/") + "/healthz"
    request = urllib.request.Request(target, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = response.getcode()
    except Exception as exc:
        raise RuntimeError(
            f"Operator URL {url} failed its health check ({exc}); "
            "deployment update did not verify"
        ) from exc
    if status != 200:
        raise RuntimeError(
            f"Operator URL {url} returned HTTP {status} from /healthz; "
            "deployment update did not verify"
        )
    return "ok"


def read_env_file(path: str) -> dict:
    """Parse a dotenv file into a mapping without interpolation."""
    values: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as stream:
            content = stream.read()
    except OSError:
        return values
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip().strip("'\"").strip()
        values[key] = value
    return values


def _release_env_for_target(target: dict) -> str:
    """Map the deployment service's desired-state mount to the checkout."""
    project_dir = str((target or {}).get("projectDir") or "")
    if not project_dir:
        return ""
    operator_env = str(target.get("envFile") or os.path.join(project_dir, ".env"))
    configured = (
        read_env_file(operator_env).get("MOONMIND_DEPLOYMENT_DESIRED_STATE_ENV_FILE")
        or "/workspace/deployment_state/.env.deploy"
    )
    for mounted, local in (
        ("/workspace/deployment_state/", os.path.join(project_dir, "deploy", "state")),
        ("/workspace/host_project/", project_dir),
    ):
        if configured.startswith(mounted):
            return os.path.join(local, configured[len(mounted) :])
    return (
        configured
        if os.path.isabs(configured)
        else os.path.join(project_dir, configured)
    )


def omnigent_channels_for_target(target: dict) -> list:
    """Return the configured Omnigent channel keys for a deployment target."""
    explicit = (target or {}).get("envFile")
    candidates = [str(explicit)] if explicit else []
    project_dir = (target or {}).get("projectDir", "")
    if project_dir:
        candidates.append(os.path.join(str(project_dir), ".env"))
    seen: dict[str, str] = {}
    for candidate in candidates:
        for key, value in read_env_file(candidate).items():
            seen.setdefault(key, value)
    channels = [
        key for key in OMNIGENT_CHANNEL_KEYS if str(seen.get(key) or "").strip()
    ]
    # Compose supplies the documented defaults even with no .env. A rendered
    # Omnigent service therefore requires the same release convergence.
    if not channels and "omnigent" in (target.get("services") or ()):
        return list(OMNIGENT_CHANNEL_KEYS)
    return channels


def run_omnigent_step(
    store: record_mod.OperationStore,
    operation: dict,
    *,
    runner,
    overlay: str,
    phase: str,
    selected_revision: int | None = None,
) -> dict:
    """Delegate one release step to the selected image, never a full updater.

    The controller retains stack ownership while the existing app-side
    release helpers resolve images or converge catalog/policy state. A
    one-off runs without dependencies and cannot enter the legacy updater.
    """
    target = operation.get("target") or {}
    operation_id = record_mod.check_operation_id(operation["operationId"])
    project = target.get("project", operation.get("stack"))
    # A timed-out compose client can leave its one-off alive. Observe that
    # exact operation/project before any retry launches another process.
    active = engine.run_command(
        runner,
        (
            "docker",
            "ps",
            "--filter",
            f"label={OMNIGENT_OPERATION_LABEL}={operation_id}",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.ID}}",
        ),
        timeout_seconds=LEGACY_PROBE_TIMEOUT_SECONDS,
    )
    if int(active.get("exit", 0)) != 0 or str(active.get("output") or "").strip():
        raise LegacyWriterUnknown(
            "Omnigent release step is still running or its ownership is unverified"
        )
    helper_config = os.path.join(
        str(store.state_dir), "image-overlays", f"{operation_id}-omnigent.json"
    )
    record_mod._atomic_write_json(
        Path(helper_config),
        {
            "services": {
                LEGACY_CONTROL_SERVICE: {
                    "image": operation["desired"]["image"],
                    "user": "0:0",
                    "environment": {
                        "DOCKER_HOST": "unix:///var/run/docker.sock",
                        "SYSTEM_DOCKER_HOST": "unix:///var/run/docker.sock",
                    },
                    "volumes": ["/var/run/docker.sock:/var/run/docker.sock:ro"],
                }
            }
        },
    )
    base = engine.compose_base(
        project=target.get("project", operation.get("stack")),
        project_dir=target.get("projectDir", ""),
        compose_files=(
            *target.get("composeFiles", ("docker-compose.yaml",)),
            helper_config,
        ),
        env_files=_env_files_for_apply(target, overlay),
    )
    request = {
        "operationId": operation_id,
        "moonmindImage": operation["desired"]["image"],
        "target": target,
    }
    if selected_revision is not None:
        request["selectedRevision"] = selected_revision
    store.record_omnigent_step(operation_id, phase)
    try:
        result = engine.run_command(
            runner,
            (
                *base,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--pull",
                "always" if phase == "select" else "never",
                "--label",
                f"{OMNIGENT_OPERATION_LABEL}={operation_id}",
                "--entrypoint",
                "python",
                LEGACY_CONTROL_SERVICE,
                "-m",
                "moonmind.workflows.skills.deployment_release",
                f"--omnigent-{phase}",
                json.dumps(request, sort_keys=True),
            ),
            timeout_seconds=engine.MAX_COMMAND_TIMEOUT_SECONDS,
        )
    finally:
        store.record_omnigent_step(operation_id, None)
    output = str(result.get("output") or "")
    if int(result.get("exit", 0)) != 0:
        raise engine.CommandError(
            f"omnigent-{phase}", int(result.get("exit", 1)), redact_text(output)[-2000:]
        )
    for line in reversed(output.splitlines()):
        if line.startswith(OMNIGENT_RESULT_PREFIX):
            receipt = json.loads(line[len(OMNIGENT_RESULT_PREFIX) :])
            if isinstance(receipt, dict):
                return receipt
    raise ValueError(f"Omnigent {phase} returned no release receipt")


def production_apply(
    store: record_mod.OperationStore,
    operation: dict,
    *,
    dispatch_probe: Callable[[], bool | None] | None = None,
    omnigent_migrator: Callable[[dict], Any] | None = None,
    omnigent_selector: Callable[[dict], Any] | None = None,
) -> dict:
    """Default applier: stage images, apply, verify, and record the result."""
    target = operation.get("target") or {}
    project = target.get("project", operation.get("stack"))
    checks = engine.pre_apply_checks(
        config_valid=bool(target.get("projectDir") and target.get("composeFiles")),
        storage_ok=True,
        access_preserved=True,
        old_service_health={},
    )
    if not checks["admitted"]:
        store.record_attempt_error(
            operation["operationId"], error="; ".join(checks["problems"])
        )
        return {"status": "refused", "problems": checks["problems"]}
    store.mark_stage(operation["operationId"], stage="staged")
    runner = engine.subprocess_runner()
    overlay = write_image_overlay(
        str(store.state_dir), operation["operationId"], operation["desired"]["image"]
    )
    channels = omnigent_channels_for_target(target)
    selection = store.load(operation["operationId"]).get("omnigentSelection") or {}
    if channels and not selection:
        try:
            selection = (
                omnigent_selector(operation)
                if omnigent_selector is not None
                else run_omnigent_step(
                    store, operation, runner=runner, overlay=overlay, phase="select"
                )
            )
            if not isinstance(selection, dict) or selection.get("status") == "failed":
                raise ValueError(f"invalid Omnigent selection receipt: {selection!r}")
            if selection.get("status") != "skipped" and not isinstance(
                selection.get("revision"), int
            ):
                raise ValueError("Omnigent selection did not record a revision")
            store.record_omnigent_selection(operation["operationId"], selection)
        except Exception as exc:
            detail = f"Omnigent selection failed: {exc} {getattr(exc, 'output', '')}"
            store.record_attempt_error(
                operation["operationId"], error=redact_text(detail)
            )
            raise engine.StageError("omnigent-select", 1, redact_text(detail)) from exc
    if channels and omnigent_migrator is None:

        def omnigent_migrator(summary):
            return run_omnigent_step(
                store,
                operation,
                runner=runner,
                overlay=overlay,
                phase="migrate",
                selected_revision=selection.get("revision"),
            )

    env_files = _env_files_for_apply(target, overlay)
    try:
        outcome = engine.apply(
            runner,
            project=project,
            project_dir=target.get("projectDir", ""),
            compose_files=tuple(target.get("composeFiles", ("docker-compose.yaml",))),
            services=tuple(target.get("services", ())),
            images=(operation["desired"]["image"],),
            env_files=env_files,
        )
    except engine.StageError as exc:
        store.record_attempt_error(
            operation["operationId"], error=f"staging failed: {exc} {exc.output}"
        )
        raise
    except engine.ApplyError as exc:
        store.record_attempt_error(
            operation["operationId"], error=f"apply failed: {exc} {exc.output}"
        )
        raise
    store.confirm_installed(
        operation["operationId"], image=operation["desired"]["image"]
    )
    verification = _verify_applied_release(
        store,
        operation,
        runner=runner,
        project=project,
        env_files=env_files,
        dispatch_probe=dispatch_probe,
        omnigent_migrator=omnigent_migrator,
    )
    if verification["complete"]:
        return {"status": "succeeded", "outcome": outcome, "verification": verification}
    return {
        "status": "partially_verified",
        "outcome": outcome,
        "verification": verification,
    }


def _verify_applied_release(
    store: record_mod.OperationStore,
    operation: dict,
    *,
    runner,
    project: str,
    env_files: list,
    dispatch_probe: Callable[[], bool | None] | None = None,
    omnigent_migrator: Callable[[dict], Any] | None = None,
) -> dict:
    """Run post-apply verification and record every check before returning.

    Service state, ordinary dispatch (when observable), and operator access
    are checked through :func:`engine.post_apply_checks`; the installed
    Omnigent release is converged through the delegated migrator when the
    deployment configures Omnigent channels. Each check is recorded on the
    operation, so failed or unavailable mandatory checks downgrade the
    terminal status to ``partially_verified`` instead of silent success.
    """
    target = operation.get("target") or {}
    services = tuple(target.get("services", ()))
    operation_id = operation["operationId"]
    completed_services: list = []
    try:
        base = engine.compose_base(
            project=project,
            project_dir=target.get("projectDir", ""),
            compose_files=tuple(target.get("composeFiles", ("docker-compose.yaml",))),
            env_files=env_files or None,
        )
        observed = engine.observe_services(runner, base, services)
        services_running = observed["services"]
        completed_services = list(observed.get("completed") or ())
    except Exception as exc:
        store.record_verification(
            operation_id,
            name="service-observation",
            status="unavailable",
            detail=f"could not observe Compose services: {exc}",
        )
        services_running = {}
    operator_access: dict[str, str] = {}
    for url in target.get("operatorUrls", ()) or ():
        try:
            operator_access[str(url)] = check_operator_url(str(url))
        except Exception as exc:
            operator_access[str(url)] = str(exc)
    dispatch_ok: bool | None = None
    dispatch_note = "not observable from the controller (no application handle)"
    if dispatch_probe is not None:
        try:
            dispatch_ok = dispatch_probe()
            dispatch_note = (
                "ordinary dispatch works"
                if dispatch_ok
                else "ordinary dispatch probe failed"
            )
        except Exception as exc:
            dispatch_ok = False
            dispatch_note = f"ordinary dispatch probe errored: {exc}"
    result = engine.post_apply_checks(
        services_running=services_running,
        dispatch_ok=dispatch_ok,
        operator_access=operator_access or None,
        completed_services=completed_services,
    )
    recorded = list(result["checks"])
    if dispatch_probe is None:
        # Ordinary dispatch is not observable from the controller without an
        # application handle; the limitation is noted in the outcome instead
        # of failing every update with an unavailable mandatory check.
        recorded = [c for c in recorded if c["name"] != "dispatch"]
    for check in recorded:
        store.record_verification(
            operation_id,
            name=check["name"],
            status=check["status"],
            detail=check["detail"],
        )
    omnigent = _verify_omnigent_release(
        store, operation, omnigent_migrator=omnigent_migrator
    )
    recorded.append(omnigent)
    failed = [c for c in recorded if c["status"] != "passed"]
    return {
        "checks": recorded,
        "complete": not failed,
        "dispatchNote": dispatch_note,
        "controllerUsable": True,
    }


def _verify_omnigent_release(
    store: record_mod.OperationStore,
    operation: dict,
    *,
    omnigent_migrator: Callable[[dict], Any] | None = None,
) -> dict:
    """Converge the installed Omnigent release or record an explicit gap.

    When the deployment configures Omnigent channels, an ordinary MoonMind
    update must also advance the singular Omnigent server/host release. The
    controller delegates that migration when a migrator is wired; otherwise
    the gap stays explicit (``partially_verified``) with a convergence
    pointer instead of silent success.
    """
    target = operation.get("target") or {}
    operation_id = operation["operationId"]
    channels = omnigent_channels_for_target(target)
    if not channels:
        check = {
            "name": "omnigent-migration",
            "status": "passed",
            "detail": "no Omnigent channels configured; nothing to migrate",
        }
        store.record_verification(operation_id, **check)
        return check
    if omnigent_migrator is None:
        check = {
            "name": "omnigent-migration",
            "status": "unavailable",
            "detail": (
                f"Omnigent channels configured ({', '.join(channels)}) but no "
                "migrator is delegated; the controller release-step capability "
                "must be restored before this update can verify"
            ),
        }
        store.record_verification(operation_id, **check)
        return check
    try:
        receipt = omnigent_migrator(
            {
                "operationId": operation_id,
                "channels": channels,
                "moonmindImage": (operation.get("desired") or {}).get("image", ""),
                "target": target,
            }
        )
    except Exception as exc:
        check = {
            "name": "omnigent-migration",
            "status": "failed",
            "detail": redact_text(
                f"Omnigent migration did not converge: {exc} {getattr(exc, 'output', '')}"
            ),
        }
        store.record_verification(operation_id, **check)
        return check
    if not receipt or (
        isinstance(receipt, dict)
        and receipt.get("status")
        not in ("migrated", "converged", "aligned", "skipped", "ok", "succeeded")
    ):
        check = {
            "name": "omnigent-migration",
            "status": "failed",
            "detail": f"Omnigent migration did not converge: {receipt!r}",
        }
        store.record_verification(operation_id, **check)
        return check
    check = {
        "name": "omnigent-migration",
        "status": "passed",
        "detail": f"Omnigent channels converged ({', '.join(channels)}): {receipt!r}",
    }
    store.record_verification(operation_id, **check)
    return check


def main(argv=None) -> int:
    import argparse

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
    app = build_app(
        store=store,
        secret_file=secret_file,
        legacy_writer_probe=probe,
        target_resolver=production_target_resolver,
    )
    httpd = make_http_server("0.0.0.0", args.port, app)
    print(f"moonmind-controller listening on 0.0.0.0:{args.port}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
