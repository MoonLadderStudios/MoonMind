"""Read routes' ``get_instance_access`` result without resolving a person.

``get_instance_access`` returns the retained predecessor ``User`` until guarded
conversion publishes, and an ``OperatorAdmission`` afterwards. Routes use these
helpers instead of reading ``User`` attributes so admitted requests never need a
person row (MoonLadderStudios/MoonMind#4354).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from api_service.auth_providers import instance_operator_principal
from moonmind.security.operator_admission import OperatorAdmission


def is_instance_operator(access: Any) -> bool:
    """Whether the caller may manage every instance resource."""

    return isinstance(access, OperatorAdmission) or bool(
        getattr(access, "is_superuser", False)
    )


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
    """Retained ``user.id`` for columns that still reference a person row.

    Admission has no person, so person foreign keys stay null.
    """

    if isinstance(access, OperatorAdmission):
        return None
    return getattr(access, "id", None)
