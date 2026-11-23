"""Read a retained security plan for the preset compiler's plan-run expansion."""

from __future__ import annotations

from moonmind.security.security_plan import (
    MAX_PLAN_BYTES,
    SecurityPlan,
    SecurityPlanError,
    parse_security_plan,
    plan_identity,
)
from moonmind.security.security_plan_run import PlanRunSelection
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

#: Producers that retain a validated, sealed ``security-plan/v1``.
RETAINING_PRODUCERS = frozenset({"security.plan", "security.assess"})


async def load_retained_plan(
    session, selection: PlanRunSelection, access
) -> SecurityPlan:
    """The retained plan whose content identity is ``selection.identity``.

    Only a plan retained by the planning or assessment tool qualifies, so an
    unvalidated draft cannot stand in for one. Diagnostics name fields and
    never echo plan content.
    """
    if access is None:
        raise SecurityPlanError(
            [
                "securityPlan: reading a retained plan needs the operator's artifact "
                "access; expand the plan run from Create or the presets API"
            ]
        )
    artifacts = TemporalArtifactService(TemporalArtifactRepository(session))
    try:
        artifact, _, _, policy = await artifacts.get_metadata(
            artifact_id=selection.plan_ref, access=access
        )
        if not policy.raw_access_allowed:
            raise SecurityPlanError(
                ["securityPlan.planRef: the retained plan does not permit reading it"]
            )
        if artifact.size_bytes is not None and artifact.size_bytes > MAX_PLAN_BYTES:
            raise SecurityPlanError(["securityPlan.planRef: exceeds the plan bound"])
        _, raw = await artifacts.read(artifact_id=selection.plan_ref, access=access)
    except TemporalArtifactError as exc:
        raise SecurityPlanError(
            [
                "securityPlan.planRef: the retained plan is missing or unreadable "
                f"({type(exc).__name__})"
            ]
        ) from None
    metadata = artifact.metadata_json or {}
    if (
        metadata.get("producer") not in RETAINING_PRODUCERS
        or metadata.get("subject") != selection.identity
    ):
        raise SecurityPlanError(
            ["securityPlan.planRef: not a retained security plan with this identity"]
        )
    plan = parse_security_plan(raw)
    if plan_identity(plan) != selection.identity:
        raise SecurityPlanError(
            ["securityPlan.identity: differs from the retained plan's content"]
        )
    return plan


__all__ = ["RETAINING_PRODUCERS", "load_retained_plan"]
