"""Resource provenance for admitted operators; machine scopes stay independent."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from api_service.auth_providers import instance_operator_principal
from moonmind.security.operator_admission import OperatorAdmission


def is_instance_operator(access: Any) -> bool:
    """Whether the caller may manage every instance resource."""

    return isinstance(access, OperatorAdmission)


def access_principal(access: Any) -> str:
    """Artifact, audit and execution scope for the admitted caller.

    Admission keeps the retained local-operator UUID string so existing
    account-free output, claims and audit attribution stay continuous.
    """

    if isinstance(access, OperatorAdmission):
        return instance_operator_principal()
    raw = getattr(access, "id", None)
    return str(raw) if raw is not None else ""


def access_user_id(access: Any) -> UUID | None:
    """Optional historical attribution; new operator requests have no person.

    Admission has no person, so new person-attribution columns stay null.
    """

    if isinstance(access, OperatorAdmission):
        return None
    return getattr(access, "id", None)
