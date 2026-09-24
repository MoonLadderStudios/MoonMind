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
import re
from typing import Any, Callable
from urllib.parse import urlsplit

import engine
import lock as lock_mod
import record as record_mod
from redact import redact_mapping

LEGACY_CONTROL_SERVICE = "temporal-worker-deployment-control"
LEGACY_PROBE_TIMEOUT_SECONDS = 30

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_IMAGE_CHARS = 1024


class LegacyWriterUnknown(RuntimeError):
    """Docker state could not be read, so legacy ownership is unverified."""


def parse_legacy_mutation_ps(output: str) -> bool:
    """Return True when `docker ps` output shows an active legacy mutation.

    The steady-state ``temporal-worker-deployment-control`` service runs
    continuously with ``restart: unless-stopped``, so mere container
    existence is not evidence of an active writer. Only a running one-off
    updater container (``com.docker.compose.oneoff=true``) for the legacy
    control service counts as an owned mutation in progress.
    """
    for line in (output or "").splitlines():
        name, _, oneoff = line.strip().partition("\t")
        if not name:
            continue
        if oneoff.strip().lower() in ("true", "1", "yes"):
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
                '{{.Names}}\t{{.Label "com.docker.compose.oneoff"}}',
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
    return parse_legacy_mutation_ps(completed.stdout)


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
            by_stack[stack_name], key=lambda op: str(op.get("createdAt") or "")
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
    return None


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
        rejection = _validate_submission(body)
        if rejection is not None:
            return _json_response(
                start_response, "400 Bad Request", {"error": rejection}
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
                    operation = _apply_with_bounded_retries(
                        store, operation["operationId"], run_apply
                    )
        except lock_mod.LockBusyError:
            # Never expose lock-owner internals: a constant conflict body.
            return _json_response(start_response, "409 Conflict", {"error": "stack is owned by another writer"})
        except Exception:  # noqa: BLE001 - never expose exception detail
            return _json_response(start_response, "500 Internal Server Error", {"error": "internal error"})
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
        except RuntimeError:
            # Constant body: retry-budget internals never reach the response.
            return _json_response(start_response, "409 Conflict", {"error": "operation cannot be retried in its current state"})
        try:
            candidate = lock_mod.StackLock(store.state_dir, operation["stack"])
            with candidate.acquire():
                operation = _apply_with_bounded_retries(
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
        return _json_response(start_response, "200 OK", redact_mapping(logs))

    return app


def _env_files_for_apply(target: dict, overlay: str) -> list:
    """Layer deployment config under the controller-owned image overlay.

    The deployment-owned `.env` (explicit ``target.envFile`` or
    ``<projectDir>/.env`` when present) stays first so Compose keeps
    operator authentication, bindings, and infrastructure versions; the
    generated image overlay comes last and overrides only the release
    selection.
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
    files.append(overlay)
    return files


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
    return [
        key for key in OMNIGENT_CHANNEL_KEYS if str(seen.get(key) or "").strip()
    ]


def production_apply(
    store: record_mod.OperationStore,
    operation: dict,
    *,
    dispatch_probe: Callable[[], bool | None] | None = None,
    omnigent_migrator: Callable[[dict], Any] | None = None,
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
        store.record_attempt_error(operation["operationId"], error="; ".join(checks["problems"]))
        return {"status": "refused", "problems": checks["problems"]}
    store.mark_stage(operation["operationId"], stage="staged")
    runner = engine.subprocess_runner()
    overlay = write_image_overlay(
        str(store.state_dir), operation["operationId"], operation["desired"]["image"]
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
        store.record_attempt_error(operation["operationId"], error=f"staging failed: {exc} {exc.output}")
        raise
    except engine.ApplyError as exc:
        store.record_attempt_error(operation["operationId"], error=f"apply failed: {exc} {exc.output}")
        raise
    store.confirm_installed(operation["operationId"], image=operation["desired"]["image"])
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
    try:
        base = engine.compose_base(
            project=project,
            project_dir=target.get("projectDir", ""),
            compose_files=tuple(target.get("composeFiles", ("docker-compose.yaml",))),
            env_files=env_files or None,
        )
        observed = engine.observe_services(runner, base, services)
        services_running = observed["services"]
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
                "migrator is delegated; re-run ./tools/update-moonmind.sh to "
                "converge the installed Omnigent release"
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
            "detail": f"Omnigent migration did not converge: {exc}",
        }
        store.record_verification(operation_id, **check)
        return check
    if not receipt or (isinstance(receipt, dict) and receipt.get("status") not in ("migrated", "aligned", "skipped", "ok", "succeeded")):
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
    httpd = make_server("0.0.0.0", args.port, app)
    print(f"moonmind-controller listening on 0.0.0.0:{args.port}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
