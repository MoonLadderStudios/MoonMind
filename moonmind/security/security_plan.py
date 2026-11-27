"""``security-plan/v1``: what an assessment will check, before it runs.

A plan generalizes the web/API test plan to every selected standard. Trusted
code derives the pack identities, the requirement list, the evidence methods a
pack allows, job-contract validity, the authority each job would need and its
readiness. A model or operator proposes the subject, applicability, evidence
paths and job inputs, citing where each proposal came from. Validation is
deterministic and resolves no target authority, credential or network
reference. A plan grants nothing: work still launches under a saved
authorization and the existing rules-of-engagement review.
"""

from __future__ import annotations

import copy
import html
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints, ValidationError, model_validator

from moonmind.schemas.container_job_models import WorkloadProfileSelection
from moonmind.schemas.repository_scanner_models import (
    CLOUD_ACTION_CLASS,
    CloudAssessment,
    ImageAssessment,
    NetworkConfigAssessment,
    RepositoryScan,
    TLSAssessment,
    WebAssessment,
    WebRoute,
)
from moonmind.security.assessment_preset_inputs import (
    INPUT_HELP,
    READINESS_ACTIONS,
    input_diagnostics,
)
from moonmind.security.assessment_projects import AssessmentProjectSnapshot
from moonmind.security.assessments import (
    MAX_RETAINED_REPORT_BYTES,
    AssessmentScope,
    ThreatModel,
    WebAPITestPlan,
    content_key,
)
from moonmind.security.framework_packs import (
    VersionedFrameworkPack,
    load_declared_pack,
    validate_pack_effectivity,
)
from moonmind.security.framework_registry import EXTERNAL_ROLES
from moonmind.security.frameworks import (
    MAX_CONTROL_REQUIREMENTS,
    MAX_FRAMEWORK_PACKS,
    AssessmentInputError,
)
from moonmind.security.frameworks import AssessmentText as Text
from moonmind.security.frameworks import DeclaredRequirementChoice
from moonmind.security.frameworks import FrameworkContract as Contract
from moonmind.security.frameworks import (
    FrameworkPackSelection,
    PackEdition,
    PackIdentifier,
)
from moonmind.security.target_assessments import TargetScope
from moonmind.security.web_api_methodology import (
    Method,
    PlannedProcedure,
    TestInterface,
    procedure_evidence,
)
from moonmind.security.workload_profiles import (
    CLOUD_ASSESSMENT_PROFILE_REF,
    TLS_ACTION_CLASS,
    TLS_ASSESSMENT_PROFILE_REF,
    WEB_ASSESSMENT_PROFILE_REF,
    CredentialPurpose,
)
from moonmind.utils.logging import redact_sensitive_text

SCHEMA_VERSION = "security-plan/v1"
MAX_PLAN_BYTES = MAX_RETAINED_REPORT_BYTES
MAX_PLAN_JOBS = 32

#: Assessment evidence methods. The first three are the control evaluator's
#: existing vocabulary; ``external_only`` names work only a laboratory, QSA,
#: auditor or regulator can do.
EvidenceMethod = Literal[
    "deterministic_check", "agent_interpretation", "document_review", "external_only"
]
EVIDENCE_METHODS: tuple[str, ...] = EvidenceMethod.__args__
GapKind = Literal[
    "no_evidence_path",
    "missing_input",
    "tool_unavailable",
    "feed_unavailable",
    "identity_unavailable",
]
#: The kind of gap each readiness diagnostic code reports.
READINESS_GAPS: dict[str, str] = {
    "invalid_assessment_input": "missing_input",
    "assessment_authority_unavailable": "missing_input",
    "feed_cache_unavailable": "feed_unavailable",
    "scanner_image_unavailable": "tool_unavailable",
    "scanner_probe_unavailable": "tool_unavailable",
    "worker_unavailable": "tool_unavailable",
    "credential_backend_unavailable": "identity_unavailable",
    "credential_unavailable": "identity_unavailable",
    "credential_resolution_unavailable": "identity_unavailable",
}
JOB_KINDS = (
    "repository-scan",
    "image-assessment",
    "cloud-assessment",
    "web-assessment",
    "application-access",
    "tls-assessment",
    "dns-assessment",
    "service-inventory",
    "network-config",
)
#: Job input fields bound at launch from a saved authorization and its grants.
#: A plan names credential purposes; references and assessment identities are
#: never part of it.
LAUNCH_BOUND_FIELDS = frozenset(
    {
        "credentialRef",
        "credential_ref",
        "targetCredentialRef",
        "target_credential_ref",
        "assessmentRef",
        "assessment_ref",
        "credentials",
        "credentialSlots",
        "credential_slots",
    }
)
_PLACEHOLDER_REF = "plan://bound-at-launch"
_PLACEHOLDER_ASSESSMENT = "planned"
_REF = re.compile(r"^[a-z0-9][a-z0-9.-]{0,127}$")
_PROFILE_HELP = {
    "application-access": "Name profileRef, mode, the profile mode's actionClass and targetScope; credential slots are bound at launch.",
    "tls-assessment": "Select an HTTPS origin, egressPolicyRef, protocols and individual cipher names.",
    "dns-assessment": "Name profileRef, mode, egressPolicyRef and dnsAssessment with exact questions.",
    "service-inventory": "Name profileRef, mode, egressPolicyRef, nativeNetwork and serviceInventory.",
}

Identifier = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ArtifactRef = Annotated[str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]+$")]


def _none(value) -> bool:
    return value is None


def _empty(value) -> bool:
    return not value


class SecurityPlanError(AssessmentInputError):
    """Field-addressed plan diagnostics that never echo plan content."""

    def __init__(self, diagnostics: list[str]):
        self.diagnostics = list(dict.fromkeys(diagnostics))
        super().__init__("; ".join(self.diagnostics[:20]))


class PlanSource(Contract):
    """Where a proposal came from; a citation, never authority."""

    kind: Literal["file", "document", "environment_field", "operator_input"]
    ref: Text


Sources = Annotated[list[PlanSource], Field(min_length=1, max_length=16)]


class PlanProject(Contract):
    """The saved assessment project environment snapshot a plan was drafted from."""

    project_id: Identifier
    environment_id: Identifier
    source_version: int = Field(ge=0)
    digest: Sha256


class PlanSubject(Contract):
    type: Literal["application", "endpoint", "network"]
    project: PlanProject
    candidate_ref: Text
    system_boundary: Text
    scope: AssessmentScope
    sources: Sources


class PlanStandard(FrameworkPackSelection):
    """A selected pack by catalog identity, edition, revision and digest.

    An installed pack omits ``artifactRef`` and carries its installed digest.
    ``title`` and ``distribution`` are derived; restricted packs omit titles.
    """

    sha256: Sha256
    title: Text | None = Field(default=None, exclude_if=_none)
    distribution: Literal["redistributable", "restricted"] | None = None

    @model_validator(mode="after")
    def artifact_or_installed(self):
        return self


class PlanGap(Contract):
    """Why a requirement lacks a usable evidence path, and what to do next."""

    kind: GapKind
    detail: Text
    job_id: Identifier | None = Field(default=None, exclude_if=_none)
    readiness_code: str | None = Field(default=None, exclude_if=_none)
    next_action: Text | None = Field(default=None, exclude_if=_none)


class PlannedRequirement(Contract):
    # Derived from the selected pack and its requirement choice.
    pack_id: PackIdentifier
    edition: PackEdition
    id: Text
    title: Text | None = Field(default=None, exclude_if=_none)
    allowed_methods: Annotated[list[EvidenceMethod], Field(max_length=4)] = Field(
        default_factory=list
    )
    external_party: Text | None = Field(default=None, exclude_if=_none)
    evidence_request_ids: Annotated[list[Text], Field(max_length=40)] = Field(
        default_factory=list
    )
    # Proposed, each citing its source.
    applicability: Literal["applicable", "not_applicable", "undetermined"]
    reason: Text
    sources: Sources
    methods: Annotated[list[EvidenceMethod], Field(max_length=4)] = Field(
        default_factory=list
    )
    job_ids: Annotated[list[Identifier], Field(max_length=MAX_PLAN_JOBS)] = Field(
        default_factory=list
    )
    procedures: Annotated[list[PlannedProcedure], Field(max_length=16)] = Field(
        default_factory=list
    )
    gaps: Annotated[list[PlanGap], Field(max_length=32)] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self):
        if len(set(self.methods)) != len(self.methods):
            raise ValueError("duplicate evidence method")
        if len(set(self.job_ids)) != len(self.job_ids):
            raise ValueError("duplicate planned job")
        if self.applicability == "not_applicable" and (
            self.methods or self.job_ids or self.procedures
        ):
            raise ValueError("inapplicable requirements select no evidence path")
        return self


class AuthorityTarget(Contract):
    """One target admission would have to name for the job; never a grant."""

    kind: Literal[
        "workspace_path",
        "artifact",
        "image",
        "web_origin",
        "cloud_account",
        "dns_names",
        "network_hosts",
    ]
    ref: Text
    routes: list[WebRoute] = Field(default_factory=list, exclude_if=_empty)
    exclusions: list[str] = Field(default_factory=list, exclude_if=_empty)
    regions: list[str] = Field(default_factory=list, exclude_if=_empty)
    names: list[str] = Field(default_factory=list, exclude_if=_empty)
    ports: list[int] = Field(default_factory=list, exclude_if=_empty)


class JobAuthority(Contract):
    """What admission would require before the job runs, derived from its input.

    ``scoped`` jobs run only inside an admitted assessment scope that names
    these profiles, action classes, targets, credential purposes, networks and
    budget. Unscoped jobs read the workspace, artifacts or a registry image.
    """

    scoped: bool
    profile_refs: list[str] = Field(default_factory=list)
    action_classes: list[str] = Field(default_factory=list)
    targets: list[AuthorityTarget] = Field(default_factory=list)
    credential_purposes: list[CredentialPurpose] = Field(default_factory=list)
    network_refs: list[str] = Field(default_factory=list)
    max_job_seconds: int = Field(ge=1)
    max_requests_per_second: int | None = Field(default=None, exclude_if=_none)


class JobReadiness(Contract):
    status: Literal["ready", "blocked", "unavailable", "unverified"]
    codes: list[str] = Field(default_factory=list, exclude_if=_empty)


class PlannedJob(Contract):
    id: Identifier
    kind: Text
    # Proposed input to the job's existing typed contract.
    input: Annotated[dict[str, Any], Field(max_length=64)]
    assets: Annotated[list[Text], Field(min_length=1, max_length=64)]
    sources: Sources
    # Derived.
    authority: JobAuthority | None = None
    readiness: JobReadiness | None = None


class PlanIdentity(Contract):
    """A named test identity; its credential is bound at launch, never here."""

    id: Text
    available: bool


class PlanMethodology(Contract):
    """Web/API procedure context for the selected standard that declares them."""

    pack_id: PackIdentifier
    edition: PackEdition
    threat_model: ThreatModel
    interfaces: Annotated[list[TestInterface], Field(min_length=1, max_length=64)]
    identities: Annotated[list[PlanIdentity], Field(max_length=64)] = Field(
        default_factory=list
    )
    source_refs: Annotated[list[Text], Field(max_length=32)] = Field(
        default_factory=list
    )
    api_context_refs: Annotated[list[Text], Field(max_length=32)] = Field(
        default_factory=list
    )
    allowed_methods: Annotated[list[Method], Field(min_length=1, max_length=6)]


class OpenPoint(Contract):
    kind: Literal["assumption", "unknown", "operator_question"]
    text: Text


class RegulatoryFact(Contract):
    """A regulatory fact stays undetermined until an operator record is cited."""

    kind: Literal[
        "jurisdiction_adoption",
        "certification",
        "laboratory_approval",
        "regulator_approval",
    ]
    subject: Text
    status: Literal["established", "not_established", "undetermined"] = "undetermined"
    record_ref: ArtifactRef | None = Field(default=None, exclude_if=_none)
    sources: Sources

    def cited(self) -> bool:
        return self.record_ref is not None and any(
            source.kind in {"operator_input", "document"}
            and source.ref == self.record_ref
            for source in self.sources
        )


class SecurityPlan(Contract):
    schema_version: Literal["security-plan/v1"] = SCHEMA_VERSION
    subject: PlanSubject
    standards: Annotated[
        list[PlanStandard], Field(min_length=1, max_length=MAX_FRAMEWORK_PACKS)
    ]
    requirements: Annotated[
        list[PlannedRequirement],
        Field(min_length=1, max_length=MAX_CONTROL_REQUIREMENTS),
    ]
    jobs: Annotated[list[PlannedJob], Field(max_length=MAX_PLAN_JOBS)] = Field(
        default_factory=list
    )
    methodology: PlanMethodology | None = Field(default=None, exclude_if=_none)
    open_points: Annotated[list[OpenPoint], Field(max_length=64)] = Field(
        default_factory=list
    )
    regulatory_facts: Annotated[list[RegulatoryFact], Field(max_length=32)] = Field(
        default_factory=list
    )

    # The web/API methodology reads a plan through these names, which it shares
    # with retained ``web-api-test-plan/v1`` plans.
    @property
    def pack(self) -> PlanStandard:
        selected = self.methodology
        return next(
            s
            for s in self.standards
            if selected
            and (s.pack_id, s.edition) == (selected.pack_id, selected.edition)
        )

    @property
    def scope(self) -> AssessmentScope:
        return self.subject.scope

    @property
    def candidate_ref(self) -> str:
        return self.subject.candidate_ref

    @property
    def system_boundary(self) -> str:
        return self.subject.system_boundary

    @property
    def objectives(self) -> list[PlannedRequirement]:
        key = (self.methodology.pack_id, self.methodology.edition)
        return [r for r in self.requirements if (r.pack_id, r.edition) == key]

    @property
    def threat_model(self) -> ThreatModel:
        return self.methodology.threat_model

    @property
    def interfaces(self) -> list[TestInterface]:
        return self.methodology.interfaces

    @property
    def identities(self) -> list[PlanIdentity]:
        return self.methodology.identities

    @property
    def source_refs(self) -> list[str]:
        return self.methodology.source_refs

    @property
    def api_context_refs(self) -> list[str]:
        return self.methodology.api_context_refs

    @property
    def allowed_methods(self) -> list[str]:
        return self.methodology.allowed_methods

    def with_context(self, **refs) -> SecurityPlan:
        """The plan with only the readable methodology context refs."""
        return self.model_copy(
            update={"methodology": self.methodology.model_copy(update=refs)}
        )


def _location(loc) -> str:
    text = ""
    for part in loc:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else part)
    return text or "plan"


def _schema_diagnostics(exc: ValidationError) -> list[str]:
    messages = []
    for error in exc.errors(
        include_input=False, include_context=False, include_url=False
    ):
        loc = list(error["loc"])
        detail = error["msg"]
        if error["type"] == "extra_forbidden":
            # An unknown field's name can itself carry sensitive input.
            loc, detail = loc[:-1], "unknown field"
        if error["type"] == "value_error":
            detail = detail.removeprefix("Value error, ")
        messages.append(f"{_location(loc)}: {detail}")
    return messages


def parse_security_plan(raw: bytes | str | Mapping) -> SecurityPlan:
    if isinstance(raw, (bytes, str)):
        if len(raw) > MAX_PLAN_BYTES:
            raise SecurityPlanError([f"plan: exceeds the {MAX_PLAN_BYTES} byte bound"])

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise SecurityPlanError(["plan: duplicate JSON key"])
                result[key] = value
            return result

        try:
            raw = json.loads(raw, object_pairs_hook=pairs)
        except ValueError as exc:
            if isinstance(exc, SecurityPlanError):
                raise
            raise SecurityPlanError(["plan: not a JSON object"]) from None
    try:
        return SecurityPlan.model_validate(raw)
    except ValidationError as exc:
        raise SecurityPlanError(_schema_diagnostics(exc)) from None


def read_methodology_plan(raw: bytes):
    """Read a methodology plan: ``security-plan/v1``, or a retained v1 plan.

    Compatibility reader for ``web-api-test-plan/v1``. Its consumers are
    retained v1 plans named by saved ``methodology.planRef`` inputs (reruns
    and schedules) and the coverage reports and procedure results that cite
    them. Remove it when no saved input or retained report names a v1 plan.
    """
    try:
        version = json.loads(raw).get("schemaVersion")
    except (ValueError, AttributeError):
        version = None
    if version == "web-api-test-plan/v1":
        return WebAPITestPlan.model_validate_json(raw)
    return parse_security_plan(raw)


def plan_identity(plan: SecurityPlan) -> str:
    """Content identity: any change to standards, choices, subject or jobs is new."""
    return content_key(plan.model_dump(mode="json", by_alias=True))


def load_standard_pack(standard: PlanStandard, raw: bytes | None = None):
    """The selected pack: installed by exact identity, or retained bytes."""
    from moonmind.security.framework_registry import installed_digest, installed_pack

    if standard.artifact_ref is None:
        raw, pack = installed_pack(standard.pack_id)
        if (pack.edition, pack.revision) != (standard.edition, standard.revision):
            raise ValueError("installed pack edition or revision differs")
        if installed_digest(standard.pack_id) != standard.sha256:
            raise ValueError("installed pack digest differs")
        return pack
    import hashlib

    if raw is None or hashlib.sha256(raw).hexdigest() != standard.sha256:
        raise ValueError("pack artifact differs from the selected digest")
    return load_declared_pack(
        raw,
        pack_id=standard.pack_id,
        edition=standard.edition,
        revision=standard.revision,
    )


def selected_for_subject(requirement, subject_type: str) -> bool:
    """Whether a standard's requirement is in scope for the plan's subject.

    An application plan keeps every requirement. Another subject selects the
    requirements a pack tags with it in ``subjectTypes``.
    """
    if subject_type == "application":
        return True
    return subject_type in requirement.subject_types


_REQUIRED_METHODS = {
    "deterministic_check": "deterministic_check",
    "enquiry": "document_review",
    "evidence": "document_review",
    "observation": "agent_interpretation",
}


def requirement_evidence(pack: VersionedFrameworkPack, requirement, choice):
    """Evidence methods the pack allows for one requirement, who alone can close
    an external-only one, and the organisational evidence requests it raises."""
    methods = set()
    types = {value.casefold() for value in requirement.evidence_types}
    if requirement.objectives or "deterministic_check" in types:
        methods.add("deterministic_check")
    methods.update(_REQUIRED_METHODS[m] for m in requirement.required_evidence_methods)
    methods.update(procedure_evidence(p.method)[0] for p in requirement.procedures)
    if (
        requirement.organisational_evidence
        or choice.organisational_evidence
        or types & {"organizational", "organisational", "documentary"}
        or pack.profile is not None
    ):
        methods.add("document_review")
    claim = next(
        (c for c in pack.external_claims if c.id == requirement.external_claim), None
    )
    roles = sorted(types & EXTERNAL_ROLES)
    if claim or roles:
        methods.add("external_only")
    party = claim.verifiable_by if claim else (", ".join(roles) or None)
    if requirement.not_assessed_reason:
        methods = set()
    requests = [item.id for item in requirement.organisational_evidence]
    requests += [
        item.id for item in choice.organisational_evidence if item.id not in requests
    ]
    return [m for m in EVIDENCE_METHODS if m in methods], party, requests


def _choice(standard, requirement_id):
    return DeclaredRequirementChoice.model_validate(
        standard.requirement_choices.get(requirement_id, {})
    )


def _launch_bound(value, path="") -> list[str]:
    found = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            here = f"{path}.{key}" if path else str(key)
            if key in LAUNCH_BOUND_FIELDS and item not in (None, {}, []):
                found.append(here)
            else:
                found.extend(_launch_bound(item, here))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_launch_bound(item, f"{path}[{index}]"))
    return found


def _profile_selection(value, *, kind, native):
    """A workload-profile selection plus the profile facts its admission uses."""
    value = dict(value)
    facts = {
        "actionClass": value.pop("actionClass", None),
        "egressPolicyRef": value.pop("egressPolicyRef", None),
    }
    scope = value.pop("targetScope", None) if kind == "application-access" else None
    errors = []
    for name, fact in facts.items():
        if fact is not None and (not isinstance(fact, str) or not _REF.fullmatch(fact)):
            errors.append(f"{name}: must be an explicit ref")
    try:
        selection = WorkloadProfileSelection.model_validate(value)
    except ValidationError as exc:
        errors += input_diagnostics(WorkloadProfileSelection, exc, _PROFILE_HELP[kind])
        selection = None
    if selection is not None and (
        selection.arguments
        or selection.configuration_exceptions
        or any(
            getattr(selection, field) is not None
            for field in ("native_network", "dns_assessment", "service_inventory")
            if field not in native
        )
        or any(getattr(selection, field) is None for field in native)
    ):
        errors.append(f"assessment: {_PROFILE_HELP[kind]}")
    if kind == "application-access":
        if facts["actionClass"] is None:
            errors.append("actionClass: name the profile mode's declared action class")
        try:
            scope = TargetScope.model_validate(scope)
        except ValidationError as exc:
            errors += input_diagnostics(TargetScope, exc, _PROFILE_HELP[kind])
            scope = None
        if scope is not None and facts["egressPolicyRef"] not in {
            None,
            scope.web.egress_policy_ref,
        }:
            errors.append("egressPolicyRef: must equal targetScope.web.egressPolicyRef")
    elif facts["egressPolicyRef"] is None:
        errors.append("egressPolicyRef: name the profile's egress policy")
    elif facts["actionClass"] is not None:
        errors.append("actionClass: fixed by the native profile owner")
    if errors:
        raise SecurityPlanError(errors)
    return selection, facts, scope


def native_job(kind: str, value: Mapping):
    """Check one planned job input with its existing typed job contract.

    Launch-bound fields are refused; the checks substitute placeholders for
    them, so no credential or assessment reference is resolved or retained.
    """
    if kind not in JOB_KINDS:
        raise SecurityPlanError(["kind: unknown job kind"])
    bound = _launch_bound(value)
    if bound:
        raise SecurityPlanError(
            [
                f"{path}: bound at launch from a saved authorization; plans name credential purposes only"
                for path in bound
            ]
        )
    value = copy.deepcopy(dict(value))
    if kind in {"dns-assessment", "service-inventory", "application-access"}:
        native = {
            "dns-assessment": ("dns_assessment",),
            "service-inventory": ("native_network", "service_inventory"),
            "application-access": (),
        }[kind]
        return _profile_selection(value, kind=kind, native=native)
    model = {
        "repository-scan": RepositoryScan,
        "image-assessment": ImageAssessment,
        "cloud-assessment": CloudAssessment,
        "web-assessment": WebAssessment,
        "tls-assessment": TLSAssessment,
        "network-config": NetworkConfigAssessment,
    }[kind]
    if kind == "cloud-assessment":
        value.update(
            credentialRef=_PLACEHOLDER_REF, assessmentRef=_PLACEHOLDER_ASSESSMENT
        )
    if kind == "web-assessment" and isinstance(value.get("identity"), dict):
        value["identity"] = {**value["identity"], "credentialRef": _PLACEHOLDER_REF}
    try:
        return model.model_validate(value), {}, None
    except ValidationError as exc:
        help_text = INPUT_HELP.get(kind) or _PROFILE_HELP[kind]
        raise SecurityPlanError(input_diagnostics(model, exc, help_text)) from None


def _web_target(web):
    return AuthorityTarget(
        kind="web_origin",
        ref=web.origin,
        routes=web.routes,
        exclusions=web.exclusions,
    )


def _job_seconds(kind, model) -> int:
    """The job timeout the native builder renders, which admission budgets."""
    from moonmind.security.assessment_preset_inputs import assessment_job_spec
    from moonmind.security.network_configs import build_network_config_job
    from moonmind.security.tls_assessments import build_tls_assessment_job

    if kind == "tls-assessment":
        return build_tls_assessment_job(model)["timeoutSeconds"]
    if kind == "network-config":
        return build_network_config_job(model)["timeoutSeconds"]
    return assessment_job_spec(kind, model.model_dump(mode="json", by_alias=True))[
        "timeoutSeconds"
    ]


def job_authority(kind: str, model, facts, scope=None) -> JobAuthority:
    """Derive the authority admission would require for one checked job input."""
    if kind == "repository-scan":
        return JobAuthority(
            scoped=False,
            targets=[AuthorityTarget(kind="workspace_path", ref=model.input_path)],
            max_job_seconds=_job_seconds(kind, model),
        )
    if kind == "network-config":
        # Captured exports of the saved environment, analyzed with no network:
        # no profile, action class, credential purpose or egress.
        return JobAuthority(
            scoped=False,
            targets=[
                AuthorityTarget(kind="artifact", ref=export.artifact_ref)
                for export in model.exports
            ],
            max_job_seconds=_job_seconds(kind, model),
        )
    if kind == "image-assessment":
        registry = model.target == "registry"
        return JobAuthority(
            scoped=False,
            targets=[
                AuthorityTarget(
                    kind="image" if registry else "artifact",
                    ref=model.image_reference if registry else model.input_artifact_ref,
                )
            ],
            network_refs=[model.egress_policy_ref] if model.egress_policy_ref else [],
            max_job_seconds=_job_seconds(kind, model),
        )
    if kind == "cloud-assessment":
        return JobAuthority(
            scoped=True,
            profile_refs=[CLOUD_ASSESSMENT_PROFILE_REF],
            action_classes=[CLOUD_ACTION_CLASS],
            targets=[
                AuthorityTarget(
                    kind="cloud_account",
                    ref=model.account,
                    names=[model.provider],
                    regions=model.regions,
                )
            ],
            credential_purposes=["cloud_inventory"],
            network_refs=[model.egress_policy_ref],
            max_job_seconds=_job_seconds(kind, model),
        )
    if kind == "web-assessment":
        api = model.api_definition
        return JobAuthority(
            scoped=True,
            profile_refs=[WEB_ASSESSMENT_PROFILE_REF],
            action_classes=[model.action_class],
            targets=[_web_target(model)]
            + (
                [AuthorityTarget(kind="artifact", ref=api.input_artifact_ref)]
                if api
                else []
            ),
            credential_purposes=["application_auth"] if model.identity else [],
            network_refs=[model.egress_policy_ref],
            max_job_seconds=_job_seconds(kind, model),
            max_requests_per_second=model.max_requests_per_second,
        )
    if kind == "tls-assessment":
        trust = model.trust_artifact_ref
        return JobAuthority(
            scoped=True,
            profile_refs=[TLS_ASSESSMENT_PROFILE_REF],
            action_classes=[TLS_ACTION_CLASS],
            targets=[AuthorityTarget(kind="web_origin", ref=model.origin)]
            + ([AuthorityTarget(kind="artifact", ref=trust)] if trust else []),
            network_refs=[model.egress_policy_ref],
            max_job_seconds=_job_seconds(kind, model),
            max_requests_per_second=model.max_requests_per_second,
        )
    if kind == "dns-assessment":
        dns = model.dns_assessment
        return JobAuthority(
            scoped=True,
            profile_refs=[model.profile_ref],
            action_classes=["dns-observation"],
            targets=[
                AuthorityTarget(
                    kind="dns_names",
                    ref="dns",
                    names=sorted(
                        {q.name for q in (*dns.questions, *dns.allowed_followups)}
                    ),
                )
            ],
            network_refs=[facts["egressPolicyRef"]],
            max_job_seconds=dns.timeout_seconds,
            max_requests_per_second=dns.queries_per_second,
        )
    if kind == "service-inventory":
        native = model.native_network
        return JobAuthority(
            scoped=True,
            profile_refs=[model.profile_ref],
            action_classes=["service-inventory"],
            targets=[
                AuthorityTarget(
                    kind="network_hosts",
                    ref="native",
                    names=list(target.hosts),
                    ports=list(target.ports),
                    exclusions=list(target.exclusions),
                )
                for target in native.targets
            ],
            network_refs=[facts["egressPolicyRef"]],
            max_job_seconds=native.limits.max_duration_seconds,
        )
    # application-access
    web = scope.web
    return JobAuthority(
        scoped=True,
        profile_refs=[model.profile_ref],
        action_classes=list(dict.fromkeys([facts["actionClass"], web.action_class])),
        targets=[_web_target(web)],
        credential_purposes=["application_auth"],
        network_refs=[web.egress_policy_ref],
        max_job_seconds=web.timeout_seconds,
        max_requests_per_second=web.max_requests_per_second,
    )


def job_readiness(result: Mapping | None, job_id: str = "job") -> JobReadiness:
    """Summarize an existing readiness result; absence is never readiness."""
    if result is None:
        return JobReadiness(status="unverified")
    status = result.get("status")
    if status not in {"ready", "blocked", "unavailable"}:
        raise SecurityPlanError([f"readiness.{job_id}.status: not a readiness result"])
    codes = [
        d.get("code")
        for d in result.get("diagnostics") or []
        if isinstance(d, Mapping) and d.get("code") in READINESS_ACTIONS
    ]
    for check in result.get("checks") or []:
        if isinstance(check, Mapping) and check.get("status") == "blocked":
            code = str(check.get("code") or "")
            if code.startswith("feed_"):
                codes.append("feed_cache_unavailable")
            elif code.startswith("scanner_"):
                codes.append("scanner_image_unavailable")
    if status != "ready" and not codes:
        codes.append(
            "worker_unavailable"
            if status == "unavailable"
            else "scanner_probe_unavailable"
        )
    return JobReadiness(
        status=status, codes=list(dict.fromkeys(codes)) if status != "ready" else []
    )


def _readiness_gap(code, job_id=None, detail=None):
    return PlanGap(
        kind=READINESS_GAPS[code],
        detail=detail or f"Job {job_id} is not ready: {code}",
        job_id=job_id,
        readiness_code=code,
        next_action=READINESS_ACTIONS[code],
    )


def _required_gaps(plan, row, requirement, restricted, jobs):
    gaps = []
    if requirement.not_assessed_reason:
        gaps.append(
            PlanGap(
                kind="no_evidence_path",
                detail=(
                    "MoonMind has no evidence path for this requirement."
                    if restricted
                    else requirement.not_assessed_reason
                ),
            )
        )
    for job_id in row.job_ids:
        job = jobs.get(job_id)
        if job is not None and job.readiness and job.readiness.status != "ready":
            gaps.extend(_readiness_gap(code, job_id) for code in job.readiness.codes)
    if plan.methodology is not None and (row.pack_id, row.edition) == (
        plan.methodology.pack_id,
        plan.methodology.edition,
    ):
        available = {i.id: i.available for i in plan.methodology.identities}
        for identity in dict.fromkeys(
            i for p in row.procedures for i in p.identity_ids
        ):
            if available.get(identity) is False:
                gaps.append(
                    _readiness_gap(
                        "credential_unavailable",
                        detail=f"Test identity {identity} is unavailable",
                    )
                )
    return gaps


def _gap_key(gap):
    return json.dumps(gap.model_dump(mode="json", by_alias=True), sort_keys=True)


def derive_security_plan(
    draft,
    *,
    packs: Mapping[tuple[str, str], VersionedFrameworkPack],
    readiness: Mapping[str, Mapping] | None = None,
) -> SecurityPlan:
    """Fill a drafted plan's derived content with trusted code.

    Proposals stay as drafted, except that a regulatory fact without a cited
    operator record becomes undetermined. Missing requirement rows are not
    invented: applicability and its reason are proposals.
    """
    plan = draft if isinstance(draft, SecurityPlan) else parse_security_plan(draft)
    readiness = readiness or {}
    standards = []
    for standard in plan.standards:
        pack = packs.get((standard.pack_id, standard.edition))
        if pack is None:
            standards.append(standard)
            continue
        restricted = pack.distribution == "restricted"
        standards.append(
            standard.model_copy(
                update={
                    "title": None if restricted else pack.title,
                    "distribution": pack.distribution,
                }
            )
        )
    jobs = []
    for job in plan.jobs:
        try:
            model, facts, scope = native_job(job.kind, job.input)
            authority = job_authority(job.kind, model, facts, scope)
        except SecurityPlanError:
            authority = None
        jobs.append(
            job.model_copy(
                update={
                    "authority": authority,
                    "readiness": job_readiness(readiness.get(job.id), job.id),
                }
            )
        )
    plan = plan.model_copy(update={"standards": standards, "jobs": jobs})
    by_job = {job.id: job for job in jobs}
    rows = []
    for row in plan.requirements:
        pack = packs.get((row.pack_id, row.edition))
        requirement = (
            next((r for r in pack.requirements if r.id == row.id), None)
            if pack
            else None
        )
        if requirement is None:
            rows.append(row)
            continue
        standard = next(
            (
                s
                for s in standards
                if (s.pack_id, s.edition) == (row.pack_id, row.edition)
            ),
            None,
        )
        if standard is None:
            rows.append(row)
            continue
        restricted = pack.distribution == "restricted"
        allowed, party, requests = requirement_evidence(
            pack, requirement, _choice(standard, row.id)
        )
        row = row.model_copy(
            update={
                "title": None if restricted else requirement.title,
                "allowed_methods": allowed,
                "external_party": party,
                "evidence_request_ids": requests,
            }
        )
        present = {_gap_key(g) for g in row.gaps}
        missing = [
            gap
            for gap in _required_gaps(plan, row, requirement, restricted, by_job)
            if _gap_key(gap) not in present
        ]
        gaps = [
            (
                gap.model_copy(
                    update={
                        "kind": READINESS_GAPS[gap.readiness_code],
                        "next_action": READINESS_ACTIONS[gap.readiness_code],
                    }
                )
                if gap.readiness_code in READINESS_ACTIONS
                else gap
            )
            for gap in row.gaps
        ]
        rows.append(row.model_copy(update={"gaps": gaps + missing}))
    regulatory = [
        (
            fact
            if fact.status == "undetermined" or fact.cited()
            else fact.model_copy(update={"status": "undetermined"})
        )
        for fact in plan.regulatory_facts
    ]
    return plan.model_copy(
        update={"requirements": rows, "regulatory_facts": regulatory}
    )


def _candidates(snapshot: AssessmentProjectSnapshot):
    config = snapshot.configuration
    origins = set(config.origin_candidates)
    hosts = {urlsplit(origin).hostname for origin in origins} | {
        asset.lower().rstrip(".") for asset in config.assets
    }
    assets = (
        set(config.assets)
        | origins
        | {item.repository for item in config.repositories}
        | {item.account for item in config.cloud_account_candidates}
        | set(config.artifact_refs)
    )
    return config, origins, hosts, assets


def _target_within(target: AuthorityTarget, snapshot) -> bool:
    config, origins, hosts, _ = _candidates(snapshot)
    if target.kind == "workspace_path":
        return True
    if target.kind == "artifact":
        return target.ref in config.artifact_refs
    if target.kind == "image":
        return target.ref in config.assets
    if target.kind == "web_origin":
        return target.ref in origins
    if target.kind == "cloud_account":
        return any(
            c.account == target.ref
            and [c.provider] == target.names
            and set(target.regions) <= set(c.regions)
            for c in config.cloud_account_candidates
        )
    return set(target.names) <= hosts


def _secret_paths(value, path="plan") -> list[str]:
    from moonmind.security.outbound_scan import scan_outbound_text

    found = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            here = f"{path}.{key}"
            if isinstance(key, str) and redact_sensitive_text(key) != key:
                found.append(path)
            found.extend(_secret_paths(item, here))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_secret_paths(item, f"{path}[{index}]"))
    elif isinstance(value, str) and (
        redact_sensitive_text(value) != value
        or scan_outbound_text(value, high_security_mode=True).findings
    ):
        found.append(path)
    return found


def validate_security_plan(
    plan,
    *,
    packs: Mapping[tuple[str, str], VersionedFrameworkPack],
    snapshot: AssessmentProjectSnapshot,
    now: datetime,
    readiness: Mapping[str, Mapping] | None = None,
) -> SecurityPlan:
    """Check a plan deterministically, with no model and no resolved authority.

    Raises ``SecurityPlanError`` with field-addressed diagnostics. Derived
    content must equal what trusted code derives from the selected packs, the
    native job contracts and the supplied readiness results.
    """
    plan = plan if isinstance(plan, SecurityPlan) else parse_security_plan(plan)
    errors = [
        f"{path.removeprefix('plan.')}: contains secret material; cite a reference instead"
        for path in _secret_paths(plan.model_dump(mode="json", by_alias=True))
    ]
    if len(plan.model_dump_json(by_alias=True).encode()) > MAX_PLAN_BYTES:
        errors.append(f"plan: exceeds the {MAX_PLAN_BYTES} byte bound")
    project = plan.subject.project
    if (
        project.project_id,
        project.environment_id,
        project.source_version,
        project.digest,
    ) != (
        snapshot.project_id,
        snapshot.environment_id,
        snapshot.source_version,
        snapshot.digest,
    ):
        errors.append("subject.project: differs from the saved environment snapshot")
    config, _, _, candidate_assets = _candidates(snapshot)
    scope = plan.subject.scope
    for index, asset in enumerate(scope.assets):
        if asset not in candidate_assets:
            errors.append(
                f"subject.scope.assets[{index}]: outside the saved environment's candidates"
            )
    if set(config.exclusions) - set(scope.exclusions):
        errors.append("subject.scope.exclusions: drops a saved environment exclusion")
    keys = [(s.pack_id, s.edition) for s in plan.standards]
    expected = derive_security_plan(plan, packs=packs, readiness=readiness)
    for index, standard in enumerate(plan.standards):
        label = f"standards[{index}]"
        if keys.count((standard.pack_id, standard.edition)) > 1:
            errors.append(f"{label}: repeats a selected pack edition")
            continue
        pack = packs.get((standard.pack_id, standard.edition))
        if pack is None:
            errors.append(f"{label}: pack is unavailable")
            continue
        try:
            validate_pack_effectivity(pack, now, standard.period)
        except ValueError as exc:
            errors.append(f"{label}.period: {exc}")
        if set(standard.requirement_choices) - {r.id for r in pack.requirements}:
            errors.append(f"{label}.requirementChoices: names an unknown requirement")
        derived = expected.standards[index]
        for field in ("title", "distribution"):
            if getattr(standard, field) != getattr(derived, field):
                errors.append(f"{label}.{field}: differs from the selected pack")
    jobs = {job.id: job for job in plan.jobs}
    if len(jobs) != len(plan.jobs):
        errors.append("jobs: duplicate job ID")
    for index, job in enumerate(plan.jobs):
        label = f"jobs[{index}]"
        try:
            native_job(job.kind, job.input)
        except SecurityPlanError as exc:
            prefix = (
                label
                if exc.diagnostics == ["kind: unknown job kind"]
                else f"{label}.input"
            )
            errors.extend(f"{prefix}.{item}" for item in exc.diagnostics)
            continue
        derived = expected.jobs[index]
        for field in ("authority", "readiness"):
            if getattr(job, field) != getattr(derived, field):
                errors.append(f"{label}.{field}: differs from the derived {field}")
        for asset_index, asset in enumerate(job.assets):
            if asset not in scope.assets or asset in scope.exclusions:
                errors.append(
                    f"{label}.assets[{asset_index}]: outside the plan subject"
                )
        for target_index, target in enumerate(derived.authority.targets):
            if not _target_within(target, snapshot):
                errors.append(
                    f"{label}.authority.targets[{target_index}]: outside the saved environment's candidates"
                )
    seen = {}
    for index, row in enumerate(plan.requirements):
        label = f"requirements[{index}]"
        key = (row.pack_id, row.edition, row.id)
        if key in seen:
            errors.append(f"{label}: duplicates requirements[{seen[key]}]")
            continue
        seen[key] = index
        pack = packs.get((row.pack_id, row.edition))
        if (row.pack_id, row.edition) not in keys or pack is None:
            errors.append(f"{label}.packId: names no selected standard")
            continue
        requirement = next((r for r in pack.requirements if r.id == row.id), None)
        if requirement is None:
            errors.append(f"{label}.id: not a requirement of the selected standard")
            continue
        derived = expected.requirements[index]
        if (
            plan.subject.type == "network"
            and not selected_for_subject(requirement, plan.subject.type)
            and row.applicability != "not_applicable"
        ):
            errors.append(
                f"{label}.applicability: the standard does not tag this requirement "
                "for a network subject; mark it not applicable"
            )
        for field, alias in (
            ("title", "title"),
            ("allowed_methods", "allowedMethods"),
            ("external_party", "externalParty"),
            ("evidence_request_ids", "evidenceRequestIds"),
        ):
            if getattr(row, field) != getattr(derived, field):
                errors.append(f"{label}.{alias}: differs from the selected pack")
        errors.extend(_proposal_errors(plan, row, derived, requirement, label, jobs))
    for index, standard in enumerate(plan.standards):
        pack = packs.get((standard.pack_id, standard.edition))
        for requirement in pack.requirements if pack else ():
            if (standard.pack_id, standard.edition, requirement.id) not in seen:
                errors.append(
                    f"requirements: omits {standard.pack_id} {standard.edition} requirement {requirement.id}"
                )
    for index, fact in enumerate(plan.regulatory_facts):
        if fact.status != "undetermined" and not fact.cited():
            errors.append(
                f"regulatoryFacts[{index}].status: stays undetermined without a cited operator record"
            )
    if plan.methodology is not None:
        errors.extend(_methodology_errors(plan, packs, now))
    if errors:
        raise SecurityPlanError(errors)
    return plan


def _proposal_errors(plan, row, derived, requirement, label, jobs):
    errors = []
    allowed = set(derived.allowed_methods)
    for index, method in enumerate(row.methods):
        if method not in allowed:
            errors.append(f"{label}.methods[{index}]: not allowed by the selected pack")
    standard = next(
        s
        for s in plan.standards
        if (s.pack_id, s.edition) == (row.pack_id, row.edition)
    )
    choice = _choice(standard, row.id)
    explicit = (
        "not_applicable" if choice.not_applicable_reason else choice.applicability
    )
    if explicit != "undetermined" and explicit != row.applicability:
        errors.append(f"{label}.applicability: conflicts with the requirement choice")
    for index, job_id in enumerate(row.job_ids):
        if job_id not in jobs:
            errors.append(f"{label}.jobIds[{index}]: names no planned job")
    if row.job_ids and "deterministic_check" not in row.methods:
        # External-only and organisational requirements are never a scan.
        errors.append(f"{label}.jobIds: jobs evidence only deterministic checks")
    pack_procedures = {p.id: p for p in requirement.procedures}
    for index, selected in enumerate(row.procedures):
        procedure = pack_procedures.get(selected.id)
        if procedure is None:
            errors.append(
                f"{label}.procedures[{index}]: not a procedure of this requirement"
            )
            continue
        if procedure_evidence(procedure.method)[0] not in row.methods:
            errors.append(
                f"{label}.procedures[{index}]: its method is not selected for this requirement"
            )
    if row.procedures and plan.methodology is None:
        errors.append(f"{label}.procedures: procedures need the plan's methodology")
    # A network plan's analyzed exports are reviewed with the security-review
    # Skill's network procedures, an agent-interpretation evidence path.
    network_review = (
        plan.subject.type == "network"
        and "agent_interpretation" in row.methods
        and any(job.kind == "network-config" for job in plan.jobs)
    )
    has_path = bool(
        row.job_ids
        or row.procedures
        or (row.evidence_request_ids and "document_review" in row.methods)
        or "external_only" in row.methods
        or network_review
    )
    if row.applicability != "not_applicable" and not (
        (row.methods and has_path) or row.gaps
    ):
        errors.append(f"{label}: needs a planned evidence path or an explicit gap")
    present = {_gap_key(g) for g in row.gaps}
    for gap in derived.gaps:
        if _gap_key(gap) not in present:
            errors.append(f"{label}.gaps: omits a required {gap.kind} gap")
    for index, gap in enumerate(row.gaps):
        if gap.readiness_code is None:
            if gap.kind in {
                "tool_unavailable",
                "feed_unavailable",
                "identity_unavailable",
            }:
                errors.append(
                    f"{label}.gaps[{index}].readinessCode: required for this gap"
                )
            elif gap.next_action is not None:
                errors.append(
                    f"{label}.gaps[{index}].nextAction: comes from a readiness code"
                )
            continue
        if gap.readiness_code not in READINESS_ACTIONS:
            errors.append(
                f"{label}.gaps[{index}].readinessCode: unknown readiness diagnostic"
            )
        elif gap.kind != READINESS_GAPS[gap.readiness_code]:
            errors.append(
                f"{label}.gaps[{index}].kind: differs from its readiness code"
            )
        elif gap.next_action != READINESS_ACTIONS[gap.readiness_code]:
            errors.append(
                f"{label}.gaps[{index}].nextAction: differs from the readiness diagnostic"
            )
        if gap.job_id is not None and gap.job_id not in row.job_ids:
            errors.append(
                f"{label}.gaps[{index}].jobId: names no job of this requirement"
            )
    return errors


def _methodology_errors(plan, packs, now):
    from moonmind.security.web_api_methodology import validate_plan

    selected = plan.methodology
    if (selected.pack_id, selected.edition) not in {
        (s.pack_id, s.edition) for s in plan.standards
    }:
        return ["methodology.packId: names no selected standard"]
    pack = packs[(selected.pack_id, selected.edition)]
    if pack.distribution != "redistributable":
        return [
            "methodology.packId: portable methodology requires redistributable procedures"
        ]
    if len({i.id for i in selected.identities}) != len(selected.identities):
        return ["methodology.identities: duplicate test identity"]
    try:
        validate_plan(plan, pack, now=now)
    except ValueError as exc:
        return [f"methodology: {exc}"]
    return []


def gap_list(plan: SecurityPlan) -> list[dict]:
    """Every requirement gap, with the readiness next action where one applies."""
    return [
        {
            "packId": row.pack_id,
            "edition": row.edition,
            "requirementId": row.id,
            **gap.model_dump(mode="json", by_alias=True),
        }
        for row in plan.requirements
        for gap in row.gaps
    ]


def _safe(value) -> str:
    value = html.escape(str(value).replace("\n", " ").replace("\r", " "))
    return re.sub(r"([\\`*_[\]{}()#!|>])", r"\\\1", value)


def _authority_text(authority: JobAuthority | None) -> str:
    if authority is None:
        return "unavailable"
    targets = ", ".join(
        f"{target.kind} {target.ref}"
        + (f" ({', '.join(target.names)})" if target.names else "")
        + (f" regions {', '.join(target.regions)}" if target.regions else "")
        for target in authority.targets
    )
    parts = [
        "scoped assessment" if authority.scoped else "unscoped job",
        f"targets: {targets or 'none'}",
        f"profiles: {', '.join(authority.profile_refs) or 'none'}",
        f"action classes: {', '.join(authority.action_classes) or 'none'}",
        f"credential purposes: {', '.join(authority.credential_purposes) or 'none'}",
        f"egress: {', '.join(authority.network_refs) or 'none'}",
        f"budget: {authority.max_job_seconds}s",
    ]
    if authority.max_requests_per_second is not None:
        parts.append(f"{authority.max_requests_per_second} requests/s")
    return "; ".join(parts)


def _network_sections(plan: SecurityPlan) -> list[str]:
    """Planned checks, review procedures and the inputs a network plan needs."""
    from moonmind.security.network_configs import (
        PROVENANCE,
        REQUIRED_INPUTS,
        REVIEW_PROCEDURES,
        RULES,
    )

    jobs = [job for job in plan.jobs if job.kind == "network-config"]
    lines = ["", "### Planned checks"]
    if jobs:
        lines += [
            f"- {rule}: {text} (job {', '.join(_safe(job.id) for job in jobs)})"
            for rule, text in RULES.items()
        ]
        lines.append(
            "- Every export's vendor, operating system, version and parse coverage; "
            "unsupported or unparsed content is a coverage gap, never clean."
        )
    else:
        lines.append("- None: no network-config job is planned.")
    lines += ["", "### Review procedures"]
    lines += [f"- {name}: {text}" for name, text in REVIEW_PROCEDURES.items()]
    lines.append(
        "- Each review result is an agent interpretation citing file and line; "
        "it never satisfies a requirement that needs a check or a record."
    )
    lines += ["", "### Required inputs"]
    lines += [f"- {text}" for text in REQUIRED_INPUTS]
    exports = [
        export
        for job in jobs
        for export in (job.input.get("exports") or [])
        if isinstance(export, dict)
    ]
    lines += [
        f"- Export {_safe(e.get('fileName'))} ({_safe(e.get('artifactRef'))}): device "
        f"{_safe(e.get('device'))}, exported {_safe(e.get('exportedAt'))}, supplied by "
        f"{_safe(e.get('supplier'))}."
        for e in exports
    ] or ["- No export is planned yet; see the open points."]
    lines.append(f"- {PROVENANCE}")
    return lines


def render_security_plan(plan: SecurityPlan, *, plan_ref: str | None = None) -> str:
    """A readable plan and its gap list. It describes work; it grants nothing."""
    subject, project = plan.subject, plan.subject.project
    lines = [
        "",
        "## Security plan",
        "",
        f"Plan: {_safe(plan_ref or 'not retained')}; identity {plan_identity(plan)}.",
        "A plan grants nothing. Launched work still needs a saved authorization and the rules-of-engagement review.",
        "",
        f"Subject: {_safe(subject.type)} {_safe(subject.candidate_ref)}, drafted from project "
        f"{_safe(project.project_id)} / {_safe(project.environment_id)} "
        f"(saved version {project.source_version}, snapshot {project.digest}).",
        f"System boundary: {_safe(subject.system_boundary)}",
        f"Assets: {_safe(', '.join(subject.scope.assets))}. "
        f"Exclusions: {_safe(', '.join(subject.scope.exclusions) or 'none')}.",
        "",
        "### Standards",
    ]
    for standard in plan.standards:
        lines.append(
            f"- {_safe(standard.pack_id)} {_safe(standard.edition)} revision "
            f"{_safe(standard.revision)} (sha256 {standard.sha256}); period "
            f"{standard.period.start} to {standard.period.end}"
            + (f"; {_safe(standard.title)}" if standard.title else "")
            + (
                f"; proposed inputs to review: {_safe(', '.join(sorted(standard.profile)))}"
                if standard.profile
                else ""
            )
        )
    counts = {
        state: sum(row.applicability == state for row in plan.requirements)
        for state in ("applicable", "undetermined", "not_applicable")
    }
    lines += [
        "",
        "### Requirements",
        "",
        f"{len(plan.requirements)} requirements: {counts['applicable']} applicable, "
        f"{counts['undetermined']} undetermined, {counts['not_applicable']} not applicable.",
    ]
    for row in plan.requirements:
        paths = [
            *(f"job {job}" for job in row.job_ids),
            *(f"procedure {p.id}" for p in row.procedures),
            *(
                f"evidence request {item}"
                for item in row.evidence_request_ids
                if "document_review" in row.methods
            ),
        ]
        if "external_only" in row.methods:
            paths.append(f"external: {row.external_party or 'outside party'}")
        lines.append(
            f"- {_safe(row.pack_id)} {_safe(row.id)} — {row.applicability}; "
            f"methods: {', '.join(row.methods) or 'none'}; "
            f"evidence: {_safe(', '.join(paths) or 'none')}; "
            f"reason: {_safe(row.reason)}"
            + (f" ({len(row.gaps)} gaps)" if row.gaps else "")
        )
    lines += ["", "### Jobs"]
    for job in plan.jobs:
        readiness = job.readiness.status if job.readiness else "unverified"
        lines.append(
            f"- {_safe(job.id)} ({_safe(job.kind)}) for {_safe(', '.join(job.assets))}; "
            f"readiness {readiness}; requires {_safe(_authority_text(job.authority))}"
        )
    if not plan.jobs:
        lines.append("- No planned jobs.")
    if subject.type == "network":
        lines += _network_sections(plan)
    lines += ["", "### Open points"]
    lines += [f"- {point.kind}: {_safe(point.text)}" for point in plan.open_points]
    if not plan.open_points:
        lines.append("- None recorded.")
    if plan.regulatory_facts:
        lines += ["", "### Regulatory facts"]
        lines += [
            f"- {fact.kind}: {_safe(fact.subject)} — {fact.status}"
            + (f" (record {fact.record_ref})" if fact.record_ref else "")
            for fact in plan.regulatory_facts
        ]
    gaps = gap_list(plan)
    lines += ["", "### Gap list"]
    lines += [
        f"- {_safe(gap['packId'])} {_safe(gap['requirementId'])}: {gap['kind']} — "
        f"{_safe(gap['detail'])}"
        + (f" Next action: {_safe(gap['nextAction'])}" if gap.get("nextAction") else "")
        for gap in gaps
    ]
    if not gaps:
        lines.append("- No gaps recorded.")
    return "\n".join(lines) + "\n"
