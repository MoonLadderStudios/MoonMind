"""Canonical effective-model resolver for managed runtime task submissions.

Single source of truth for resolving which model should be used for a
managed agent run.  The precedence chain is:

1. Explicit model chosen on the task (``"task_override"``).
2. ``default_model`` on the selected provider profile
   (``"provider_profile_default"``).
3. Runtime default for the managed runtime (``"runtime_default"``).
4. No model resolved (``"none"``).

Usage::

    from moonmind.workflows.tasks.model_resolver import resolve_effective_model

    resolved_model, model_source = resolve_effective_model(
        runtime_id="codex_cli",
        profile=profile_db_row,    # may be None
        requested_model=task_model,  # from the task payload, may be None
    )
"""

from __future__ import annotations

from typing import Any, Mapping

from moonmind.workflows.tasks.runtime_defaults import (
    normalize_runtime_id,
    resolve_runtime_defaults,
)

__all__ = ["resolve_effective_model"]

_MODEL_SOURCE_TASK_OVERRIDE = "task_override"
_MODEL_SOURCE_PROFILE_DEFAULT = "provider_profile_default"
_MODEL_SOURCE_RUNTIME_DEFAULT = "runtime_default"
_MODEL_SOURCE_NONE = "none"


def resolve_effective_model(
    *,
    runtime_id: str | None,
    profile: Any | None,
    requested_model: str | None,
    workflow_settings: Any | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[str | None, str]:
    """Compute the effective model and its source for a managed runtime task.

    Parameters
    ----------
    runtime_id:
        Raw runtime id from the task payload (e.g. ``"codex_cli"`` or alias
        ``"codex"``).  Normalized internally before lookup.
    profile:
        The selected provider profile DB row (``ManagedAgentProviderProfile``),
        or ``None`` when no profile is selected.
    requested_model:
        The model explicitly chosen on the task, or ``None`` when the task
        does not specify one.
    workflow_settings:
        Optional ``WorkflowSettings`` instance for env-override behavior
        (passed through to ``resolve_runtime_defaults``).
    env:
        Optional environment mapping override (defaults to ``os.environ``).

    Returns
    -------
    tuple[str | None, str]
        ``(resolved_model, model_source)`` where ``model_source`` is one of
        ``"task_override"``, ``"provider_profile_default"``,
        ``"runtime_default"``, or ``"none"``.
    """
    # Preserve the raw requested_model exactly for pass-through; only use a
    # clean copy internally to detect blankness. This keeps `codex.model` and
    # `codex.effort` inputs unmodified (Compatibility Policy §codex).
    clean_requested = (str(requested_model or "").strip()) or None

    # 1. Explicit task-level model takes top priority.
    if clean_requested:
        return clean_requested, _MODEL_SOURCE_TASK_OVERRIDE

    # 2. Provider profile default_model.
    if profile is not None:
        profile_model = str(getattr(profile, "default_model", None) or "").strip() or None
        if profile_model:
            return profile_model, _MODEL_SOURCE_PROFILE_DEFAULT

    # 3. Runtime default.  Always normalize (normalize_runtime_id falls back to
    # DEFAULT_TASK_RUNTIME for None/empty), so the runtime-default tier applies
    # even when the caller omits targetRuntime.
    canonical_runtime = normalize_runtime_id(runtime_id)
    runtime_model, _ = resolve_runtime_defaults(
        canonical_runtime,
        workflow_settings=workflow_settings,
        env=env,
    )
    if runtime_model:
        return runtime_model, _MODEL_SOURCE_RUNTIME_DEFAULT

    return None, _MODEL_SOURCE_NONE
