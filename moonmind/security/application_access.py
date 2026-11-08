"""Policy-backed application access assertions over sanitized target observations.

This module performs no requests and grants no authority. TargetScope and the
existing target client own admission and transport. Observers evaluate bounded
JSON predicates in memory and retain only booleans, never target response data.
The authorization matrix remains the owner of expected rights.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from moonmind.security.assessments import Contract, Evidence, Text, content_key
from moonmind.security.authorization_matrix import (
    AccessObservation,
    AuthorizationMatrix,
    IdentityRef,
)
from moonmind.security.target_assessments import TargetRequest

JsonPath = Annotated[
    str,
    StringConstraints(
        pattern=r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){0,15}$", max_length=256
    ),
]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Scalar = Annotated[str, StringConstraints(max_length=512)] | bool | int | None


class JsonAssertion(Contract):
    id: IdentityRef
    path: JsonPath
    operator: Literal["equals", "exists"] = "equals"
    value: Scalar = None


class StateAssertion(Contract):
    id: IdentityRef
    path: JsonPath
    operator: Literal["changed", "unchanged", "equals"]
    value: Scalar = None


class AssertionResult(Contract):
    id: IdentityRef
    matched: bool | None = Field(strict=True)


class AccessResource(Contract):
    id: IdentityRef
    owner_identity_ref: IdentityRef
    account_ref: IdentityRef
    session_ref: IdentityRef
    integration_ref: IdentityRef


class ApplicationAccessCase(Contract):
    case_ref: IdentityRef
    resource_ref: IdentityRef
    policy_rule_ref: Text
    request: TargetRequest
    identity_check: TargetRequest
    identity_assertions: list[JsonAssertion] = Field(min_length=1, max_length=16)
    allow_assertions: list[JsonAssertion] = Field(min_length=1, max_length=32)
    deny_assertions: list[JsonAssertion] = Field(min_length=1, max_length=32)
    protected_assertions: list[JsonAssertion] = Field(
        default_factory=list, max_length=32
    )
    state_check: TargetRequest | None = None
    state_assertions: list[StateAssertion] = Field(default_factory=list, max_length=32)
    mutation_ref: IdentityRef | None = None
    mutation_ref_path: JsonPath | None = None
    receipt_assertion: JsonAssertion | None = None

    @model_validator(mode="after")
    def _consistent(self):
        for request in (self.request, self.identity_check, self.state_check):
            if request is not None and request.protocol != "http":
                raise ValueError("application access cases require HTTP")
        for request in (self.identity_check, self.state_check):
            if request is not None and request.method != "GET":
                raise ValueError(
                    "identity and state verification require read-only GET"
                )
        mutation = self.request.method not in {"GET", "HEAD", "OPTIONS"}
        if mutation != (self.mutation_ref is not None):
            raise ValueError("mutations require a stable mutation reference")
        if bool(self.state_assertions) != (self.state_check is not None):
            raise ValueError("state assertions require a state check")
        if mutation and (
            self.mutation_ref_path is None
            or _value(self.request.payload, self.mutation_ref_path) != self.mutation_ref
        ):
            raise ValueError(
                "request payload must carry its declared mutation reference"
            )
        if not mutation and self.mutation_ref_path is not None:
            raise ValueError("only mutations declare a correlation path")
        if mutation and (
            self.receipt_assertion is None
            or self.receipt_assertion.operator != "equals"
            or self.receipt_assertion.value != self.mutation_ref
        ):
            raise ValueError(
                "mutations require a receipt assertion bound to mutation_ref"
            )
        if not mutation and self.receipt_assertion is not None:
            raise ValueError("only mutations declare receipts")
        if mutation and not self.state_assertions:
            raise ValueError("mutations require before/after state evidence")
        for assertions in (
            self.identity_assertions,
            self.allow_assertions + self.deny_assertions + self.protected_assertions,
            self.state_assertions,
        ):
            if len({a.id for a in assertions}) != len(assertions):
                raise ValueError(
                    "assertion IDs must be unique within their observation"
                )
        return self


class ApplicationAccessSpec(Contract):
    schema_version: Literal["application-access-spec/v1"] = "application-access-spec/v1"
    source_ref: Text
    policy_ref: Text
    policy_review_ref: Text
    authorization_matrix: AuthorizationMatrix
    resources: list[AccessResource] = Field(min_length=1, max_length=512)
    cases: list[ApplicationAccessCase] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def _consistent(self):
        matrix = self.authorization_matrix
        resources = {r.id: r for r in self.resources}
        if len(resources) != len(self.resources) or len(
            {c.case_ref for c in self.cases}
        ) != len(self.cases):
            raise ValueError("resources and cases must be unique")
        if {c.case_ref for c in self.cases} != {c.id for c in matrix.cases}:
            raise ValueError(
                "application cases must cover the authorization matrix exactly"
            )
        identities = {i.id: i for i in matrix.identities}
        for resource in self.resources:
            owner = identities.get(resource.owner_identity_ref)
            if (
                owner is None
                or owner.anonymous
                or owner.account_ref != resource.account_ref
                or owner.integration_ref != resource.integration_ref
            ):
                raise ValueError(
                    "resources require a known owner in their account and integration"
                )
        mutations = [c.mutation_ref for c in self.cases if c.mutation_ref is not None]
        if len(set(mutations)) != len(mutations):
            raise ValueError("mutation references must be unique")
        for case in self.cases:
            access = matrix.case(case.case_ref)
            resource = resources.get(case.resource_ref)
            if resource is None or access.resource_ref != case.resource_ref:
                raise ValueError("case resource must match its matrix resource")
            if (access.account_ref, access.session_ref, access.integration_ref) != (
                resource.account_ref,
                resource.session_ref,
                resource.integration_ref,
            ):
                raise ValueError(
                    "matrix case differs from the registered resource context"
                )
            identity = matrix.identity(access.identity_ref)
            if any(
                r.credential_ref != identity.credential_ref
                for r in (case.request, case.identity_check)
            ):
                raise ValueError(
                    "case and identity verification must use the selected identity"
                )
            if access.expected == "deny" and any(
                a.operator != "unchanged" for a in case.state_assertions
            ):
                raise ValueError(
                    "denied mutations must assert unchanged protected state"
                )
        return self


class ApplicationAccessObservation(Contract):
    case_ref: IdentityRef
    identity_before: list[AssertionResult] = Field(default_factory=list, max_length=16)
    identity_after: list[AssertionResult] = Field(default_factory=list, max_length=16)
    response: list[AssertionResult] = Field(default_factory=list, max_length=96)
    state: list[AssertionResult] = Field(default_factory=list, max_length=32)
    evidence_refs: Evidence
    status: int | None = Field(default=None, strict=True, ge=100, le=599)
    attempt_ref: IdentityRef | None = None
    receipt_ref: IdentityRef | None = None
    receipt: AssertionResult | None = None
    stop_reason: (
        Literal[
            "timeout",
            "cancelled",
            "malformed_output",
            "verification_unavailable",
            "identity_failed",
            "request_failed",
            "effect_uncertain",
            "budget_exhausted",
            "unsupported",
        ]
        | None
    ) = None


class ApplicationAccessBundle(Contract):
    schema_version: Literal["application-access-bundle/v1"] = (
        "application-access-bundle/v1"
    )
    spec_digest: Digest
    observations: list[ApplicationAccessObservation] = Field(
        default_factory=list, max_length=512
    )


class ApplicationAccessResult(Contract):
    case_ref: IdentityRef
    resource_ref: IdentityRef
    identity_ref: IdentityRef
    expected: Literal["allow", "deny"]
    access: AccessObservation
    authentication_state: Literal["verified", "unverified"]
    outcome: Literal["pass", "finding", "inconclusive"]
    coverage: Literal["complete", "partial"]
    reasons: list[IdentityRef] = Field(max_length=16)
    attempt_ref: IdentityRef | None = None
    receipt_ref: IdentityRef | None = None


class ApplicationAccessJob(Contract):
    terminal_state: Text
    exit_code: int | None
    input_refs: dict[Literal["spec", "capture"], Text]

    @model_validator(mode="after")
    def _complete(self):
        if set(self.input_refs) != {"spec", "capture"}:
            raise ValueError("job metadata requires spec and capture references")
        return self


class ApplicationAccessReport(Contract):
    schema_version: Literal["application-access-report/v1"] = (
        "application-access-report/v1"
    )
    spec_digest: Digest
    observations_digest: Digest
    input_digests: dict[Literal["spec", "capture"], Digest] = Field(
        default_factory=dict
    )
    job: ApplicationAccessJob | None = None
    cases: list[ApplicationAccessResult] = Field(min_length=1, max_length=512)
    status: Literal["complete", "partial"]
    outcome: Literal["consistent", "violations", "incomplete"]
    limitations: list[Text]
    verification: Literal["not_performed"] = "not_performed"

    @model_validator(mode="after")
    def _consistent(self):
        if self.input_digests and set(self.input_digests) != {"spec", "capture"}:
            raise ValueError("input digests require both spec and capture")
        if len({c.case_ref for c in self.cases}) != len(self.cases):
            raise ValueError("report cases must be unique")
        if any(
            (c.outcome == "pass" and c.coverage != "complete")
            or (c.outcome == "inconclusive" and c.coverage != "partial")
            for c in self.cases
        ):
            raise ValueError("case outcome differs from its coverage")
        partial = any(c.coverage == "partial" for c in self.cases) or bool(
            self.job
            and (self.job.terminal_state != "succeeded" or self.job.exit_code != 0)
        )
        outcome = (
            "violations"
            if any(c.outcome == "finding" for c in self.cases)
            else "incomplete" if partial else "consistent"
        )
        if (
            self.status != ("partial" if partial else "complete")
            or self.outcome != outcome
        ):
            raise ValueError(
                "report coverage and outcome differ from case/job evidence"
            )
        return self


_MISSING = object()


def _value(payload, path):
    for key in path.split("."):
        if not isinstance(payload, dict) or key not in payload:
            return _MISSING
        payload = payload[key]
    return payload


def _equal(a, b):
    return type(a) is type(b) and a == b


def project_response(
    payload: dict, assertions: list[JsonAssertion]
) -> list[AssertionResult]:
    """Only predicate results leave the raw-response boundary."""
    results = []
    for assertion in assertions:
        value = _value(payload, assertion.path)
        matched = (
            value is not _MISSING
            if assertion.operator == "exists"
            else _equal(value, assertion.value)
        )
        results.append(
            AssertionResult(
                id=assertion.id, matched=matched if isinstance(payload, dict) else None
            )
        )
    return results


class StateValue(Contract):
    """Canonical protected-state projection safe to checkpoint between attempts."""

    id: IdentityRef
    present: bool = Field(strict=True)
    digest: Digest | None = None

    @model_validator(mode="after")
    def _consistent(self):
        if self.present != (self.digest is not None):
            raise ValueError("present state requires a value digest")
        return self


def _value_digest(value):
    # Retain JSON type distinctions (notably true versus 1).
    return content_key([type(value).__name__, value])


def project_state_values(
    payload: dict | None, assertions: list[StateAssertion]
) -> list[StateValue]:
    results = []
    for assertion in assertions:
        value = _value(payload, assertion.path)
        present = value is not _MISSING
        results.append(
            StateValue(
                id=assertion.id,
                present=present,
                digest=_value_digest(value) if present else None,
            )
        )
    return results


def compare_state(
    before: list[StateValue], after: list[StateValue], assertions: list[StateAssertion]
) -> list[AssertionResult]:
    expected = {a.id for a in assertions}
    valid = all(
        len(values) == len(expected) and {v.id for v in values} == expected
        for values in (before, after)
    )
    old, new = {v.id: v for v in before}, {v.id: v for v in after}
    results = []
    for assertion in assertions:
        matched = None
        if valid and old[assertion.id].present and new[assertion.id].present:
            if assertion.operator == "equals":
                matched = new[assertion.id].digest == _value_digest(assertion.value)
            else:
                matched = (old[assertion.id].digest == new[assertion.id].digest) == (
                    assertion.operator == "unchanged"
                )
        results.append(AssertionResult(id=assertion.id, matched=matched))
    return results


def project_state(
    before: dict | None, after: dict | None, assertions: list[StateAssertion]
) -> list[AssertionResult]:
    return compare_state(
        project_state_values(before, assertions),
        project_state_values(after, assertions),
        assertions,
    )


def spec_digest(spec: ApplicationAccessSpec) -> str:
    return content_key(spec.model_dump(mode="json"))


def _results(assertions, results):
    expected = {a.id for a in assertions}
    if (
        len({r.id for r in results}) != len(results)
        or {r.id for r in results} != expected
    ):
        return None
    return {r.id: r.matched for r in results}


def assess_application_access(
    spec: ApplicationAccessSpec, bundle: ApplicationAccessBundle
) -> ApplicationAccessReport:
    """Re-evaluate retained predicates without reissuing requests or mutations."""
    digest = spec_digest(spec)
    refs = [o.case_ref for o in bundle.observations]
    valid_bundle = (
        bundle.spec_digest == digest
        and len(refs) == len(set(refs))
        and set(refs) <= {c.case_ref for c in spec.cases}
    )
    observations = {o.case_ref: o for o in bundle.observations} if valid_bundle else {}
    cases = []
    for case in spec.cases:
        expected = spec.authorization_matrix.case(case.case_ref)
        obs = observations.get(case.case_ref)
        reasons = []
        incomplete = True
        observed, outcome, authenticated = "unknown", "inconclusive", False
        evidence = obs.evidence_refs if obs else [spec.source_ref]
        if not valid_bundle:
            reasons.append("observation_binding_invalid")
        elif obs is None:
            reasons.append("observation_missing")
        else:
            before = _results(case.identity_assertions, obs.identity_before)
            after = _results(case.identity_assertions, obs.identity_after)
            authenticated = (
                obs.stop_reason != "identity_failed"
                and before is not None
                and after is not None
                and all(v is True for v in [*before.values(), *after.values()])
            )
            response = _results(
                case.allow_assertions
                + case.deny_assertions
                + case.protected_assertions,
                obs.response,
            )
            state = _results(case.state_assertions, obs.state)
            if not authenticated:
                reasons.append("identity_unverified")
            if obs.stop_reason:
                reasons.append(obs.stop_reason)
            if response is None or any(v is None for v in response.values()):
                reasons.append("response_incomplete")
            if state is None or any(v is None for v in state.values()):
                reasons.append("state_incomplete")
            allow = response is not None and all(
                response[a.id] is True for a in case.allow_assertions
            )
            deny = response is not None and all(
                response[a.id] is True for a in case.deny_assertions
            )
            leaked = response is not None and any(
                response[a.id] is True for a in case.protected_assertions
            )
            state_ok = state is not None and all(
                state[a.id] is True for a in case.state_assertions
            )
            state_violated = state is not None and any(
                state[a.id] is False for a in case.state_assertions
            )
            # Directly observed denial plus unchanged state needs no target
            # receipt side effect. A claimed but mismatched receipt is not absence.
            denied_without_effect = (
                not reasons
                and deny
                and not allow
                and not leaked
                and state_ok
                and obs.attempt_ref is not None
                and obs.receipt_ref is None
                and (
                    obs.receipt is None
                    or (
                        obs.receipt.matched is False
                        and case.receipt_assertion is not None
                        and obs.receipt.id == case.receipt_assertion.id
                    )
                )
                and all(a.operator == "unchanged" for a in case.state_assertions)
            )
            if (
                case.mutation_ref
                and not denied_without_effect
                and (
                    obs.attempt_ref is None
                    or obs.receipt_ref != case.mutation_ref
                    or obs.receipt is None
                    or obs.receipt.matched is not True
                    or obs.receipt.id != case.receipt_assertion.id
                )
            ):
                reasons.append("effect_unreconciled")
            incomplete = bool(reasons)
            # Attribution still requires verified identity. Missing secondary
            # evidence cannot erase an independently established leak/effect.
            if (
                authenticated
                and expected.expected == "deny"
                and (allow or leaked or state_violated)
            ):
                observed, outcome = "allow", "finding"
                reasons.append("unexpected_access")
            elif not reasons:
                if expected.expected == "deny" and deny:
                    observed, outcome = "deny", "pass"
                elif expected.expected == "allow" and allow and not deny and state_ok:
                    observed, outcome = "allow", "pass"
                elif expected.expected == "allow" and deny and not allow and not leaked:
                    observed, outcome = "deny", "finding"
                    reasons.append("expected_access_denied")
                else:
                    reasons.append("operation_not_established")
                    incomplete = True
        cases.append(
            ApplicationAccessResult(
                case_ref=case.case_ref,
                resource_ref=case.resource_ref,
                identity_ref=expected.identity_ref,
                expected=expected.expected,
                access=AccessObservation(
                    operation=case.request.id,
                    case_ref=case.case_ref,
                    observed=observed,
                    evidence_refs=evidence,
                ),
                authentication_state="verified" if authenticated else "unverified",
                outcome=outcome,
                coverage="partial" if incomplete else "complete",
                reasons=reasons,
                attempt_ref=obs.attempt_ref if obs else None,
                receipt_ref=obs.receipt_ref if obs else None,
            )
        )
    incomplete = any(c.coverage == "partial" for c in cases)
    return ApplicationAccessReport(
        spec_digest=digest,
        observations_digest=content_key(bundle.model_dump(mode="json")),
        cases=cases,
        status="partial" if incomplete else "complete",
        outcome=(
            "violations"
            if any(c.outcome == "finding" for c in cases)
            else "incomplete" if incomplete else "consistent"
        ),
        limitations=[
            "declared_policy_and_assertions_only",
            "unobserved_objects_not_assessed",
            "predicate_projections_not_raw_responses",
        ],
    )


def bind_job_result(
    payload: bytes,
    terminal_state: str,
    exit_code: int | None,
    input_refs: dict[str, str],
) -> bytes:
    """Bind authoritative ContainerJob outcome without discarding findings."""
    report = ApplicationAccessReport.model_validate_json(payload)
    data = report.model_dump(mode="json", by_alias=True)
    data["job"] = {
        "terminalState": terminal_state,
        "exitCode": exit_code,
        "inputRefs": input_refs,
    }
    if terminal_state != "succeeded" or exit_code != 0:
        data["status"] = "partial"
        if data["outcome"] != "violations":
            data["outcome"] = "incomplete"
        data["limitations"] = sorted({*data["limitations"], "job_did_not_succeed"})
    return (
        ApplicationAccessReport.model_validate(data)
        .model_dump_json(by_alias=True)
        .encode()
    )


def verify_report_evidence(
    report: ApplicationAccessReport,
    spec: ApplicationAccessSpec,
    bundle: ApplicationAccessBundle,
    spec_body: bytes,
    capture_body: bytes,
) -> None:
    """Reject altered conclusions or inputs before collecting/interpreting them."""
    digests = {
        "spec": hashlib.sha256(spec_body).hexdigest(),
        "capture": hashlib.sha256(capture_body).hexdigest(),
    }
    if report.input_digests != digests or bundle.spec_digest != spec_digest(spec):
        raise ValueError("application access inputs differ from their report")
    expected = assess_application_access(spec, bundle)
    expected.input_digests = digests
    if report.job is not None:
        expected = ApplicationAccessReport.model_validate_json(
            bind_job_result(
                expected.model_dump_json(by_alias=True).encode(),
                report.job.terminal_state,
                report.job.exit_code,
                report.job.input_refs,
            )
        )
    if expected != report:
        raise ValueError("application access report differs from retained evidence")
