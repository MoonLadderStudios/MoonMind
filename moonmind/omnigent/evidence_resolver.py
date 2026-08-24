"""Resolve execution evidence according to the configured evidence policy."""

from __future__ import annotations

from typing import Any, Literal

from moonmind.omnigent.settings import omnigent_evidence_policy


def resolve_execution_evidence(
    plan_payload: Any,
    *,
    policy: str | None = None,
    now=None,
) -> tuple[dict[str, Any], Literal["supported", "deployment_qualified"]]:
    """Resolve evidence for a plan according to policy.

    Returns (evidence_dict, support_tier).
    Raises ValueError if no admissible evidence is found.
    """
    selected_policy = (policy or omnigent_evidence_policy()).lower()
    # Try protected first if policy is protected or either
    if selected_policy in {"protected", "either"}:
        try:
            from moonmind.omnigent.execution_support_evidence import (
                load_protected_execution_support_evidence,
            )

            evidence = load_protected_execution_support_evidence(
                plan_payload, now=now
            )
            return evidence, "supported"
        except Exception:
            if selected_policy == "protected":
                raise
            # fall through to deployment for either
    if selected_policy in {"deployment", "either"}:
        try:
            from moonmind.omnigent.deployment_evidence import load_deployment_evidence

            evidence = load_deployment_evidence(plan_payload, now=now)
            return evidence, "deployment_qualified"
        except Exception as exc:
            # If policy is either and protected already failed, bubble deployment failure
            raise ValueError(
                "no admissible execution evidence for the current policy"
            ) from exc
    raise ValueError(f"unknown evidence policy: {selected_policy}")


def evidence_policy_allows_deployment(*, policy: str | None = None) -> bool:
    p = (policy or omnigent_evidence_policy()).lower()
    return p in {"deployment", "either"}


def evidence_policy_requires_protected(*, policy: str | None = None) -> bool:
    p = (policy or omnigent_evidence_policy()).lower()
    return p == "protected"


__all__ = [
    "resolve_execution_evidence",
    "evidence_policy_allows_deployment",
    "evidence_policy_requires_protected",
]
