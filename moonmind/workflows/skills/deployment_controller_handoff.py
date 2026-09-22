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
    services: tuple[str, ...] = ("api", "worker"),
    concrete_images: dict | None = None,
    authorization: dict | None = None,
    storage: dict | None = None,
    access_settings: dict | None = None,
) -> dict:
    """Build the controller handoff payload from trusted submission data.

    Optional preservation/validation fields are trusted release data carried
    as plain data; absent fields stay absent (see ``client``).
    """
    desired: dict = {
        "targetImage": image,
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


def submit_to_controller(payload: dict) -> dict:
    """Submit trusted release data to the controller (lazy import, no app boot)."""
    from moonmind_controller import client

    return client.submit_operation(payload)
