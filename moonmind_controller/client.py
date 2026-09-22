"""Deployment-owned client for the standalone controller endpoint.

Stdlib-only (``urllib``). The host CLI and the application handoff use this
client to submit or observe the same controller operation through one small
authenticated local endpoint. The bearer secret is deployment-owned (see
:mod:`auth`); it is never looked up from an application service.
"""

from __future__ import annotations

import json
import urllib.request

from moonmind_controller import auth


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
    services: tuple[str, ...] = (),
    concrete_images: dict | None = None,
    authorization: dict | None = None,
    storage: dict | None = None,
    access_settings: dict | None = None,
) -> dict:
    """Build the data handoff submitted to the controller (no app imports).

    Optional ``authorization``/``storage``/``access_settings`` (and
    ``concrete_images``) are trusted release data carried as plain data so
    the controller's pre-apply validators are non-vacuous whenever the
    release artifact supplies them. Absent fields stay absent; the
    controller refuses only explicitly empty mappings.
    """
    desired: dict = {
        "targetImage": target_image,
        "services": list(services),
    }
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


def submit_operation(
    payload: dict, *, base_url: str | None = None, secret: str | None = None
) -> dict:
    """Submit an operation to the controller; raise with redacted diagnostics."""
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
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode(errors="replace")
    except Exception as exc:
        raise RuntimeError(
            "Controller submission failed; the existing installation is unchanged: "
            + redact.redact(str(exc) or type(exc).__name__)[:500]
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
