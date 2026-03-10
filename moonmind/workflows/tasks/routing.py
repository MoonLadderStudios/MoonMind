from typing import Literal

from moonmind.config.settings import settings

TaskTarget = Literal["temporal", "orchestrator", "queue"]

def get_routing_target_for_task(
    *,
    is_manifest: bool = False,
    is_run: bool = False,
) -> TaskTarget:
    """Determine the deterministic backend execution target for a task."""
    if not settings.temporal_dashboard.submit_enabled:
        return "queue"

    # When submit is enabled, run and manifest workflows prefer Temporal.
    if is_manifest or is_run:
        return "temporal"

    return "queue"
