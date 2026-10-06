"""Client for the standalone deployment controller endpoint.

Settings Operations (the API) and the ``deployment.update_compose_stack``
tool submit and observe the same controller operation the host entrypoint
uses (MoonLadderStudios/MoonMind#4502; the controller is #4500). Once a
deployment has installed the controller it is the only update owner: a
timeout, refusal, or outage is reported and never converted into another
updater. The deployment-owned bearer secret is read server side from the
controller state bootstrap writes; it never reaches a browser or an agent.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlencode
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


class DeploymentOperationError(ValueError):
    """Raised when a deployment operation request cannot be served.

    ``status_code`` defaults to a policy rejection; controller outcomes carry
    their own distinct status and non-secret ``details``.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})


CONTROLLER_STATE_DIR_ENV = "MOONMIND_CONTROLLER_STATE_DIR"
CONTROLLER_DEFAULT_STATE_DIR = "deploy/state/controller"
CONTROLLER_HOST_ALIAS = "moonmind-controller"
# The controller applies inside the submission request, so the API waits a
# bounded time and then observes its own operation identity.
CONTROLLER_SUBMIT_TIMEOUT_SECONDS: float = 20
CONTROLLER_STATUS_TIMEOUT_SECONDS: float = 10
CONTROLLER_SUBMIT_ATTEMPTS = 2
CONTROLLER_RECENT_LIMIT = 10
_OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_CONTROLLER_TEXT_CHARS = 2000

# ``UNKNOWN`` is a missing or unrecognized controller status: it is neither
# queued nor finished, so readers keep observing instead of guessing.
ControllerStatus = Literal[
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "PARTIALLY_VERIFIED",
    "FAILED",
    "SUPERSEDED",
    "UNKNOWN",
]
_CONTROLLER_STATUS_MAP: dict[str, ControllerStatus] = {
    "pending": "QUEUED",
    "staged": "RUNNING",
    "applying": "RUNNING",
    "succeeded": "SUCCEEDED",
    "partially_verified": "PARTIALLY_VERIFIED",
    "failed": "FAILED",
    "superseded": "SUPERSEDED",
}


@dataclass(frozen=True)
class ControllerEndpoint:
    """The installed controller as the API reaches it (server side only)."""

    base_url: str
    secret: str | None


class ControllerTransportError(RuntimeError):
    """The controller did not answer (refused, unreachable, or timed out)."""


def controller_state_dir(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(
        str(env.get(CONTROLLER_STATE_DIR_ENV) or "").strip()
        or CONTROLLER_DEFAULT_STATE_DIR
    )


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _controller_secret(env: dict[str, str], state_dir: Path) -> str | None:
    explicit = str(env.get("MOONMIND_CONTROLLER_SECRET") or "").strip()
    if explicit:
        return explicit
    path = Path(
        str(env.get("MOONMIND_CONTROLLER_SECRET_FILE") or "").strip()
        or state_dir / "secrets" / "controller-bearer"
    )
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def resolve_controller_endpoint(
    environ: dict[str, str] | None = None,
) -> ControllerEndpoint | None:
    """Return the installed controller endpoint, or ``None`` when absent.

    Installation is read from the deployment-owned controller state that
    bootstrap writes (mounted read-only into the API): its identity plus a
    verified controller image or recorded operations. An identity left by an
    install that could not pin its image is not an installed controller, the
    same rule the host entrypoint applies. ``MOONMIND_CONTROLLER_URL``
    selects an endpoint explicitly. The default endpoint is the controller's
    alias on the deployment-controller network at its recorded port.
    """
    env = os.environ if environ is None else environ
    state_dir = controller_state_dir(env)
    explicit_url = str(env.get("MOONMIND_CONTROLLER_URL") or "").strip()
    identity = _read_json_object(state_dir / "controller-identity.json")
    if not explicit_url:
        if identity is None:
            return None
        image = _read_json_object(state_dir / "controller-image.json") or {}
        operations = state_dir / "operations"
        recorded = operations.is_dir() and any(operations.glob("*.json"))
        if not (image.get("verified") is True or recorded):
            return None
    base_url = explicit_url
    if not base_url:
        # Only the recorded port is read; the host is the fixed alias on the
        # deployment-controller network, never a value from a state file.
        port = (identity or {}).get("port")
        if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536:
            base_url = f"http://{CONTROLLER_HOST_ALIAS}:{port}"
    return ControllerEndpoint(
        base_url=base_url.rstrip("/"), secret=_controller_secret(env, state_dir)
    )


def _controller_request(
    endpoint: ControllerEndpoint,
    *,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float,
) -> tuple[int, dict[str, Any]]:
    if not endpoint.base_url:
        raise ControllerTransportError(
            "the controller identity records no endpoint port"
        )
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{endpoint.base_url}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {endpoint.secret}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8") or "{}"
            status = response.status
    except urllib.error.HTTPError as exc:
        try:
            parsed = json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
        except (OSError, ValueError):
            parsed = {}
        return exc.code, parsed if isinstance(parsed, dict) else {}
    except urllib.error.URLError as exc:
        raise ControllerTransportError(
            f"controller endpoint is unreachable ({exc.reason})"
        ) from None
    except (OSError, TimeoutError) as exc:
        # The request may have reached the controller; its acknowledgment
        # was lost. Callers observe the operation identity before deciding.
        raise ControllerTransportError(
            f"controller did not answer ({type(exc).__name__})"
        ) from None
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def _controller_error_text(parsed: dict[str, Any]) -> str:
    return str(parsed.get("error") or "no detail")[:_MAX_CONTROLLER_TEXT_CHARS]


def _controller_refusal(
    status: int, parsed: dict[str, Any], *, operation_id: str | None = None
) -> DeploymentOperationError:
    """Map a controller answer onto a truthful, distinct API error."""
    details = {"operationId": operation_id} if operation_id else {}
    if status == 401:
        return DeploymentOperationError(
            "deployment_controller_access_denied",
            "The deployment controller rejected the API's deployment-owned "
            "credential; reinstall or restore the controller from the host.",
            status_code=503,
        )
    if status == 409 and "activeOperationId" in parsed:
        active = parsed.get("activeOperationId")
        return DeploymentOperationError(
            "deployment_controller_busy",
            "Another deployment operation owns this stack; wait for it to "
            "finish before requesting a different target.",
            status_code=409,
            details={"activeOperationId": active} if active else {},
        )
    if status == 409:
        return DeploymentOperationError(
            "deployment_controller_conflict",
            f"The deployment controller refused the request: {_controller_error_text(parsed)}",
            status_code=409,
            details=details,
        )
    if status in (400, 404):
        return DeploymentOperationError(
            "deployment_controller_rejected",
            f"The deployment controller rejected the request: {_controller_error_text(parsed)}",
            status_code=422,
            details=details,
        )
    return DeploymentOperationError(
        "deployment_controller_failed",
        f"The deployment controller answered HTTP {status} without a result.",
        status_code=502,
        details=details,
    )


def unavailable_controller_error(
    reason: str, *, operation_id: str | None = None
) -> DeploymentOperationError:
    message = f"The deployment controller is unavailable: {reason}."
    if operation_id:
        message += (
            f" Operation {operation_id} was not confirmed; it is not "
            "resubmitted automatically. Use the host update command "
            "(./tools/update-moonmind.sh) if the dashboard cannot reach it."
        )
    return DeploymentOperationError(
        "deployment_controller_unavailable",
        message,
        status_code=503,
        details={"operationId": operation_id} if operation_id else {},
    )


def _require_controller_secret(endpoint: ControllerEndpoint) -> None:
    if not endpoint.secret:
        raise DeploymentOperationError(
            "deployment_controller_credential_unavailable",
            "The deployment controller is installed but its deployment-owned "
            "credential is not readable by the API.",
            status_code=503,
        )


def _check_operation_id(operation_id: str) -> str:
    if not _OPERATION_ID_PATTERN.fullmatch(str(operation_id or "")):
        raise DeploymentOperationError(
            "deployment_operation_id_invalid", "Operation id is invalid."
        )
    return operation_id


def observe_controller_operation(
    endpoint: ControllerEndpoint, operation_id: str
) -> dict[str, Any] | None:
    """Read one controller operation; ``None`` when it was never recorded."""
    _check_operation_id(operation_id)
    status, parsed = _controller_request(
        endpoint,
        method="GET",
        path=f"/v1/operations/{operation_id}",
        timeout=CONTROLLER_STATUS_TIMEOUT_SECONDS,
    )
    if status == 200:
        return parsed
    if status == 404:
        return None
    raise _controller_refusal(status, parsed, operation_id=operation_id)


def submit_controller_update(
    endpoint: ControllerEndpoint,
    *,
    operation_id: str,
    stack: str,
    desired_image: str,
    reason: str = "",
) -> dict[str, Any]:
    """Submit an update under the caller's own operation identity.

    The controller applies within the request, so a bounded wait usually
    ends first. A lost acknowledgment is resolved through the identity: the
    API observes its operation and, if the controller never recorded it,
    resubmits the same identity (the controller reattaches instead of
    starting a second writer). An unreachable controller is reported; no
    other updater is started.
    """
    _require_controller_secret(endpoint)
    _check_operation_id(operation_id)
    payload = {
        "operationId": operation_id,
        "stack": stack,
        "desiredImage": desired_image,
        "sourceRevision": "",
        "reason": reason,
    }
    last_error = "no answer"
    for _attempt in range(CONTROLLER_SUBMIT_ATTEMPTS):
        try:
            status, parsed = _controller_request(
                endpoint,
                method="POST",
                path="/v1/operations",
                payload=payload,
                timeout=CONTROLLER_SUBMIT_TIMEOUT_SECONDS,
            )
        except ControllerTransportError as exc:
            last_error = str(exc)
            try:
                observed = observe_controller_operation(endpoint, operation_id)
            except ControllerTransportError as observe_exc:
                last_error = str(observe_exc)
                break
            if observed is not None:
                return observed
            continue
        if status == 202 and parsed.get("operationId"):
            return parsed
        if status >= 500:
            # The controller may have recorded the operation before failing.
            try:
                observed = observe_controller_operation(endpoint, operation_id)
            except ControllerTransportError:
                observed = None
            if observed is not None:
                return observed
        raise _controller_refusal(status, parsed, operation_id=operation_id)
    raise unavailable_controller_error(last_error, operation_id=operation_id)


def retry_controller_operation(
    endpoint: ControllerEndpoint, operation_id: str
) -> dict[str, Any]:
    """Request the controller's fresh bounded attempt for one operation."""
    _require_controller_secret(endpoint)
    _check_operation_id(operation_id)
    try:
        before = observe_controller_operation(endpoint, operation_id)
    except ControllerTransportError as exc:
        raise unavailable_controller_error(str(exc)) from None
    if before is None:
        raise DeploymentOperationError(
            "deployment_operation_not_found",
            "The deployment controller has no such operation.",
            status_code=404,
        )
    group = before.get("attemptGroup", 1)
    try:
        status, parsed = _controller_request(
            endpoint,
            method="POST",
            path=f"/v1/operations/{operation_id}/retry",
            timeout=CONTROLLER_SUBMIT_TIMEOUT_SECONDS,
        )
    except ControllerTransportError as exc:
        try:
            observed = observe_controller_operation(endpoint, operation_id)
        except ControllerTransportError:
            observed = None
        if observed is not None and observed.get("attemptGroup", 1) != group:
            return observed
        raise unavailable_controller_error(str(exc)) from None
    if status == 202 and parsed.get("operationId"):
        return parsed
    raise _controller_refusal(status, parsed, operation_id=operation_id)


def list_controller_operations(
    endpoint: ControllerEndpoint, *, stack: str, limit: int = CONTROLLER_RECENT_LIMIT
) -> list[dict[str, Any]]:
    _require_controller_secret(endpoint)
    status, parsed = _controller_request(
        endpoint,
        method="GET",
        path=f"/v1/operations?{urlencode({'stack': stack, 'limit': int(limit)})}",
        timeout=CONTROLLER_STATUS_TIMEOUT_SECONDS,
    )
    if status != 200:
        raise _controller_refusal(status, parsed)
    operations = parsed.get("operations")
    return (
        [op for op in operations if isinstance(op, dict)]
        if isinstance(operations, list)
        else []
    )


def controller_action_status(controller_status: str) -> ControllerStatus:
    """Map a controller operation status onto the Operations action status."""
    return _CONTROLLER_STATUS_MAP.get(str(controller_status or ""), "UNKNOWN")
