"""Project an admitted execution request + plan into canonical turn authority.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane 7/11]).

Every Omnigent execution realizer -- Codex, the generic host, and any future
harness -- describes the immutable authority its turns must preserve the same
way: from the recorded execution plan (#3706) plus the repository, branch, and
publication authority carried by the admitted request. The projection is
deliberately harness-neutral so the canonical turn service never grows a
Codex-versus-OpenCode lifecycle branch.
"""

from __future__ import annotations

from typing import Any

from moonmind.omnigent.control_plane.turn_admission import ImmutableTurnAuthority
from moonmind.omnigent.workspace_intent import authored_starting_branch


def _authored_repository(request: Any) -> str | None:
    parameters = getattr(request, "parameters", None) or {}
    repository = parameters.get("repository")
    if isinstance(repository, str):
        value = repository.strip()
        return value or None
    if isinstance(repository, dict):
        for key in ("fullName", "name", "url"):
            value = str(repository.get(key) or "").strip()
            if value:
                return value
    return None


def canonical_turn_authority(
    request: Any,
    plan: Any,
    *,
    runtime_binding_ref: str | None = None,
    provider_profile_id: str | None = None,
    provider_profile_generation: int | None = None,
) -> ImmutableTurnAuthority:
    """Return the immutable authority a same-session turn must preserve."""

    parameters = getattr(request, "parameters", None) or {}
    publish_mode = str(parameters.get("publishMode") or "none").strip().lower()
    return ImmutableTurnAuthority.from_execution_plan(
        plan,
        runtime_binding_ref=runtime_binding_ref,
        repository=_authored_repository(request),
        repository_branch=authored_starting_branch(request),
        publish_mode=publish_mode or None,
        provider_profile_id=provider_profile_id,
        provider_profile_generation=provider_profile_generation,
    )


__all__ = ["canonical_turn_authority"]
