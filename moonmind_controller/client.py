"""Deployment-owned client for the standalone controller endpoint.

Stdlib-only (``urllib``). The host CLI and the application handoff use this
client to submit or observe the same controller operation through one small
authenticated local endpoint. The bearer secret is deployment-owned (see
:mod:`auth`); it is never looked up from an application service.
"""

from __future__ import annotations

import json
import urllib.request

from moonmind_controller import auth, compose


class ControllerError(RuntimeError):
    """The controller owned the submission; legacy fallback is unsafe."""


class ControllerUnavailableError(ControllerError):
    """No controller writer owns this operation; fallback stays safe."""


class ControllerBusyError(ControllerError):
    """The controller holds an unfinished operation; observe, don't duplicate."""


class ControllerFailedError(ControllerError):
    """The controller refused or failed the operation; inspect before retrying."""

# How long a submission POST may take before the client reattaches through
# status observation instead of assuming failure. The controller performs
# pull (900s) and apply (900s) synchronously in the request thread, so a
# prompt client always outruns a real apply; the threaded server keeps
# serving GET /operation while the POST runs.
SUBMIT_TIMEOUT_SECONDS = 30
STATUS_TIMEOUT_SECONDS = 10
POLL_INTERVAL_SECONDS = 10
# Bounded wait for one terminal record: pull + apply budgets plus margin.
POLL_TIMEOUT_SECONDS = 2100


def controller_base_url(explicit: str | None = None) -> str:
    """Return the controller endpoint base URL (loopback only by default)."""
    import os

    return (
        explicit
        or os.environ.get("MOONMIND_CONTROLLER_URL")
        or "http://127.0.0.1:8099"
    ).rstrip("/")


def build_operation_payload(
    *,
    operation_id: str,
    target_image: str,
    services: tuple[str, ...] | None = None,
    concrete_images: dict | None = None,
    authorization: dict | None = None,
    storage: dict | None = None,
    access_settings: dict | None = None,
    stack: str | None = None,
) -> dict:
    """Build the data handoff submitted to the controller (no app imports).

    ``services`` defaults to the release-owned service set (``api`` plus
    the versioned worker fleet); every name exists in the canonical stack.
    ``stack`` names the target Compose project when the handoff knows it
    (the host entrypoint passes its project); omitted stays omitted and the
    controller falls back to its configured project. Optional
    ``authorization``/``storage``/``access_settings`` (and
    ``concrete_images``) are trusted release data carried as plain data so
    the controller's pre-apply validators are non-vacuous whenever the
    release artifact supplies them. Absent fields stay absent; the
    controller refuses only explicitly empty mappings.
    """
    desired: dict = {
        "targetImage": target_image,
        "services": list(services)
        if services is not None
        else list(compose.RELEASE_OWNED_SERVICES),
    }
    if stack is not None:
        if not str(stack).strip():
            raise ValueError("Controller target stack must not be empty.")
        desired["stack"] = str(stack).strip()
    if concrete_images is not None:
        desired["concreteImages"] = dict(concrete_images)
    if authorization is not None:
        desired["authorization"] = dict(authorization)
    if storage is not None:
        desired["storage"] = dict(storage)
    if access_settings is not None:
        desired["accessSettings"] = dict(access_settings)
    return {
        "operationId": operation_id,
        "desired": desired,
    }


def get_operation(
    *,
    base_url: str | None = None,
    secret: str | None = None,
    timeout: int = STATUS_TIMEOUT_SECONDS,
) -> dict:
    """Observe the controller's current operation record (read-only)."""
    from moonmind_controller import redact

    resolved_secret = secret if secret is not None else auth.secret_from_environ()
    if not resolved_secret:
        raise ValueError("Controller secret is not configured; refusing to submit.")
    url = f"{controller_base_url(base_url)}/operation"
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            **auth.authorization_header(resolved_secret),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode(errors="replace")
    except Exception as exc:
        raise RuntimeError(
            "Controller status is unavailable; the existing installation is unchanged: "
            + redact.redact(str(exc) or type(exc).__name__)[:500]
        ) from exc
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("Controller returned an unreadable response.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Controller returned an unreadable response.")
    return parsed


def wait_for_terminal_operation(
    operation_id: str,
    *,
    base_url: str | None = None,
    secret: str | None = None,
    poll_interval: int = POLL_INTERVAL_SECONDS,
    poll_timeout: int = POLL_TIMEOUT_SECONDS,
) -> dict:
    """Poll the durable record until one terminal outcome (installed/failed).

    Reattaching to the reserved operation never duplicates mutation: the
    controller executes its own record exactly once, and every poll is
    read-only. Raises on failure, on a foreign operation record, or when
    the bounded wait expires (naming the last observed status).
    """
    import time

    deadline = time.monotonic() + max(0, poll_timeout)
    while True:
        observed = get_operation(base_url=base_url, secret=secret)
        if observed.get("operationId") != operation_id:
            raise ControllerBusyError(
                f"Controller holds operation {observed.get('operationId')!r}, "
                f"not {operation_id!r}; refusing to fall back while another "
                "operation may still be applying."
            )
        status = observed.get("status")
        if status == "installed":
            return {"operation": observed, "reconciled": True}
        if status == "failed":
            raise ControllerFailedError(
                f"Controller operation {operation_id!r} failed: "
                f"{observed.get('lastError') or 'see controller status'}"
            )
        if time.monotonic() >= deadline:
            raise ControllerBusyError(
                f"Controller operation {operation_id!r} is still "
                f"{status!r} after {poll_timeout}s; observe it with "
                "controller status instead of repeating the submission."
            )
        time.sleep(max(0, poll_interval))


def submit_operation(
    payload: dict, *,
    base_url: str | None = None,
    secret: str | None = None,
    timeout: int = SUBMIT_TIMEOUT_SECONDS,
    wait_for_terminal: bool = False,
    poll_interval: int = POLL_INTERVAL_SECONDS,
    poll_timeout: int = POLL_TIMEOUT_SECONDS,
) -> dict:
    """Submit an operation to the controller; raise with redacted diagnostics.

    The POST carries only the durable reservation: when it outruns
    ``timeout`` the operation is reconciled through GET /operation before
    anything falls back. A reconciled record for this ``operationId``
    returns (installed) or raises (still applying/failed) — it never drops
    into a legacy fallback while the controller may still be mutating the
    same stack. Only a missing or foreign record re-raises the submission
    error, in which case no controller writer owns this operation. With
    ``wait_for_terminal`` the call reattaches with bounded polling until
    the record reaches ``installed`` or ``failed``.
    """
    import urllib.error

    from moonmind_controller import redact

    resolved_secret = secret if secret is not None else auth.secret_from_environ()
    if not resolved_secret:
        raise ValueError("Controller secret is not configured; refusing to submit.")
    url = f"{controller_base_url(base_url)}/operation"
    body = (json.dumps(payload, sort_keys=True) + "\n").encode()
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            **auth.authorization_header(resolved_secret),
        },
    )
    operation_id = str((payload or {}).get("operationId") or "")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        message = ""
        try:
            detail = json.loads((exc.read() or b"").decode(errors="replace") or "{}")
            if isinstance(detail, dict):
                message = str(detail.get("error") or "")
        except (ValueError, OSError):
            message = ""
        raise ControllerFailedError(
            f"Controller refused the submission (HTTP {exc.code}): "
            + redact.redact(message or str(exc) or type(exc).__name__)[:500]
        ) from exc
    except Exception as exc:
        # Never fall back blindly: reconcile the durable record first.
        try:
            observed = get_operation(base_url=base_url, secret=resolved_secret)
        except Exception:
            raise ControllerUnavailableError(
                "Controller submission failed and its status is unreachable; "
                "the existing installation is unchanged: "
                + redact.redact(str(exc) or type(exc).__name__)[:500]
            ) from exc
        if observed.get("operationId") != operation_id or not operation_id:
            if observed.get("operationId"):
                raise ControllerBusyError(
                    f"Controller holds operation {observed.get('operationId')!r}, "
                    f"not {operation_id!r}; refusing to fall back while another "
                    "operation may still be applying."
                ) from exc
            raise ControllerUnavailableError(
                "Controller submission failed; the existing installation is unchanged: "
                + redact.redact(str(exc) or type(exc).__name__)[:500]
            ) from exc
        if observed.get("status") == "installed":
            return {"operation": observed, "reconciled": True}
        if observed.get("status") == "failed":
            raise ControllerFailedError(
                f"Controller operation {operation_id!r} failed: "
                f"{observed.get('lastError') or 'see controller status'}"
            ) from exc
        if wait_for_terminal:
            return wait_for_terminal_operation(
                operation_id,
                base_url=base_url,
                secret=resolved_secret,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
            )
        raise ControllerBusyError(
            f"Controller operation {operation_id!r} is still applying "
            f"(status {observed.get('status')!r}); observe it with controller "
            "status instead of falling back while it may still be mutating."
        ) from exc
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("Controller returned an unreadable response.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Controller returned an unreadable response.")
    return parsed


def is_controller_configured(secret: str | None = None) -> bool:
    """Whether a deployment-owned secret exists for controller submission."""
    resolved = secret if secret is not None else auth.secret_from_environ()
    return bool(resolved)
