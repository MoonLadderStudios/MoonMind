from typing import Literal

from moonmind.config.settings import settings

TaskTarget = Literal["temporal"]


class TemporalSubmitDisabledError(RuntimeError):
    """Raised when Temporal task submission is disabled via configuration.

    API routers should catch this and return HTTP 503 Service Unavailable
    with a structured error body rather than allowing it to surface as a 500.
    """


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


def get_routing_target_for_task(
    *,
    is_manifest: bool = False,
    is_run: bool = False,
    task_payload: object | None = None,
) -> TaskTarget:
    """Determine the deterministic backend execution target for a task.

    All tasks now route to Temporal. The legacy queue and orchestrator
    execution substrates are deprecated and no longer supported.

    ``task_payload`` is accepted for API stability; run routing does not branch on it.
    Proposal generation (``task.proposeTasks``) is handled inside Temporal workflows.
    """
    if not settings.temporal_dashboard.submit_enabled:
        raise TemporalSubmitDisabledError(
            "Temporal task submission is disabled "
            "(temporal_dashboard.submit_enabled=False). "
            "The legacy queue execution substrate is no longer supported. "
            "Enable Temporal submission to proceed."
        )

    return "temporal"
