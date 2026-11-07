"""Explicit access expectations for identities in an assessed target system.

This contract describes test identities and resources, never MoonMind users or
roles. Credential references identify existing admitted workload slots; the
matrix itself grants neither credentials nor network authority.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from moonmind.security.assessments import Contract, Evidence, Text

IdentityRef = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
]


class TargetIdentity(Contract):
    id: IdentityRef
    account_ref: IdentityRef
    session_ref: IdentityRef
    integration_ref: IdentityRef
    credential_ref: Text = Field(pattern=r"^[A-Za-z][A-Za-z0-9+.-]*://\S+$")


class AccessCase(Contract):
    id: IdentityRef
    identity_ref: IdentityRef
    account_ref: IdentityRef
    session_ref: IdentityRef
    integration_ref: IdentityRef
    action: IdentityRef
    expected: Literal["allow", "deny"]


class AuthorizationMatrix(Contract):
    identities: list[TargetIdentity] = Field(min_length=1, max_length=64)
    cases: list[AccessCase] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def _unambiguous(self):
        ids = {i.id for i in self.identities}
        if len(ids) != len(self.identities) or len({c.id for c in self.cases}) != len(
            self.cases
        ):
            raise ValueError("target identities and access cases must be unique")
        credentials = [i.credential_ref for i in self.identities]
        if len(set(credentials)) != len(credentials):
            raise ValueError("a credential cannot identify multiple target sessions")
        expectations = set()
        for case in self.cases:
            if case.identity_ref not in ids:
                raise ValueError("access case references an unknown identity")
            key = (
                case.identity_ref,
                case.account_ref,
                case.session_ref,
                case.integration_ref,
                case.action,
            )
            if key in expectations:
                raise ValueError("duplicate or contradictory target access case")
            expectations.add(key)
        return self

    def case(self, ref: str) -> AccessCase:
        return next(c for c in self.cases if c.id == ref)

    def identity(self, ref: str) -> TargetIdentity:
        return next(i for i in self.identities if i.id == ref)


class AccessObservation(Contract):
    operation: IdentityRef
    case_ref: IdentityRef
    observed: Literal["allow", "deny", "unknown"]
    evidence_refs: Evidence
