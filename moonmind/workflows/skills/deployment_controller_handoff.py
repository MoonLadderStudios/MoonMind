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


def build_controller_payload(*, submission_id: str, image: str) -> dict:
    """Build the controller handoff payload from trusted submission data."""
    return {
        "operationId": f"host-update:{submission_id}",
        "desired": {
            "targetImage": image,
            "services": ["api", "worker"],
        },
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
