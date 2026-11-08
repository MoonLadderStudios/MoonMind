"""Reusable assessment inputs, never target, credential or network authority."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterator, Mapping
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from moonmind.schemas.agent_runtime_models import ExecutionBudget
from moonmind.schemas.repository_scanner_models import web_origin
from moonmind.security.frameworks import EvidencePeriod, FrameworkContract

SETTINGS_KEY = "security.assessment_projects"
Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)
]
Identifier = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
]
ConnectionRef = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$")
]
ArtifactRef = Annotated[str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]{1,128}$")]
CredentialRef = Annotated[
    str, StringConstraints(pattern=r"^db://[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
]
Strings = Annotated[list[Text], Field(max_length=32)]


class AssessmentBudget(ExecutionBudget):
    model_config = {"extra": "forbid", "populate_by_name": True, "frozen": True}

    @model_validator(mode="after")
    def bounded(self):
        if (
            not 0
            < self.progress_stall_seconds
            <= self.base_seconds
            <= self.max_seconds
            <= 300
        ):
            raise ValueError(
                "comparison budget must satisfy 0 < stall <= base <= max <= 300 seconds"
            )
        return self


class AssessmentRepository(FrameworkContract):
    repository: Annotated[
        str,
        StringConstraints(
            pattern=r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$", max_length=256
        ),
    ]
    connection_ref: ConnectionRef | None = None

    @field_validator("repository")
    @classmethod
    def repository_segments(cls, value):
        if any(part in {".", ".."} for part in value.split("/")):
            raise ValueError("repository candidate must name a repository")
        return value


class AssessmentCloudCandidate(FrameworkContract):
    provider: Literal["aws"] = "aws"
    account: Annotated[str, StringConstraints(pattern=r"^[0-9]{12}$")]
    regions: Annotated[
        list[
            Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(?:-[a-z]+)+-[0-9]+$")]
        ],
        Field(min_length=1, max_length=32),
    ]
    credential_ref: CredentialRef | None = None


class AssessmentFramework(FrameworkContract):
    id: Text
    edition: Text


class AssessmentEnvironment(FrameworkContract):
    id: Identifier
    name: Text
    assets: Strings = Field(default_factory=list)
    exclusions: Strings = Field(default_factory=list)
    repositories: Annotated[list[AssessmentRepository], Field(max_length=16)] = Field(
        default_factory=list
    )
    artifact_refs: Annotated[list[ArtifactRef], Field(max_length=32)] = Field(
        default_factory=list
    )
    origin_candidates: Strings = Field(default_factory=list)
    cloud_account_candidates: Annotated[
        list[AssessmentCloudCandidate], Field(max_length=16)
    ] = Field(default_factory=list)
    connection_refs: Annotated[list[ConnectionRef], Field(max_length=32)] = Field(
        default_factory=list
    )
    credential_refs: Annotated[list[CredentialRef], Field(max_length=32)] = Field(
        default_factory=list
    )
    data_classification: Literal["public", "internal", "confidential", "restricted"] = (
        "internal"
    )
    frameworks: Annotated[list[AssessmentFramework], Field(max_length=16)] = Field(
        default_factory=list
    )
    jurisdictions: Strings = Field(default_factory=list)
    period: EvidencePeriod | None = None
    budget: AssessmentBudget = Field(
        default_factory=lambda: AssessmentBudget(
            baseSeconds=60, maxSeconds=120, progressStallSeconds=60
        )
    )
    retention_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("origin_candidates")
    @classmethod
    def normalize_origins(cls, origins):
        return [web_origin(origin) for origin in origins]

    @model_validator(mode="after")
    def distinct_references(self):
        for name in (
            "assets",
            "artifact_refs",
            "origin_candidates",
            "connection_refs",
            "credential_refs",
        ):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {name}")
        return self


class AssessmentProject(FrameworkContract):
    id: Identifier
    name: Text
    purpose: Text
    boundaries: Text
    environments: Annotated[
        list[AssessmentEnvironment], Field(min_length=1, max_length=16)
    ]

    @model_validator(mode="after")
    def unique_environments(self):
        ids = [environment.id for environment in self.environments]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate environment identity")
        return self


class AssessmentProjects(FrameworkContract):
    projects: Annotated[list[AssessmentProject], Field(max_length=32)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def unique_projects(self):
        ids = [project.id for project in self.projects]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate project identity")
        return self


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


class AssessmentProjectSnapshot(FrameworkContract):
    schema_version: Literal["assessment-project/v1"] = "assessment-project/v1"
    project_id: Identifier
    project_name: Text
    environment_id: Identifier
    environment_name: Text
    source_version: int = Field(ge=0)
    purpose: Text
    boundaries: Text
    configuration: AssessmentEnvironment
    digest: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def intact(self):
        if (
            self.environment_id != self.configuration.id
            or self.environment_name != self.configuration.name
        ):
            raise ValueError("snapshot environment identity differs from configuration")
        if self.digest != _digest(
            self.model_dump(mode="json", by_alias=True, exclude={"digest"})
        ):
            raise ValueError(
                "assessment project snapshot changed; resolve the edited configuration again"
            )
        return self


def resolve_assessment_project(
    document: Any,
    *,
    project_id: str,
    environment_id: str,
    version: int,
    overrides: dict[str, Any] | None = None,
) -> AssessmentProjectSnapshot:
    projects = AssessmentProjects.model_validate(document)
    project = next((item for item in projects.projects if item.id == project_id), None)
    if project is None:
        raise ValueError(
            "Saved assessment project is unavailable; choose an existing project."
        )
    environment = next(
        (item for item in project.environments if item.id == environment_id), None
    )
    if environment is None:
        raise ValueError(
            "Saved assessment environment is unavailable; choose an existing environment."
        )
    # An override replaces its entire field, including explicit empty values.
    configuration = AssessmentEnvironment.model_validate(
        {**environment.model_dump(mode="json", by_alias=True), **(overrides or {})}
    )
    if configuration.id != environment.id:
        raise ValueError("Copy an environment in Settings to change its identity.")
    value = dict(
        schemaVersion="assessment-project/v1",
        projectId=project.id,
        projectName=project.name,
        environmentId=configuration.id,
        environmentName=configuration.name,
        sourceVersion=version,
        purpose=project.purpose,
        boundaries=project.boundaries,
        configuration=configuration.model_dump(mode="json", by_alias=True),
    )
    return AssessmentProjectSnapshot.model_validate({**value, "digest": _digest(value)})


ASSESSMENT_PROJECT_INPUT_KEYS = ("assessmentProject", "assessment_project")


def assessment_input_selectors(
    workflow: Mapping[str, Any],
) -> Iterator[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Select assessment inputs exactly where root/ordered-step admission does.

    Root Skills prefer tool then skill, with name before id. Ordered steps
    prefer their explicit Skill then the legacy tool fallback, with id before
    name. Conflicting security aliases are rejected rather than allowing an
    opaque serialized adapter to reinterpret the same selector differently.
    """
    steps = workflow.get("steps")
    nodes = (
        [
            (workflow, True),
            *((step, False) for step in steps if isinstance(step, Mapping)),
        ]
        if isinstance(steps, list)
        else [(workflow, True)]
    )
    for node, is_root in nodes:
        tool = node.get("tool") if isinstance(node.get("tool"), Mapping) else None
        skill = node.get("skill") if isinstance(node.get("skill"), Mapping) else None
        for candidate in (tool, skill):
            if candidate is None:
                continue
            aliases = {
                str(candidate.get(key) or "").strip() for key in ("id", "name")
            } - {""}
            if "security.assess" in aliases and len(aliases) > 1:
                raise ValueError(
                    "Use matching id and name values for a security.assess selector."
                )
        if is_root:
            selected = tool or skill
            keys = ("name", "id")
        else:
            selected = (
                tool
                if str(node.get("type") or "").strip().lower() == "tool"
                else (skill if skill is not None else tool)
            )
            keys = ("id", "name")
        if selected is None:
            continue
        identity = str(selected.get(keys[0]) or selected.get(keys[1]) or "").strip()
        if identity == "security.assess":
            yield node, selected


def apply_assessment_defaults(
    workflow: dict[str, Any], snapshot: AssessmentProjectSnapshot | None
) -> None:
    """Fill absent comparison inputs; candidates do not create collection jobs.

    Explicit fields, including empty arrays/objects, remain the operator's
    choice. Credential and network candidates are context only: their actual
    grants still belong to existing execution/job admission.
    """
    for step, tool in assessment_input_selectors(workflow):
        if not isinstance(step, dict) or not isinstance(tool, dict):
            continue
        # Both Pydantic spellings are accepted input. Remove stale metadata
        # from every existing input alias before freezing the canonical one.
        for values in (step.get("inputs"), tool.get("inputs"), tool.get("args")):
            if isinstance(values, dict):
                for key in ASSESSMENT_PROJECT_INPUT_KEYS:
                    values.pop(key, None)
        if snapshot is None:
            continue
        config = snapshot.configuration
        authored = tool.get("inputs")
        if not isinstance(authored, dict):
            authored = tool.get("args", {})
        overrides = step.get("inputs")
        if not isinstance(overrides, dict):
            overrides = {}
        if not isinstance(authored, dict):
            continue
        # Match the ordinary normalizer: step inputs override selector inputs.
        inputs = {**authored, **overrides}
        tool["inputs"] = inputs
        if isinstance(step.get("inputs"), dict):
            step["inputs"] = inputs
        inputs.setdefault(
            "scope",
            {"assets": list(config.assets), "exclusions": list(config.exclusions)},
        )
        inputs.setdefault(
            "budget", config.budget.model_dump(mode="json", by_alias=True)
        )
        inputs.setdefault("reportRefs", list(config.artifact_refs))
        inputs.setdefault(
            "tools", ["retained-report"] if inputs.get("reportRefs") else []
        )
        inputs.setdefault("credentialRefs", [])
        inputs.setdefault("networkRefs", [])
        inputs["assessmentProject"] = copy.deepcopy(
            snapshot.model_dump(mode="json", by_alias=True)
        )
