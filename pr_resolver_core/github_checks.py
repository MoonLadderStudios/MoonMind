"""Provider-independent reconciliation of GitHub check requirements and statuses."""

from collections.abc import Mapping, Sequence
from typing import Any


def required_check_contexts(
    branch: Any, protection: Any, rules: Any
) -> list[str] | None:
    """Unknown policy is distinct from an observed unprotected branch."""
    if isinstance(branch, Mapping) and branch.get("protected") is False:
        return []
    if not isinstance(protection, Mapping) or not isinstance(rules, list):
        return None
    required = protection.get("required_status_checks") or {}
    if not isinstance(required, Mapping):
        return None
    contexts = required.get("contexts") or []
    checks = required.get("checks") or []
    if not isinstance(contexts, list) or not isinstance(checks, list):
        return None
    names = {str(value).strip() for value in contexts if str(value).strip()}
    names.update(
        str(check.get("context") or "").strip()
        for check in checks
        if isinstance(check, Mapping)
    )
    for rule in rules:
        if (
            not isinstance(rule, Mapping)
            or rule.get("type") != "required_status_checks"
        ):
            continue
        parameters = rule.get("parameters")
        if not isinstance(parameters, Mapping) or not isinstance(
            parameters.get("required_status_checks"), list
        ):
            return None
        names.update(
            str(check.get("context") or "").strip()
            for check in parameters["required_status_checks"]
            if isinstance(check, Mapping)
        )
    return sorted(names - {""})


def partition_commit_statuses(
    statuses: Sequence[Mapping[str, Any]],
    required_contexts: list[str] | None,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Retain latest statuses, gating every context when policy is unavailable.

    GitHub returns newest status observations first. Actions check-runs are
    always evaluated separately; an empty requirement list never disables CI.
    """
    latest = {}
    for status in statuses:
        context = str(status.get("context") or "").strip()
        if context:
            latest.setdefault(context, status)
    gating, advisory = [], []
    for context, status in latest.items():
        (
            gating
            if required_contexts is None or context in required_contexts
            else advisory
        ).append(status)
    return gating, advisory
