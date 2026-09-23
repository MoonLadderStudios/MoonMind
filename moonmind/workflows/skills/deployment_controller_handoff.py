"""Replacement-owner handoff from the application updater to the controller.

The standalone controller in ``moonmind_controller`` is the replacement owner
for deployment mutation (MoonLadderStudios/MoonMind#4500). This module is the
thin data handoff: it extracts release configuration from a selected trusted
artifact as plain data and submits it to the controller's authenticated local
endpoint. It never imports the target application's Python bootstrap to read
that configuration, and it performs no mutation itself.

Cutover rule: the controller path is used when it is configured (deployment-
owned secret present) and reachable. The legacy application-owned path is
retained only until the old writer is positively stopped/reconciled; callers
must keep its historical logs and must not auto-restart obsolete controllers
or delete a live lock inode (see ``moonmind_controller.lock``).
"""

from __future__ import annotations


def build_controller_payload(
    *,
    submission_id: str,
    image: str,
    services: tuple[str, ...] | None = None,
    concrete_images: dict | None = None,
    authorization: dict | None = None,
    storage: dict | None = None,
    access_settings: dict | None = None,
    stack: str | None = None,
) -> dict:
    """Build the controller handoff payload from trusted submission data.

    ``services`` defaults to the release-owned service set (``api`` plus
    the versioned worker fleet from the canonical stack); every name exists
    in ``docker-compose.yaml``. ``stack`` names the target Compose project
    when the submitter knows it. Optional preservation/validation fields
    are trusted release data carried as plain data; absent fields stay
    absent (see ``client``).
    """
    from moonmind_controller import compose as _controller_compose

    resolved_services = (
        list(services)
        if services is not None
        else list(_controller_compose.RELEASE_OWNED_SERVICES)
    )
    desired: dict = {
        "targetImage": image,
        "services": resolved_services,
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
        "operationId": f"host-update:{submission_id}",
        "desired": desired,
    }


def controller_available() -> bool:
    """Whether the replacement-owner controller endpoint can be attempted."""
    try:
        from moonmind_controller import client
    except ImportError:
        return False
    return client.is_controller_configured()


def submit_to_controller(payload: dict, *, wait_for_terminal: bool = True) -> dict:
    """Submit trusted release data to the controller (lazy import, no app boot).

    Waits for the durable record to reach a terminal outcome by default so
    callers never treat an in-flight submission as complete and never fork
    a legacy fallback while the controller may still be applying.
    """
    from moonmind_controller import client

    return client.submit_operation(payload, wait_for_terminal=wait_for_terminal)
