"""Explicit admission fixtures for callers of the required artifact read API.

This translates the identities used by older fixtures into trusted in-process
admission objects. Production never decodes a principal this way. Tests of an
untrusted payload must use the real registered boundary instead.
"""

from uuid import UUID

from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.temporal.artifacts import ArtifactAccessContext


def access_for_test(principal, *, admitted_principal=None, recovery_principal=None):
    def operator(value):
        if value == "operator":
            return True
        try:
            UUID(str(value))
            return True
        except ValueError:
            return False

    if operator(principal) or operator(admitted_principal):
        return ArtifactAccessContext.for_operator(
            OperatorAdmission(via="artifact-test-fixture"),
            recovery=recovery_principal == "operator",
        )
    scope = admitted_principal or principal
    if str(scope).startswith("workflow:"):
        return ArtifactAccessContext.for_execution(
            str(scope).removeprefix("workflow:"),
            principal=(
                principal
                if principal and not str(principal).startswith("workflow:")
                else "service:test-artifacts"
            ),
        )
    return ArtifactAccessContext.for_service(principal)
