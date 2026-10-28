"""Scoped workload credentials for authorized Container Jobs.

Admission matches each requested profile credential slot to a grant in the
session's admitted assessment scope and records a value-free
:class:`WorkloadCredentialAuthorization` bound to the job. The worker checks
that decision again against the current profile and the job, resolves the
stored secret (pinned to the granted revision), and, for cloud inventory,
exchanges it for a short-lived read-only session. Values exist only in the
worker and in job-owned delivery files; they never enter requests, workflow
history, argv, evidence or model-visible results.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Protocol

from moonmind.security.workload_profiles import AdmittedAssessmentScope, _profile

#: Session policies attached to every issued audit session. A session's
#: permissions are the intersection of the role's and these, so a broader role
#: still yields read-only inspection.
READ_ONLY_POLICY_ARNS = (
    "arn:aws:iam::aws:policy/SecurityAudit",
    "arn:aws:iam::aws:policy/job-function/ViewOnlyAccess",
)
#: STS's minimum session length; a grant that cannot cover it has expired.
MIN_SESSION_SECONDS = 900


def owner_principal(owner) -> str:
    return f"{owner.principal_type}:{owner.principal_id}"


def authorize_workload_credentials(
    spec,
    scope: AdmittedAssessmentScope | None,
    profiles,
    *,
    principal: str,
    now: datetime,
) -> list[dict[str, Any]]:
    """Admission decision for each requested slot, without its job binding.

    Raises ``PermissionError`` for any reference the scope does not admit for
    that purpose, account, profile and the whole job budget. Environment
    ``secretRef`` entries are accepted only as a profile's rendered slots.
    """

    selection = spec.workload_profile
    requested = list(selection.credentials or ()) if selection else []
    rendered = {item.credential_ref for item in requested}
    if any(
        item.secret_ref is not None and item.secret_ref not in rendered
        for item in spec.environment
    ):
        raise PermissionError(
            "environment secretRef requires an admitted workload credential slot"
        )
    if not requested:
        return []
    if scope is None:
        raise PermissionError("workload credentials require an admitted assessment")
    profile = _profile(profiles, selection.profile_ref)
    decisions = []
    for item in requested:
        slot = profile.credential(item.slot)
        grants = [g for g in scope.grants if g.ref == item.credential_ref]
        if not grants:
            raise PermissionError(
                "credential reference is not admitted for this assessment"
            )
        grants = [g for g in grants if g.purpose == slot.purpose]
        if not grants:
            raise PermissionError(
                "credential reference is not admitted for this purpose"
            )
        grants = [g for g in grants if g.account == item.account]
        if not grants:
            raise PermissionError(
                "credential reference is not admitted for this account"
            )
        grants = [g for g in grants if selection.profile_ref in g.profile_refs]
        if not grants:
            raise PermissionError(
                "credential reference is not admitted for this profile"
            )
        deadline = now + timedelta(seconds=spec.timeout_seconds)
        live = [g for g in grants if g.expires_at > now]
        if not live:
            raise PermissionError("credential grant has expired")
        covering = [g for g in live if g.expires_at >= deadline]
        if not covering:
            raise PermissionError("credential grant expires before the job budget ends")
        grant = covering[0]
        decisions.append(
            {
                "principal": principal,
                "assessmentRef": scope.assessment_ref,
                "profileRef": selection.profile_ref,
                "slot": item.slot,
                "purpose": slot.purpose,
                "credentialRef": item.credential_ref,
                "account": item.account,
                "expiresAt": grant.expires_at,
                "credentialRevision": grant.revision,
                "roleArn": grant.role_arn,
                "maxSessionSeconds": grant.max_session_seconds,
                "accessLevel": grant.access_level,
            }
        )
    return decisions


def launch_authorizations(request, profiles, *, now: datetime):
    """Worker recheck; returns ``(slot, authorization)`` pairs to deliver.

    Raises ``PermissionError`` when a decision is missing, belongs to another
    job, principal, assessment or profile, or no longer matches the current
    profile, and ``TimeoutError`` once the grant has expired.
    """

    spec = request.request.spec
    selection = spec.workload_profile
    requested = list(selection.credentials or ()) if selection else []
    decided = list(request.workload_credentials or ())
    if any(item.secret_ref is not None for item in spec.environment) and not requested:
        raise PermissionError("environment secretRef has no admitted credential slot")
    if not requested:
        if decided:
            raise PermissionError("workload credential decisions do not match the job")
        return []
    profile = _profile(profiles, selection.profile_ref)
    by_slot = {item.slot: item for item in decided}
    if len(by_slot) != len(decided) or set(by_slot) != {i.slot for i in requested}:
        raise PermissionError("workload credential decisions do not match the job")
    pairs = []
    for item in requested:
        decision = by_slot[item.slot]
        slot = profile.credential(item.slot)
        if (
            decision.job_id != request.job_id
            or decision.principal != owner_principal(request.owner)
            or decision.assessment_ref != selection.assessment_ref
            or decision.profile_ref != selection.profile_ref
            or decision.credential_ref != item.credential_ref
            or decision.account != item.account
            or decision.purpose != slot.purpose
        ):
            raise PermissionError("workload credential was not authorized for this job")
        if decision.expires_at <= now:
            raise TimeoutError("workload credential grant has expired")
        pairs.append((slot, decision))
    return pairs


def session_seconds(decision, *, now: datetime, budget_end: datetime) -> int:
    """Bounded session length: grant maximum, grant expiry and job budget."""

    remaining = min(decision.expires_at, budget_end) - now
    seconds = min(decision.max_session_seconds, int(remaining.total_seconds()))
    if seconds < MIN_SESSION_SECONDS:
        # A shorter grant or budget than STS can issue: expire instead of
        # handing out a session that outlives its authority.
        if decision.expires_at - now < timedelta(seconds=MIN_SESSION_SECONDS):
            raise TimeoutError("workload credential grant has expired")
        seconds = MIN_SESSION_SECONDS
    return seconds


class CredentialIssueError(RuntimeError):
    """An issuer refused or could not complete issuance.

    Only the provider's error code is kept; provider messages can echo request
    parameters. ``uncertain`` means a session may exist despite the error.
    """

    def __init__(self, code: str, *, uncertain: bool = False) -> None:
        self.code = code
        self.uncertain = uncertain
        super().__init__(f"credential issuer returned {code}")


@dataclass(frozen=True)
class CloudSession:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration: datetime

    def values(self) -> tuple[str, ...]:
        return (self.access_key_id, self.secret_access_key, self.session_token)

    def credentials_file(self) -> bytes:
        return (
            "[default]\n"
            f"aws_access_key_id = {self.access_key_id}\n"
            f"aws_secret_access_key = {self.secret_access_key}\n"
            f"aws_session_token = {self.session_token}\n"
        ).encode()


def parse_aws_source(value: str) -> dict[str, str]:
    """The stored long-lived source key document for ``aws-sts`` slots."""

    try:
        document = json.loads(value)
    except ValueError:
        document = None
    if (
        not isinstance(document, dict)
        or not isinstance(document.get("accessKeyId"), str)
        or not isinstance(document.get("secretAccessKey"), str)
        or not isinstance(document.get("sessionToken", ""), str)
    ):
        raise ValueError("stored cloud source credential is not an access key document")
    return document


class CloudSessionIssuer(Protocol):
    async def issue(
        self,
        *,
        source: Mapping[str, str],
        role_arn: str,
        session_name: str,
        duration_seconds: int,
        policy_arns: tuple[str, ...],
    ) -> CloudSession:
        pass


class AwsStsSessionIssuer:
    """Smallest adapter: STS ``AssumeRole`` with explicit source credentials.

    The client is built only from the stored source key, never from ambient
    worker credentials or configuration profiles. STS sessions cannot be
    revoked individually; their bounded duration is the revocation.
    """

    def __init__(self, client_factory: Callable[..., Any] | None = None) -> None:
        self._client_factory = client_factory

    def _client(self, source: Mapping[str, str]):
        if self._client_factory is not None:
            return self._client_factory(source)
        import boto3

        session = boto3.session.Session(
            aws_access_key_id=source["accessKeyId"],
            aws_secret_access_key=source["secretAccessKey"],
            aws_session_token=source.get("sessionToken") or None,
            region_name="us-east-1",
        )
        return session.client("sts")

    def _assume(self, source, role_arn, session_name, duration_seconds, policy_arns):
        try:
            response = self._client(source).assume_role(
                RoleArn=role_arn,
                RoleSessionName=session_name,
                DurationSeconds=duration_seconds,
                PolicyArns=[{"arn": arn} for arn in policy_arns],
            )
            issued = response["Credentials"]
            return CloudSession(
                access_key_id=issued["AccessKeyId"],
                secret_access_key=issued["SecretAccessKey"],
                session_token=issued["SessionToken"],
                expiration=issued["Expiration"],
            )
        except CredentialIssueError:
            raise
        except Exception as exc:  # noqa: BLE001 - only the code survives
            response = getattr(exc, "response", None)
            error = response.get("Error") or {} if isinstance(response, dict) else {}
            code = str(error.get("Code") or type(exc).__name__)[:64]
            # Without a provider answer the request may have been served.
            raise CredentialIssueError(code, uncertain=not error) from None

    async def issue(
        self, *, source, role_arn, session_name, duration_seconds, policy_arns
    ) -> CloudSession:
        return await asyncio.to_thread(
            self._assume, source, role_arn, session_name, duration_seconds, policy_arns
        )


__all__ = [
    "MIN_SESSION_SECONDS",
    "READ_ONLY_POLICY_ARNS",
    "AwsStsSessionIssuer",
    "CloudSession",
    "CloudSessionIssuer",
    "CredentialIssueError",
    "authorize_workload_credentials",
    "launch_authorizations",
    "owner_principal",
    "parse_aws_source",
    "session_seconds",
]
