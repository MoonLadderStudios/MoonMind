from collections.abc import Mapping
from typing import Literal

from moonmind.config.settings import settings

TaskTarget = Literal["temporal", "orchestrator", "queue"]


# Accepted truthy string forms: 1, true, yes, on
# Accepted falsy string forms:  0, false, no, off
_TRUTHY_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSY_STRINGS = frozenset({"0", "false", "no", "off"})


def _coerce_bool(value: object, *, default: bool) -> bool:
    """Normalize bool-like request values with fallback to ``default``.

    Raises ``ValueError`` for non-None values that cannot be coerced,
    enforcing fail-fast semantics for unsupported runtime inputs.
    """

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in _TRUTHY_STRINGS:
        return True
    if lowered in _FALSY_STRINGS:
        return False
    raise ValueError(
        f"Unsupported boolean value: {value!r}; "
        f"expected true/false, yes/no, on/off, 1/0, or omit for default"
    )


def _task_requests_proposals(task_payload: Mapping[str, object] | None) -> bool:
    """Return whether a task submission requests post-run proposal generation."""

    default_enabled = bool(settings.workflow.enable_task_proposals)
    if not isinstance(task_payload, Mapping):
        return default_enabled
    task_node = task_payload.get("task")
    task = task_node if isinstance(task_node, Mapping) else {}
    return _coerce_bool(task.get("proposeTasks"), default=default_enabled)


def get_routing_target_for_task(
    *,
    is_manifest: bool = False,
    is_run: bool = False,
    task_payload: Mapping[str, object] | None = None,
) -> TaskTarget:
    """Determine the deterministic backend execution target for a task."""
    if not settings.temporal_dashboard.submit_enabled:
        return "queue"

    if is_manifest:
        return "temporal"
    if is_run:
        # Temporal proposal generation is still stubbed. Route proposal-requested
        # runs to the queue worker path so Mission Control proposals are emitted.
        if _task_requests_proposals(task_payload):
            return "queue"
        return "temporal"

    return "queue"
