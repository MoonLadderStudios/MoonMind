"""Deployment-controlled workload profiles and admitted assessment scope.

A workload profile is the smallest approved-invocation template the existing
Container Job admission and launch path can enforce: the deployment binds the
image source, invocation modes with typed arguments, effective configuration,
resource ceilings, inputs and outputs. Callers choose a mode and argument
values; they never supply the image, entrypoint, argv, environment, caches or
network selection of a profile job. Admission and the worker render the same
selection and compare the concrete launch content, so a stale or edited request
cannot run under a profile label it no longer matches.

An admitted assessment scope is resolved from operator authority at the
trusted launch boundary and signed into the session's container-job
capability. A scoped session can submit only profile jobs and web
assessments inside that scope; generic image, entrypoint and
repository-scanner submissions are refused. Web assessments run only inside a
scope, since reaching a target does not authorize acting on it. Other
sessions keep their ordinary development flexibility.

A profile declares credential slots (purpose, delivery, issuer); a scope's
credential grants say which opaque references may fill them, for which
account, profiles and lifetime. Requests name references, never values.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.schemas.repository_scanner_models import (
    WebRoute,
    route_within,
    web_origin,
    web_route,
)

WORKLOAD_PROFILES_ENV_KEY = "MOONMIND_CONTAINER_BACKEND_WORKLOAD_PROFILES"

_REF = r"^[a-z0-9][a-z0-9.-]{0,127}$"
_ARGUMENT_NAME = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"
_PLACEHOLDER = re.compile(r"\{([A-Za-z][A-Za-z0-9_]{0,63})\}")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_INPUT_VALUE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}(?:/[A-Za-z0-9._@+-]+)*$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _relative_path(value: str) -> str:
    if value == ".":
        return value
    parts = value.split("/")
    if value.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("path must be '.' or a normalized relative path")
    return value


def _within(path: str, root: str) -> bool:
    return root == "." or path == root or path.startswith(root + "/")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class ProfileArgument(_Model):
    """One typed caller-selected value substituted into an argv template.

    Values replace ``{name}`` placeholders inside argv entries; they never pass
    through a shell. ``input`` values name a captured input attachment (or a
    path inside one) and render as ``/inputs/...``, so a target is always an
    admitted snapshot rather than an arbitrary container path.
    """

    name: str = Field(pattern=_ARGUMENT_NAME)
    type: Literal["string", "integer", "enum", "input"]
    pattern: str | None = Field(None, max_length=512)
    values: tuple[str, ...] | None = Field(None, min_length=1, max_length=64)
    minimum: int | None = None
    maximum: int | None = None
    required: bool = False

    @model_validator(mode="after")
    def _constraint_matches_type(self) -> "ProfileArgument":
        if (self.type == "string") != (self.pattern is not None):
            raise ValueError("string arguments require exactly one pattern")
        if self.pattern is not None:
            re.compile(self.pattern)
        if (self.type == "enum") != (self.values is not None):
            raise ValueError("enum arguments require explicit values")
        if self.type != "integer" and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("bounds apply only to integer arguments")
        return self

    def render(self, value: Any, *, inputs: frozenset[str]) -> str:
        if self.type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"argument {self.name} must be an integer")
            if (self.minimum is not None and value < self.minimum) or (
                self.maximum is not None and value > self.maximum
            ):
                raise ValueError(f"argument {self.name} is outside its bounds")
            return str(value)
        if not isinstance(value, str) or not value or _CONTROL.search(value):
            raise ValueError(f"argument {self.name} must be a plain string")
        if self.type == "enum":
            if value not in (self.values or ()):
                raise ValueError(f"argument {self.name} is not an approved value")
            return value
        if self.type == "input":
            if (
                not _INPUT_VALUE.fullmatch(value)
                or ".." in value.split("/")
                or value.split("/", 1)[0] not in inputs
            ):
                raise ValueError(f"argument {self.name} must name a captured input")
            return f"/inputs/{value}"
        if not re.fullmatch(self.pattern or "", value):
            raise ValueError(f"argument {self.name} does not match its pattern")
        return value


class InvocationMode(_Model):
    """One approved operation with its action class and argv template.

    An argv entry referencing an omitted optional argument is dropped, so one
    mode can admit optional flags without a separate frozen command each.
    """

    name: str = Field(pattern=_REF)
    action_class: str = Field(alias="actionClass", pattern=_REF)
    argv: tuple[str, ...] = Field(min_length=1, max_length=128)
    arguments: tuple[ProfileArgument, ...] = Field((), max_length=32)

    @model_validator(mode="after")
    def _placeholders_are_declared(self) -> "InvocationMode":
        names = [argument.name for argument in self.arguments]
        if len(set(names)) != len(names):
            raise ValueError("mode argument names must be unique")
        for entry in self.argv:
            if not entry or len(entry) > 4096:
                raise ValueError("argv entries must contain 1..4096 characters")
            for name in _PLACEHOLDER.findall(entry):
                if name not in names:
                    raise ValueError("argv references an undeclared argument")
        return self


class ConfigurationException(_Model):
    """A named, deployment-defined relaxation of the profile's configuration.

    Repository rules, ignore files, inline suppressions and similar
    repository-supplied configuration stay disabled by the mode's argv unless
    the request names an exception the profile defines and, for a scoped
    assessment, the scope admits. Its argv entries are fixed text appended to
    the invocation, so it cannot select another mode, target or credential.
    """

    name: str = Field(pattern=_REF)
    argv: tuple[str, ...] = Field(min_length=1, max_length=16)

    @field_validator("argv")
    @classmethod
    def _fixed_text(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not item or len(item) > 4096 or _PLACEHOLDER.search(item) for item in value
        ):
            raise ValueError("configuration exception argv must be fixed text")
        return value


class ProfileCache(_Model):
    cache_ref: str = Field(alias="cacheRef", min_length=1, max_length=255)
    target: str = Field(pattern=r"^/[A-Za-z0-9._@+-]+(?:/[A-Za-z0-9._@+-]+)*$")


class ProfileOutput(_Model):
    name: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(alias="relativePath", min_length=1, max_length=1000)


#: Distinct workload credential uses. Private target-registry access is the
#: separate ``imageAssessment.targetCredentialRef`` path; model credentials stay
#: with Provider Profiles. A grant for one purpose never fills another's slot.
CredentialPurpose = Literal["cloud_inventory", "application_auth"]
#: Container directory of file-delivered workload credentials.
WORKLOAD_CREDENTIALS_TARGET = "/run/moonmind-credentials"


class ProfileCredential(_Model):
    """One credential slot a profile's tool can consume.

    ``static`` delivers the stored value; ``aws-sts`` exchanges the stored
    source key for a short-lived session written as an AWS credentials file at
    ``/run/moonmind-credentials/<name>``, which the worker names in
    ``AWS_SHARED_CREDENTIALS_FILE``. Values never enter argv.
    """

    name: str = Field(pattern=_REF)
    purpose: CredentialPurpose
    delivery: Literal["env", "file"]
    environment_name: str | None = Field(None, alias="environmentName")
    issuer: Literal["static", "aws-sts"] = "static"

    @model_validator(mode="after")
    def _consistent(self) -> "ProfileCredential":
        if (self.delivery == "env") != (self.environment_name is not None):
            raise ValueError("env delivery, and only it, names an environment variable")
        if self.environment_name is not None and not _ENV_NAME.fullmatch(
            self.environment_name
        ):
            raise ValueError("credential environment names must be plain identifiers")
        if (self.purpose == "cloud_inventory") != (self.issuer == "aws-sts"):
            # Cloud inventory uses short-lived sessions, never a stored key.
            raise ValueError("cloud inventory credentials, and only they, use aws-sts")
        if self.issuer == "aws-sts" and self.delivery != "file":
            raise ValueError("issued cloud sessions are delivered as files")
        return self


class WorkloadProfile(_Model):
    profile_ref: str = Field(alias="profileRef", pattern=_REF)
    image_source_ref: str = Field(alias="imageSourceRef", pattern=_REF)
    entrypoint: tuple[str, ...] = Field((), max_length=32)
    modes: tuple[InvocationMode, ...] = Field(min_length=1, max_length=32)
    environment: dict[str, str] = Field(default_factory=dict, max_length=64)
    caches: tuple[ProfileCache, ...] = Field((), max_length=16)
    configuration_exceptions: tuple[ConfigurationException, ...] = Field(
        (), alias="configurationExceptions", max_length=16
    )
    network_mode: Literal["none", "bridge"] = Field("none", alias="networkMode")
    egress_policy_ref: str | None = Field(None, alias="egressPolicyRef", pattern=_REF)
    outputs: tuple[ProfileOutput, ...] = Field((), max_length=64)
    credentials: tuple[ProfileCredential, ...] = Field((), max_length=8)
    max_cpu_millis: int = Field(alias="maxCpuMillis", ge=1, le=128000)
    max_memory_mib: int = Field(alias="maxMemoryMiB", ge=16, le=1048576)
    max_timeout_seconds: int = Field(alias="maxTimeoutSeconds", ge=1, le=86400)

    @model_validator(mode="after")
    def _unambiguous(self) -> "WorkloadProfile":
        if len({mode.name for mode in self.modes}) != len(self.modes):
            raise ValueError("profile mode names must be unique")
        if len({item.name for item in self.configuration_exceptions}) != len(
            self.configuration_exceptions
        ):
            raise ValueError("configuration exception names must be unique")
        if any(not _ENV_NAME.fullmatch(name) for name in self.environment):
            raise ValueError("profile environment names must be plain identifiers")
        if (self.network_mode == "bridge") != (self.egress_policy_ref is not None):
            # Docker bridge alone is not an egress boundary.
            raise ValueError("bridge networking requires an egress policy")
        if len({item.name for item in self.credentials}) != len(self.credentials):
            raise ValueError("profile credential names must be unique")
        if sum(item.issuer == "aws-sts" for item in self.credentials) > 1:
            # The worker points AWS_SHARED_CREDENTIALS_FILE at the one session.
            raise ValueError("a profile declares at most one issued cloud session")
        names = [i.environment_name for i in self.credentials if i.environment_name]
        if len(set(names)) != len(names) or set(names) & set(self.environment):
            raise ValueError("credential environment names must be distinct")
        return self

    def credential(self, name: str) -> ProfileCredential:
        for item in self.credentials:
            if item.name == name:
                return item
        raise ValueError("workload credential slot is not declared by the profile")

    def mode(self, name: str) -> InvocationMode:
        for mode in self.modes:
            if mode.name == name:
                return mode
        raise ValueError("workload profile mode is not deployment-authorized")


def parse_workload_profiles(raw: str | None) -> tuple[WorkloadProfile, ...]:
    if not raw or not raw.strip():
        return ()
    try:
        rows = json.loads(raw)
        if not isinstance(rows, list) or len(rows) > 64:
            raise ValueError("bounded profile array required")
        profiles = tuple(WorkloadProfile.model_validate(row) for row in rows)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"{WORKLOAD_PROFILES_ENV_KEY} is not a valid workload profile array"
        ) from exc
    if len({p.profile_ref for p in profiles}) != len(profiles):
        raise ValueError(f"{WORKLOAD_PROFILES_ENV_KEY} repeats a profileRef")
    return profiles


def _profile(profiles, profile_ref: str) -> WorkloadProfile:
    for profile in profiles:
        if profile.profile_ref == profile_ref:
            return profile
    raise ValueError("workload profile is not deployment-authorized")


#: Spec fields a profile owns. A request leaves them at their defaults or
#: carries exactly the rendered content (an exact replay of the resolved job).
_PROFILE_OWNED = (
    "image",
    "image_source_ref",
    "entrypoint",
    "command",
    "workdir",
    "environment",
    "caches",
    "network_mode",
    "service_ref",
    "egress_policy_ref",
    "pull_policy",
    "registry_credential_ref",
    "outputs",
)


def resolve_profile_job(spec, profiles):
    """Return ``spec`` with its profile's concrete launch content.

    Raises ``ValueError`` when the selection is not deployment-authorized, an
    argument violates its constraint, a resource or timeout exceeds the
    profile, or any profile-owned field differs from both its default and the
    rendering. Admission persists the result; the worker calls this again and
    requires the persisted content to equal its own rendering.
    """

    selection = spec.workload_profile
    if selection is None:
        return spec
    profile = _profile(profiles, selection.profile_ref)
    mode = profile.mode(selection.mode)
    inputs = frozenset(item.name for item in spec.input_attachments or ())
    if not inputs:
        raise ValueError("workload profile jobs read captured input attachments")
    declared = {argument.name: argument for argument in mode.arguments}
    if set(selection.arguments) - set(declared):
        raise ValueError("workload profile argument is not declared by the mode")
    rendered: dict[str, str] = {}
    for name, argument in declared.items():
        if name in selection.arguments:
            rendered[name] = argument.render(selection.arguments[name], inputs=inputs)
        elif argument.required:
            raise ValueError(f"argument {name} is required")
    command: list[str] = []
    for entry in mode.argv:
        names = _PLACEHOLDER.findall(entry)
        if any(name not in rendered for name in names):
            continue
        command.append(_PLACEHOLDER.sub(lambda match: rendered[match.group(1)], entry))
    exceptions = {item.name: item for item in profile.configuration_exceptions}
    if len(set(selection.configuration_exceptions)) != len(
        selection.configuration_exceptions
    ):
        raise ValueError("configuration exceptions must be unique")
    for name in selection.configuration_exceptions:
        if name not in exceptions:
            raise ValueError("configuration exception is not defined by the profile")
        command.extend(exceptions[name].argv)
    selected = selection.credentials or ()
    if len({item.slot for item in selected}) != len(selected):
        raise ValueError("workload credential slots must be unique")
    credential_environment = []
    for item in selected:
        slot = profile.credential(item.slot)
        if slot.delivery == "env":
            credential_environment.append(
                {"name": slot.environment_name, "secretRef": item.credential_ref}
            )
    resources = spec.resources
    if (
        resources.gpu is not None
        or resources.cpu_millis > profile.max_cpu_millis
        or resources.memory_mib > profile.max_memory_mib
    ):
        raise ValueError("workload resources exceed the profile")
    if spec.timeout_seconds > profile.max_timeout_seconds:
        raise ValueError("workload timeout exceeds the profile")
    if spec.workspace_read_only is False:
        raise ValueError("workload profile jobs cannot write the workspace")
    field_info = type(spec).model_fields
    defaults = {
        name: field_info[name].get_default(call_default_factory=True)
        for name in _PROFILE_OWNED
    }
    concrete = {
        **defaults,
        "image_source_ref": profile.image_source_ref,
        "entrypoint": list(profile.entrypoint),
        "command": command,
        "environment": [
            {"name": name, "value": value}
            for name, value in sorted(profile.environment.items())
        ]
        + sorted(credential_environment, key=lambda item: item["name"]),
        "caches": [
            {"cacheRef": cache.cache_ref, "target": cache.target, "readOnly": True}
            for cache in profile.caches
        ],
        "network_mode": profile.network_mode,
        "egress_policy_ref": profile.egress_policy_ref,
        "outputs": [item.model_dump(by_alias=True) for item in profile.outputs],
    }
    resolved = type(spec).model_validate(
        {
            **spec.model_dump(mode="json", by_alias=True, exclude=set(_PROFILE_OWNED)),
            **{
                field_info[name].alias or name: value
                for name, value in concrete.items()
            },
            "workspaceReadOnly": True,
        }
    )

    def owned(value) -> dict:
        return value.model_dump(mode="json", by_alias=True, include=set(_PROFILE_OWNED))

    current = owned(spec)
    blank = owned(spec.model_copy(update=defaults))
    if current != blank and current != owned(resolved):
        raise ValueError("workload profile jobs cannot override profile launch content")
    return resolved


def validate_profile_launch(spec, profiles) -> None:
    """Worker-side recheck: persisted content must equal the current rendering.

    A profile removed or changed after admission no longer authorizes the job;
    the worker refuses instead of launching the old content or choosing
    another profile.
    """

    if spec.workload_profile is None:
        return
    if spec.image_source_ref is None:
        raise ValueError("workload profile job was not resolved at admission")
    resolve_profile_job(spec, profiles)


#: The typed web workload's name in scope ``profileRefs`` and credential
#: grants; it has no deployment profile of its own.
WEB_ASSESSMENT_PROFILE_REF = "web-assessment"


class AssessmentTarget(_Model):
    """One admitted input path, input artifact or web origin.

    A web origin carries the route prefixes and methods that may be tested
    and the prefixes that may not; jobs stay within all of them.
    """

    workspace_path: str | None = Field(None, alias="workspacePath", max_length=1000)
    artifact_ref: str | None = Field(
        None, alias="artifactRef", pattern=r"^art_[0-9A-Za-z]{1,64}$"
    )
    web_origin: str | None = Field(None, alias="webOrigin", max_length=300)
    web_routes: tuple[WebRoute, ...] = Field((), alias="webRoutes", max_length=64)
    web_exclusions: tuple[str, ...] = Field((), alias="webExclusions", max_length=64)

    _normalized = field_validator("workspace_path")(
        lambda value: None if value is None else _relative_path(value)
    )
    _origin = field_validator("web_origin")(
        lambda value: None if value is None else web_origin(value)
    )
    _excluded = field_validator("web_exclusions")(
        lambda value: tuple(web_route(item) for item in value)
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> "AssessmentTarget":
        named = [self.workspace_path, self.artifact_ref, self.web_origin]
        if sum(item is not None for item in named) != 1:
            raise ValueError(
                "exactly one of workspacePath, artifactRef or webOrigin is required"
            )
        if (self.web_origin is not None) != bool(self.web_routes):
            raise ValueError("web origins, and only they, admit routes")
        if self.web_exclusions and self.web_origin is None:
            raise ValueError("web exclusions belong to a web origin")
        return self


_AWS_ACCOUNT = re.compile(r"^[0-9]{12}$")
_ROLE_ARN = re.compile(r"^arn:aws:iam::([0-9]{12}):role/[A-Za-z0-9+=,.@_/-]{1,512}$")


class CredentialGrant(_Model):
    """Operator admission of one workload credential reference.

    The reference may fill a slot of ``purpose`` in the listed profiles, for
    ``account`` only, until ``expiresAt``. ``revision`` pins a
    managed secret revision, so a rotated or revoked secret fails closed.
    Cloud grants name the role to assume in that account; issued sessions are
    limited to read-only inspection policies. Active or remediation access
    needs a scope of its own and is not expressible here.
    """

    # Scope keys avoid "credential": step policies refuse such keys outright.
    ref: str = Field(min_length=4, max_length=1024)
    purpose: CredentialPurpose
    account: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
    profile_refs: tuple[str, ...] = Field(
        alias="profileRefs", min_length=1, max_length=32
    )
    expires_at: datetime = Field(alias="expiresAt")
    revision: int | None = Field(None, ge=1)
    role_arn: str | None = Field(None, alias="roleArn", max_length=600)
    max_session_seconds: int = Field(3600, alias="maxSessionSeconds", ge=900, le=43200)
    access_level: Literal["read_only"] = Field("read_only", alias="accessLevel")

    @model_validator(mode="after")
    def _bound(self) -> "CredentialGrant":
        if "://" not in self.ref:
            raise ValueError("credential grants name an opaque secret reference")
        if self.expires_at.tzinfo is None:
            raise ValueError("credential grant expiry must include a timezone")
        if any(not re.fullmatch(_REF, item) for item in self.profile_refs):
            raise ValueError("scope references must be explicit refs")
        cloud = self.purpose == "cloud_inventory"
        if cloud != (self.role_arn is not None):
            raise ValueError("cloud inventory grants, and only they, name a role")
        if cloud:
            match = _ROLE_ARN.fullmatch(self.role_arn or "")
            if not _AWS_ACCOUNT.fullmatch(self.account) or match is None:
                raise ValueError("cloud grants name an AWS account and role ARN")
            if match.group(1) != self.account:
                raise ValueError("the granted role belongs to another account")
        return self


class AdmittedAssessmentScope(_Model):
    """Compact operator-admitted authority for one assessment's container jobs.

    It is resolved at the trusted launch boundary and travels only inside the
    signed session capability; repository text and model output cannot alter
    it. ``maxJobs`` and ``maxJobSeconds`` bound new jobs; an exact replay of an
    already-admitted job consumes no budget. ``networkRefs`` names admitted
    egress policies. ``grants`` are the only workload credential references
    its jobs may request.
    """

    assessment_ref: str = Field(alias="assessmentRef", pattern=_REF)
    profile_refs: tuple[str, ...] = Field(
        alias="profileRefs", min_length=1, max_length=32
    )
    action_classes: tuple[str, ...] = Field(
        alias="actionClasses", min_length=1, max_length=32
    )
    targets: tuple[AssessmentTarget, ...] = Field(min_length=1, max_length=32)
    exclusions: tuple[str, ...] = Field((), max_length=64)
    configuration_exceptions: tuple[str, ...] = Field(
        (), alias="configurationExceptions", max_length=16
    )
    network_refs: tuple[str, ...] = Field((), alias="networkRefs", max_length=16)
    max_jobs: int = Field(alias="maxJobs", ge=1, le=1000)
    max_job_seconds: int = Field(alias="maxJobSeconds", ge=1, le=86400)
    evidence_intent: str = Field(alias="evidenceIntent", min_length=1, max_length=500)
    grants: tuple[CredentialGrant, ...] = Field((), max_length=16)

    @field_validator("exclusions")
    @classmethod
    def _normalized_exclusions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(_relative_path(item) == "." for item in value):
            raise ValueError("an exclusion cannot cover the whole workspace")
        return value

    @field_validator(
        "profile_refs", "action_classes", "configuration_exceptions", "network_refs"
    )
    @classmethod
    def _refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.fullmatch(_REF, item) for item in value):
            raise ValueError("scope references must be explicit refs")
        return value


def selection_within_scope(spec, scope: AdmittedAssessmentScope) -> bool:
    """Request-level scope check shared by submission and persisted-job reads."""

    if getattr(spec, "web_assessment", None) is not None:
        try:
            web_assessment_within_scope(spec, scope)
        except PermissionError:
            return False
        return True
    selection = spec.workload_profile
    if (
        selection is None
        or spec.repository_scan is not None
        or selection.assessment_ref != scope.assessment_ref
        or selection.profile_ref not in scope.profile_refs
        or not set(selection.configuration_exceptions)
        <= set(scope.configuration_exceptions)
        or not spec.input_attachments
    ):
        return False
    paths = [t.workspace_path for t in scope.targets if t.workspace_path is not None]
    artifacts = {t.artifact_ref for t in scope.targets if t.artifact_ref is not None}
    for item in spec.input_attachments:
        if item.artifact_ref is not None:
            if item.artifact_ref not in artifacts:
                return False
            continue
        path = item.workspace_path or "."
        if not any(_within(path, root) for root in paths):
            return False
        # A capture cannot omit part of its tree, so an input that contains an
        # exclusion is as out of scope as one inside it.
        if any(
            _within(path, excluded) or _within(excluded, path)
            for excluded in scope.exclusions
        ):
            return False
    return True


def web_assessment_within_scope(spec, scope: AdmittedAssessmentScope) -> None:
    """Every origin, route, method, exclusion, limit and input is admitted.

    Network permission is necessary but not sufficient: the mode's action
    class must be admitted too, and the job may not reach past the routes,
    methods and exclusions of its origin.
    """

    web = spec.web_assessment
    if web.assessment_ref != scope.assessment_ref:
        raise PermissionError("web assessment belongs to another assessment")
    if WEB_ASSESSMENT_PROFILE_REF not in scope.profile_refs:
        raise PermissionError("web assessments are not admitted in this scope")
    if web.action_class not in scope.action_classes:
        raise PermissionError("web assessment mode action class is not admitted")
    if web.egress_policy_ref not in scope.network_refs:
        raise PermissionError("web assessment network access is not admitted")
    if spec.timeout_seconds > scope.max_job_seconds:
        raise PermissionError("web assessment timeout exceeds the assessment budget")
    targets = [t for t in scope.targets if t.web_origin == web.origin]
    if not targets:
        raise PermissionError("web origin is not an admitted target")
    target = targets[0]

    def admitted(path: str, method: str) -> bool:
        return any(
            route_within(path, r.path) and method in r.methods
            for r in target.web_routes
        )

    for route in web.routes:
        if not all(admitted(route.path, m) for m in route.methods):
            raise PermissionError("web route or method is not admitted")
        # The job must exclude at least what the scope excludes below it.
        for excluded in target.web_exclusions:
            if (
                route_within(excluded, route.path) or route_within(route.path, excluded)
            ) and not any(route_within(excluded, e) for e in web.exclusions):
                raise PermissionError("web assessment omits an admitted exclusion")
    api = web.api_definition
    artifacts = {t.artifact_ref for t in scope.targets if t.artifact_ref}
    if api is not None and api.input_artifact_ref not in artifacts:
        raise PermissionError("API definition is not an admitted input")


def resolved_job_within_scope(spec, scope: AdmittedAssessmentScope, profiles) -> None:
    """Admission check of the rendered launch content against the scope."""

    if getattr(spec, "web_assessment", None) is not None:
        web_assessment_within_scope(spec, scope)
        return
    if not selection_within_scope(spec, scope):
        raise PermissionError("container job is outside the admitted assessment scope")
    profile = _profile(profiles, spec.workload_profile.profile_ref)
    if (
        profile.mode(spec.workload_profile.mode).action_class
        not in scope.action_classes
    ):
        raise PermissionError("workload mode action class is not admitted")
    if (
        spec.egress_policy_ref is not None
        and spec.egress_policy_ref not in scope.network_refs
    ):
        raise PermissionError("workload network access is not admitted")
    if spec.registry_credential_ref:
        raise PermissionError("scoped assessment jobs cannot select pull credentials")
    if spec.timeout_seconds > scope.max_job_seconds:
        raise PermissionError("workload timeout exceeds the assessment budget")


__all__ = [
    "WEB_ASSESSMENT_PROFILE_REF",
    "WORKLOAD_CREDENTIALS_TARGET",
    "WORKLOAD_PROFILES_ENV_KEY",
    "AdmittedAssessmentScope",
    "AssessmentTarget",
    "CredentialGrant",
    "CredentialPurpose",
    "ProfileCredential",
    "WorkloadProfile",
    "parse_workload_profiles",
    "resolve_profile_job",
    "resolved_job_within_scope",
    "selection_within_scope",
    "validate_profile_launch",
    "web_assessment_within_scope",
]
