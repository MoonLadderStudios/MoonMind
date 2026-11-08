"""Optional OSCAL 1.2.3 exchange over native assessment contracts.

Imported context is descriptive data. It is never an execution request or a
source of scope, credentials, tool selection, or regulatory attestation.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import (
    UUID4,
    UUID5,
    AwareDatetime,
    Field,
    StringConstraints,
    model_validator,
)

from moonmind.security.evidence_exports import prepare_evidence_payload
from moonmind.security.frameworks import FrameworkContract, FrameworkPack, Text
from moonmind.utils.logging import (
    EVIDENCE_TRANSFORMATION_LIMITATIONS,
    EVIDENCE_TRANSFORMATION_VERSION,
)

if TYPE_CHECKING:
    from moonmind.security.assessments import Comparison

OSCAL_VERSION = "1.2.3"
NAMESPACE = "https://moonmind.dev/ns/assessment"
MODELS = ("catalog", "profile", "assessment-plan", "assessment-results", "poam")
Model = Literal["catalog", "profile", "assessment-plan", "assessment-results", "poam"]
Token = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
ArtifactRef = Annotated[str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]+$")]
OscalUuid = UUID4 | UUID5
LIMITATIONS = (
    "Supported export subset only; no OSCAL import, execution engine or authorization workflow.",
    "Native coverage, applicability and triage remain authoritative; namespaced properties preserve native semantics.",
    "Technical signals are observations; objective findings require explicit assessment mappings.",
    "External auditor/tool acceptance and the authenticity of supplied reference context remain Unverified.",
)


class UnsupportedExport(ValueError):
    def __init__(self, message: str, code: str = "unsupported_mapping"):
        super().__init__(message)
        self.code = code


class OscalExportRequest(FrameworkContract):
    models: Annotated[list[Model], Field(min_length=1, max_length=5)]
    context_ref: ArtifactRef | None = None

    @model_validator(mode="after")
    def unique_models(self):
        if len(set(self.models)) != len(self.models):
            raise ValueError("duplicate OSCAL export model")
        return self


class SystemContext(FrameworkContract):
    identifier: Text
    ssp_ref: ArtifactRef


class Party(FrameworkContract):
    uuid: OscalUuid
    name: Text
    type: Literal["person", "organization"]


class Procedure(FrameworkContract):
    id: Token
    control_id: Text
    objective: Text
    methods: Annotated[
        list[Literal["EXAMINE", "INTERVIEW", "TEST"]], Field(min_length=1, max_length=3)
    ]
    steps: Annotated[list[Text], Field(min_length=1, max_length=32)]
    assets: Annotated[list[Text], Field(min_length=1, max_length=256)]
    responsible_party: OscalUuid


class FindingTarget(FrameworkContract):
    procedure_id: Token
    status: Literal["satisfied", "not-satisfied"]
    reason: Text


class RemediationAction(FrameworkContract):
    id: Token
    description: Text
    lifecycle: Literal["planned", "completed"]


class Milestone(FrameworkContract):
    id: Token
    description: Text
    due: AwareDatetime


class Remediation(FrameworkContract):
    id: Token
    finding_id: Text
    risk_statement: Text
    status: Literal[
        "open",
        "investigating",
        "remediating",
        "deviation-requested",
        "deviation-approved",
        "closed",
    ]
    description: Text
    responsible_party: OscalUuid
    evidence_refs: Annotated[list[ArtifactRef], Field(min_length=1, max_length=64)]
    actions: Annotated[list[RemediationAction], Field(min_length=1, max_length=32)]
    milestones: Annotated[list[Milestone], Field(max_length=32)] = Field(
        default_factory=list
    )


class OscalContext(FrameworkContract):
    schema_version: Literal["oscal-export-context/v1"] = "oscal-export-context/v1"
    system: SystemContext | None = None
    subjects: Annotated[dict[Text, OscalUuid], Field(max_length=256)] = Field(
        default_factory=dict
    )
    parties: Annotated[list[Party], Field(max_length=64)] = Field(default_factory=list)
    procedures: Annotated[list[Procedure], Field(max_length=256)] = Field(
        default_factory=list
    )
    finding_targets: Annotated[dict[Text, FindingTarget], Field(max_length=8192)] = (
        Field(default_factory=dict)
    )
    remediations: Annotated[list[Remediation], Field(max_length=256)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def unique_identities(self):
        for values in (
            [p.uuid for p in self.parties],
            [p.id for p in self.procedures],
            [r.id for r in self.remediations],
        ):
            if len(values) != len(set(values)):
                raise ValueError("duplicate OSCAL context identity")
        for remediation in self.remediations:
            ids = [a.id for a in remediation.actions] + [
                m.id for m in remediation.milestones
            ]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate remediation action or milestone identity")
        return self

    def evidence_refs(self) -> list[str]:
        return list(
            dict.fromkeys([r for item in self.remediations for r in item.evidence_refs])
        )


def artifact_uri(ref: str) -> str:
    return "urn:moonmind:artifact:" + ref


def control_id(native_id: str) -> str:
    # Escape underscores too, so literal escape-looking names cannot collide
    # with encoded punctuation (for example GLI module:section identities).
    return "control." + "".join(
        ch if ch.isascii() and (ch.isalnum() or ch in ".-") else f"_x{ord(ch):x}_"
        for ch in native_id
    )


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _props(**values) -> list[dict]:
    return [
        {
            "name": key.replace("_", "-"),
            "ns": NAMESPACE,
            "value": value if isinstance(value, str) else _json(value),
        }
        for key, value in values.items()
    ]


class OscalAdapter:
    def __init__(
        self,
        *,
        comparison: Comparison,
        pack: FrameworkPack,
        framework_report: dict,
        context: OscalContext | None = None,
        ssp: dict | None = None,
    ):
        self.comparison = comparison
        self.pack = pack
        self.report = framework_report
        self.context = context or OscalContext()
        self.ssp = ssp
        self.controls = {r.id for r in pack.requirements}
        self.rows = {r["id"]: r for r in framework_report["requirements"]}
        if set(self.rows) != self.controls:
            raise UnsupportedExport(
                "Native framework requirement relationships do not match the selected pack."
            )
        supported_states = {
            "evidence_recorded",
            "incomplete",
            "exclusion_for_review",
            "not_applicable",
            "applicability_missing",
            "excluded",
            "partner_owned",
            "not_selected",
            "Unverified",
            "applicable",
        }
        if any(row.get("status") not in supported_states for row in self.rows.values()):
            raise UnsupportedExport(
                "Native framework state has no supported mapping; retain the native report.",
                "unmappable_native_state",
            )
        self.identity = f"{pack.id}:{pack.edition}:" + (
            self.context.system.identifier if self.context.system else ""
        )

    def uid(self, value: str) -> str:
        return str(uuid5(NAMESPACE_URL, self.identity + ":" + value))

    def metadata(self, model: str) -> dict:
        metadata = {
            "title": f"{self.pack.title}: {model}",
            "last-modified": self.comparison.observation.observed_at.isoformat(),
            "version": self.pack.edition,
            "oscal-version": OSCAL_VERSION,
            "remarks": "\n".join((*LIMITATIONS, *EVIDENCE_TRANSFORMATION_LIMITATIONS)),
            "props": _props(
                native_pack=self.pack.id,
                native_edition=self.pack.edition,
                presentation_transformation=EVIDENCE_TRANSFORMATION_VERSION,
            ),
        }
        if self.context.parties:
            metadata.update(
                roles=[
                    {"id": "assessor", "title": "Assessment practitioner"},
                    {"id": "remediation-owner", "title": "Remediation owner"},
                ],
                parties=[p.model_dump(mode="json") for p in self.context.parties],
            )
        return metadata

    def _selected(self) -> list[str]:
        # Exclusions remain visible and are never converted into satisfied controls.
        return [
            r
            for r, row in self.rows.items()
            if not row.get("exclusionReason")
            and row["status"]
            not in {
                "not_applicable",
                "not_selected",
                "excluded",
                "exclusion_for_review",
            }
        ]

    def _reviewed(self) -> dict:
        controls = list(dict.fromkeys(p.control_id for p in self.context.procedures))
        return {
            "control-selections": [
                {"include-controls": [{"control-id": control_id(c)} for c in controls]}
            ],
            "control-objective-selections": [
                {
                    "include-objectives": [
                        {"objective-id": "objective." + p.id}
                        for p in self.context.procedures
                    ]
                }
            ],
        }

    def _plan_context(self) -> None:
        c = self.context
        for field in ("system", "parties", "procedures"):
            if not getattr(c, field):
                raise UnsupportedExport(
                    f"Supply {field} in the export context; technical target scope alone is not an assessment plan.",
                    "missing_context",
                )
        if not self.ssp or not isinstance(self.ssp.get("system-security-plan"), dict):
            raise UnsupportedExport(
                "Supply a readable system-security-plan artifact through system.sspRef.",
                "missing_ssp",
            )
        ssp = self.ssp["system-security-plan"]
        try:
            identifiers = {r["id"] for r in ssp["system-characteristics"]["system-ids"]}
            components = {r["uuid"] for r in ssp["system-implementation"]["components"]}
            implemented = {
                r["control-id"]
                for r in ssp["control-implementation"]["implemented-requirements"]
            }
        except (KeyError, TypeError, AttributeError):
            raise UnsupportedExport(
                "Supply structured SSP system identity, components and control-implementation references.",
                "missing_ssp_context",
            ) from None
        if c.system.identifier not in identifiers:
            raise UnsupportedExport(
                "system.identifier must resolve to the supplied SSP system-ids.",
                "unresolved_system",
            )
        scope = set(self.comparison.observation.scope.assets)
        if set(c.subjects) != scope or any(
            str(v) not in components for v in c.subjects.values()
        ):
            raise UnsupportedExport(
                "subjects must map exactly the admitted scope assets to components in the supplied SSP.",
                "unresolved_subject",
            )
        parties = {p.uuid for p in c.parties}
        for p in c.procedures:
            if (
                p.control_id not in self._selected()
                or control_id(p.control_id) not in implemented
                or not set(p.assets) <= scope
                or p.responsible_party not in parties
            ):
                raise UnsupportedExport(
                    "Each procedure must resolve to a selected control, admitted subjects and a supplied responsible party.",
                    "unsupported_relationship",
                )
        findings = {r.finding_id: r for r in self.comparison.findings}
        procedures = {p.id: p for p in c.procedures}
        if not set(c.finding_targets) <= set(findings) or any(
            t.procedure_id not in procedures
            or findings[fid].finding.asset not in procedures[t.procedure_id].assets
            for fid, t in c.finding_targets.items()
        ):
            raise UnsupportedExport(
                "findingTargets must resolve native findings to supplied assessment procedures.",
                "unsupported_relationship",
            )
        from moonmind.security.assessments import FINDING_STATES

        if any(r.state not in FINDING_STATES for r in self.comparison.findings):
            raise UnsupportedExport(
                "A native finding state has no supported mapping; retain the native report.",
                "unmappable_native_state",
            )

    def _subjects(self, assets) -> list[dict]:
        return [
            {"subject-uuid": str(self.context.subjects[a]), "type": "component"}
            for a in assets
        ]

    def _role(self, party: UUID, role: str = "assessor") -> list[dict]:
        return [{"role-id": role, "party-uuids": [str(party)]}]

    def _plan(self, refs: dict) -> dict:
        self._plan_context()
        activities, objectives, tasks = [], [], []
        for p in self.context.procedures:
            objectives.append(
                {
                    "control-id": control_id(p.control_id),
                    "parts": [
                        {
                            "id": "objective." + p.id,
                            "name": "objective",
                            "prose": p.objective,
                            "parts": [
                                {"name": "method", "prose": method}
                                for method in p.methods
                            ],
                        }
                    ],
                }
            )
            activities.append(
                {
                    "uuid": self.uid("activity:" + p.id),
                    "description": p.objective,
                    "props": _props(methods=p.methods),
                    "steps": [
                        {"uuid": self.uid(f"step:{p.id}:{i}"), "description": step}
                        for i, step in enumerate(p.steps)
                    ],
                    "related-controls": {
                        "control-selections": [
                            {
                                "include-controls": [
                                    {"control-id": control_id(p.control_id)}
                                ]
                            }
                        ]
                    },
                    "responsible-roles": self._role(p.responsible_party),
                }
            )
            subjects = [
                {"type": "component", "include-subjects": self._subjects(p.assets)}
            ]
            tasks.append(
                {
                    "uuid": self.uid("task:" + p.id),
                    "type": "action",
                    "title": " ".join(p.objective.split()),
                    "associated-activities": [
                        {
                            "activity-uuid": self.uid("activity:" + p.id),
                            "subjects": subjects,
                        }
                    ],
                    "subjects": subjects,
                    "responsible-roles": self._role(p.responsible_party),
                }
            )
        return {
            "import-ssp": {"href": artifact_uri(self.context.system.ssp_ref)},
            "reviewed-controls": self._reviewed(),
            "local-definitions": {
                "objectives-and-methods": objectives,
                "activities": activities,
            },
            "assessment-subjects": [
                {
                    "type": "component",
                    "include-subjects": self._subjects(self.context.subjects),
                }
            ],
            "tasks": tasks,
        }

    def _observations_and_findings(self) -> tuple[list[dict], list[dict]]:
        observations, findings = [], []
        native = self.comparison.observation
        # Coverage is reported separately from objective judgments.
        observations.append(
            {
                "uuid": self.uid("coverage"),
                "description": "Native technical coverage; this is not a control-satisfaction judgment.",
                "methods": ["EXAMINE"],
                "collected": native.observed_at.isoformat(),
                "expires": native.expires_at.isoformat(),
                "props": _props(
                    native_coverage=[
                        r.model_dump(mode="json", by_alias=True)
                        for r in native.coverage
                    ],
                    native_scope=native.scope.model_dump(mode="json", by_alias=True),
                    provenance=native.provenance,
                ),
            }
        )
        method_map = {
            "enquiry": "INTERVIEW",
            "evidence": "EXAMINE",
            "observation": "EXAMINE",
        }
        for i, e in enumerate(self.report.get("evidence", [])):
            method = method_map.get(e["method"])
            if method is None:
                raise UnsupportedExport(
                    "Framework evidence method has no supported OSCAL mapping.",
                    "unmappable_native_state",
                )
            observations.append(
                {
                    "uuid": self.uid("framework-evidence:" + str(i)),
                    "description": e["description"],
                    "methods": [method],
                    "collected": e["provenance"]["recordedOn"] + "T00:00:00Z",
                    "props": _props(native_evidence=e),
                    "relevant-evidence": [
                        {
                            "href": artifact_uri(e["artifactRef"]),
                            "description": e["description"],
                        }
                    ],
                }
            )
        for row in self.comparison.findings:
            fid = row.finding_id
            decisions = [
                d.model_dump(mode="json", by_alias=True)
                for d in self.comparison.decisions
                if d.decision.finding_id == fid
            ]
            observation = {
                "uuid": self.uid("observation:" + fid),
                "description": f"Native technical signal {row.finding.native_id}; state {row.state}. Historical or absent signals do not assert current presence.",
                "methods": ["EXAMINE"],
                "collected": native.observed_at.isoformat(),
                "expires": row.evidence_expires_at.isoformat(),
                "props": _props(
                    native_finding_id=fid,
                    native_state=row.state,
                    native_finding=row.model_dump(mode="json", by_alias=True),
                    native_decisions=decisions,
                ),
                "relevant-evidence": [
                    (
                        {
                            "href": artifact_uri(r),
                            "description": "Native technical evidence " + r,
                        }
                        if r.startswith("art_")
                        else {"description": r}
                    )
                    for r in row.finding.evidence_refs
                ],
            }
            if row.finding.asset in self.context.subjects:
                observation["subjects"] = self._subjects([row.finding.asset])
            observations.append(observation)
            target = self.context.finding_targets.get(fid)
            if target is not None:
                findings.append(
                    {
                        "uuid": self.uid("finding:" + fid),
                        "title": row.finding.native_id,
                        "description": target.reason,
                        "props": _props(
                            native_finding_id=fid,
                            native_state=row.state,
                            native_decisions=decisions,
                        ),
                        "target": {
                            "type": "objective-id",
                            "target-id": "objective." + target.procedure_id,
                            "status": {
                                "state": target.status,
                                "remarks": target.reason,
                            },
                        },
                        "related-observations": [
                            {"observation-uuid": observation["uuid"]}
                        ],
                    }
                )
        return observations, findings

    def _poam(self, refs: dict) -> dict:
        self._plan_context()
        if not self.context.remediations:
            raise UnsupportedExport(
                "Supply remediation records with risk statements, actions, status and responsible parties; finding comparison alone is not a remediation plan.",
                "missing_remediation",
            )
        native = {r.finding_id: r for r in self.comparison.findings}
        parties = {p.uuid for p in self.context.parties}
        risks, items = [], []
        for r in self.context.remediations:
            if (
                r.finding_id not in native
                or r.finding_id not in self.context.finding_targets
                or r.responsible_party not in parties
            ):
                raise UnsupportedExport(
                    "Each remediation must resolve to an explicit objective finding and responsible party.",
                    "unsupported_relationship",
                )
            responses = []
            for a in r.actions:
                task = {
                    "uuid": self.uid(f"action:{r.id}:{a.id}"),
                    "type": "action",
                    "title": a.id,
                    "description": a.description,
                    "responsible-roles": self._role(
                        r.responsible_party, "remediation-owner"
                    ),
                }
                responses.append(
                    {
                        "uuid": self.uid(f"response:{r.id}:{a.id}"),
                        "lifecycle": a.lifecycle,
                        "title": a.id,
                        "description": a.description,
                        "tasks": [task],
                    }
                )
            if r.milestones:
                # Milestones belong to the recorded plan, not to an invented date.
                responses[0]["tasks"].extend(
                    {
                        "uuid": self.uid(f"milestone:{r.id}:{m.id}"),
                        "type": "milestone",
                        "title": m.id,
                        "description": m.description,
                        "timing": {
                            "on-date": {
                                "date": m.due.isoformat().replace("+00:00", "Z")
                            }
                        },
                        "responsible-roles": self._role(
                            r.responsible_party, "remediation-owner"
                        ),
                    }
                    for m in r.milestones
                )
            risk_id = self.uid("risk:" + r.id)
            risks.append(
                {
                    "uuid": risk_id,
                    "title": r.id,
                    "description": r.description,
                    "statement": r.risk_statement,
                    "status": r.status,
                    "props": _props(
                        native_remediation=r.model_dump(mode="json", by_alias=True),
                        native_state=native[r.finding_id].state,
                    ),
                    "links": [
                        {"href": artifact_uri(ref), "rel": "evidence"}
                        for ref in r.evidence_refs
                    ],
                    "remediations": responses,
                    "related-observations": [
                        {"observation-uuid": self.uid("observation:" + r.finding_id)}
                    ],
                }
            )
            items.append(
                {
                    "uuid": self.uid("poam-item:" + r.id),
                    "title": r.id,
                    "description": r.description,
                    "related-findings": [
                        {"finding-uuid": self.uid("finding:" + r.finding_id)}
                    ],
                    "related-risks": [{"risk-uuid": risk_id}],
                }
            )
        observations, findings = self._observations_and_findings()
        for finding in findings:
            finding["target"]["links"] = [
                {
                    "href": artifact_uri(refs["assessment-plan"])
                    + "#"
                    + finding["target"]["target-id"],
                    "rel": "reference",
                }
            ]
        return {
            "import-ssp": {"href": artifact_uri(self.context.system.ssp_ref)},
            "system-id": {"id": self.context.system.identifier},
            "observations": observations,
            "risks": risks,
            "poam-items": items,
            **({"findings": findings} if findings else {}),
        }

    def build(self, model: Model, refs: dict[str, str]) -> dict:
        document = {"uuid": self.uid(model), "metadata": self.metadata(model)}
        if model == "catalog":
            document["controls"] = [
                {
                    "id": control_id(r.id),
                    "title": r.title,
                    "props": _props(
                        category=r.category,
                        applicability=r.applicability,
                        native_requirement=r.model_dump(mode="json", by_alias=True),
                        native_assessment=self.rows[r.id],
                    ),
                    "links": [
                        {"href": s.url, "rel": "reference", "text": s.section}
                        for s in r.sources
                    ]
                    or [{"href": self.pack.source.url, "rel": "reference"}],
                    "parts": [
                        {
                            "id": control_id(r.id) + ".statement",
                            "name": "statement",
                            "prose": r.summary or r.evidence_prompt,
                        }
                    ],
                }
                for r in self.pack.requirements
            ]
        elif model == "profile":
            if not self._selected():
                raise UnsupportedExport(
                    "No controls remain selected; native exclusions are retained in the catalog.",
                    "empty_selection",
                )
            document["imports"] = [
                {
                    "href": artifact_uri(refs["catalog"]),
                    "include-controls": [
                        {"with-ids": [control_id(c) for c in self._selected()]}
                    ],
                }
            ]
            document["merge"] = {"as-is": True}
        elif model == "assessment-plan":
            document.update(self._plan(refs))
            document["metadata"]["links"] = [
                {"href": artifact_uri(refs["profile"]), "rel": "reference"}
            ]
        elif model == "assessment-results":
            self._plan_context()
            observations, findings = self._observations_and_findings()
            result = {
                "uuid": self.uid("result"),
                "title": self.pack.title,
                "description": "Recorded native evidence with explicitly supplied objective judgments.",
                "start": self.comparison.observation.observed_at.isoformat(),
                "reviewed-controls": self._reviewed(),
                "props": _props(
                    coverage_status=self.comparison.coverage_status,
                    native_counts=self.comparison.counts,
                ),
                "observations": observations,
            }
            if findings:
                result["findings"] = findings
            document.update(
                {
                    "import-ap": {"href": artifact_uri(refs["assessment-plan"])},
                    "results": [result],
                }
            )
        elif model == "poam":
            document.update(self._poam(refs))
            document["metadata"]["links"] = [
                {"href": artifact_uri(refs["assessment-plan"]), "rel": "reference"}
            ]
        else:
            raise UnsupportedExport("Unsupported OSCAL model.", "unsupported_model")
        # Mapping and stable semantic identifiers use immutable native data;
        # only the emitted exchange projection is transformed.
        return prepare_evidence_payload(
            {"plan-of-action-and-milestones" if model == "poam" else model: document}
        )
