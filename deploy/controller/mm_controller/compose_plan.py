"""Semantic Compose plan for the controller-owned changed-service path.

Normal execution is exactly::

    pull --policy always   (stage every image needed for the update first)
    up -d --pull never --no-build --remove-orphans --wait   (changed services)

``down``, force-recreate, and volume/image pruning are never part of the
automatic path; they are explicit repair operations the operator requests.
Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Infrastructure services whose image/config selections must not churn on an
# ordinary application update unless an infrastructure change is intentional.
INFRASTRUCTURE_SERVICES = frozenset({"postgres", "temporal", "minio", "init-db"})

# One-shot gating services stay in the Compose lifecycle; the controller never
# runs a second migration runner outside Compose.
ONE_SHOT_SERVICES = frozenset({"init-db"})

PULL_TIMEOUT_SECONDS = 600
UP_TIMEOUT_SECONDS = 600

ALLOWED_MODES = frozenset({"changed_services", "force_recreate", "down"})


@dataclass(frozen=True, slots=True)
class ComposePlan:
    pull_command: tuple[str, ...]
    up_command: tuple[str, ...]
    mode: str = "changed_services"
    pull_timeout_seconds: int = PULL_TIMEOUT_SECONDS
    up_timeout_seconds: int = UP_TIMEOUT_SECONDS


def build_plan(
    *,
    services: tuple[str, ...] = (),
    mode: str = "changed_services",
) -> ComposePlan:
    """Build the semantic pull-then-up plan.

    ``services`` lists the changed services to recreate; empty means Compose
    resolves the changed set from the rendered project (never ``down``).
    ``mode="down"`` and ``mode="force_recreate"`` are explicit repair
    operations only and must be requested with ``explicit_repair=True``.
    """
    if mode not in ALLOWED_MODES:
        raise ValueError(f"unsupported controller mode {mode!r}")
    pull_command = (
        "docker",
        "compose",
        "pull",
        "--policy",
        "always",
        *services,
    )
    up_command = (
        "docker",
        "compose",
        "up",
        "-d",
        "--pull",
        "never",
        "--no-build",
        "--remove-orphans",
        "--wait",
        *services,
    )
    return ComposePlan(
        pull_command=pull_command,
        up_command=up_command,
        mode=mode,
    )


def build_explicit_repair(*, operation: str) -> tuple[str, ...]:
    """Build an explicit repair command. Never used for automatic escalation."""
    if operation == "down":
        return ("docker", "compose", "down")
    if operation == "force_recreate":
        return (
            "docker",
            "compose",
            "up",
            "-d",
            "--pull",
            "never",
            "--no-build",
            "--force-recreate",
            "--remove-orphans",
            "--wait",
        )
    raise ValueError(f"unsupported explicit repair {operation!r}")


@dataclass(frozen=True, slots=True)
class PlanRequest:
    target_image: str
    services: tuple[str, ...] = ()
    mode: str = "changed_services"
    explicit_repair: bool = False
    # When True the request intentionally changes infrastructure
    # (postgres/temporal/minio) image or config selections.
    infrastructure_change: bool = False


def validate_request(request: PlanRequest) -> list[str]:
    """Return a list of rejection reasons; empty means the request is admissible."""
    errors: list[str] = []
    if not request.target_image.strip():
        errors.append("target_image is required")
    if request.mode not in ALLOWED_MODES:
        errors.append(f"unsupported mode {request.mode!r}")
    if request.mode in ("down", "force_recreate") and not request.explicit_repair:
        errors.append(
            f"mode {request.mode!r} is an explicit repair operation only; "
            "set explicit_repair=True to request it deliberately"
        )
    touched_infra = INFRASTRUCTURE_SERVICES.intersection(set(request.services))
    if touched_infra and not request.infrastructure_change:
        errors.append(
            "services include infrastructure "
            f"{sorted(touched_infra)} without infrastructure_change=True; "
            "ordinary application updates must not churn infra definitions"
        )
    return errors


def changed_services_for_target(
    *,
    configured_services: tuple[str, ...],
    image_services: tuple[str, ...],
    infrastructure_change: bool = False,
) -> tuple[str, ...]:
    """Resolve the changed-service set, excluding untouched infrastructure.

    Services whose image is the requested application image are recreated;
    infrastructure services are included only for intentional infra changes.
    """
    selected = [s for s in configured_services if s in set(image_services)]
    if not infrastructure_change:
        selected = [s for s in selected if s not in INFRASTRUCTURE_SERVICES]
    return tuple(selected)
