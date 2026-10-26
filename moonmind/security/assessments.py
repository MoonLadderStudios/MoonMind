"""Artifact-only, coverage-aware security comparisons; no findings database."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from pydantic.alias_generators import to_camel

from moonmind.schemas.agent_runtime_models import ExecutionBudget

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
]
Items = Annotated[list[Text], Field(max_length=4096)]
Evidence = Annotated[list[Text], Field(min_length=1, max_length=64)]
CoverageStatus = Literal[
    "complete", "partial", "authentication_failed", "unsupported", "failed"
]
FindingState = Literal[
    "new",
    "still_observed",
    "absent_comparable",
    "not_reassessed",
    "observed_without_baseline",
]


def content_key(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, alias_generator=to_camel
    )


class AssessmentScope(Contract):
    assets: Annotated[Items, Field(min_length=1)]
    exclusions: Items = Field(default_factory=list)
    repository_exceptions: Items = Field(default_factory=list)

    def equivalent(self, other: AssessmentScope) -> bool:
        return all(
            set(getattr(self, key)) == set(getattr(other, key))
            for key in ("assets", "exclusions", "repository_exceptions")
        )


class ExaminedSubject(Contract):
    asset: Text
    locator: Text
    identity: Text
    resource: Text


class ThreatModel(Contract):
    entry_points: Annotated[Items, Field(min_length=1)]
    trust_boundaries: Annotated[Items, Field(min_length=1)]
    invariants: Annotated[Items, Field(min_length=1)]


class SecurityInterpretation(Contract):
    id: Text
    subject: ExaminedSubject
    hypothesis: Text
    disposition: Literal["confirmed", "likely", "unresolved"]
    evidence_status: Literal["supported", "contradicted", "unsupported"]
    evidence_refs: Evidence
    native_finding_id: Text | None = None
    limitations: Annotated[Items, Field(min_length=1)]

    @model_validator(mode="after")
    def validate_disposition(self):
        if self.evidence_status != "supported" and self.disposition != "unresolved":
            raise ValueError(
                "contradicted or unsupported interpretations remain unresolved"
            )
        return self


class SecurityRetest(Contract):
    hypothesis_id: Text
    original_evidence_refs: Evidence
    current_evidence_refs: Evidence
    changed_assets: Annotated[Items, Field(min_length=1)]
    covered_assets: Items
    coverage: Literal["complete", "partial", "unavailable"]
    limitations: Annotated[Items, Field(min_length=1)]

    @model_validator(mode="after")
    def validate_coverage(self):
        if self.coverage == "complete" and not set(self.changed_assets) <= set(
            self.covered_assets
        ):
            raise ValueError("complete retest coverage must include the changed assets")
        return self


class SecurityReview(Contract):
    """Agent interpretations, never native scanner facts or control attestation.

    Test outcomes and confinement assertions deliberately have no fields here.
    Their authoritative evidence stays with the job and runtime owners.
    """

    schema_version: Literal["security-review/v1"] = "security-review/v1"
    scope: AssessmentScope
    candidate_ref: Text
    threat_model: ThreatModel
    interpretations: Annotated[list[SecurityInterpretation], Field(max_length=128)]
    limitations: Annotated[Items, Field(min_length=1)]
    retests: Annotated[list[SecurityRetest], Field(max_length=128)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_subjects(self):
        ids = [item.id for item in self.interpretations]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate interpretation identity")
        if any(
            item.subject.asset not in self.scope.assets for item in self.interpretations
        ):
            raise ValueError("examined subject outside admitted scope")
        for retest in self.retests:
            if retest.hypothesis_id not in ids:
                raise ValueError("retest requires its original hypothesis")
            original = next(
                item for item in self.interpretations if item.id == retest.hypothesis_id
            )
            if not set(original.evidence_refs) <= set(retest.original_evidence_refs):
                raise ValueError("retest must retain the original hypothesis evidence")
            if (
                retest.coverage == "complete"
                and original.subject.asset not in retest.covered_assets
            ):
                raise ValueError(
                    "complete retest must cover the original hypothesis subject"
                )
            if (set(retest.changed_assets) | set(retest.covered_assets)) - set(
                self.scope.assets
            ):
                raise ValueError("retest outside admitted scope")
        if len(self.evidence_refs()) > 64:
            raise ValueError("review requires at most 64 distinct evidence refs")
        return self

    def evidence_refs(self) -> list[str]:
        return list(
            dict.fromkeys(
                [ref for item in self.interpretations for ref in item.evidence_refs]
                + [
                    ref
                    for retest in self.retests
                    for ref in (
                        *retest.original_evidence_refs,
                        *retest.current_evidence_refs,
                    )
                ]
            )
        )


class Finding(Contract):
    source: Text
    tool: Text
    rule_id: Text
    asset: Text
    native_id: Text
    evidence_refs: Evidence

    @property
    def finding_id(self) -> str:
        # Native identity is required; presentation titles/severities play no part.
        return content_key(
            [self.source, self.tool, self.rule_id, self.asset, self.native_id]
        )


class Coverage(Contract):
    source: Text
    tool: Text
    assets: Items
    rule_ids: Items
    # All rules in config_ref, including rules with no positive findings. The
    # configuration identity still has to match before absence is comparable.
    all_rules: bool = False
    config_ref: Text
    status: CoverageStatus

    def covers(self, finding: Finding) -> bool:
        return (
            self.source == finding.source
            and self.tool == finding.tool
            and finding.asset in self.assets
            and (self.all_rules or finding.rule_id in self.rule_ids)
        )


class Observation(Contract):
    schema_version: Literal["security-observation/v1"] = "security-observation/v1"
    scope: AssessmentScope
    observed_at: AwareDatetime
    expires_at: AwareDatetime
    coverage: Annotated[list[Coverage], Field(min_length=1, max_length=64)]
    findings: Annotated[list[Finding], Field(max_length=4096)]
    provenance: Annotated[dict[Text, Text], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def validate_findings(self):
        ids = [finding.finding_id for finding in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate native finding identity")
        if any(finding.asset not in self.scope.assets for finding in self.findings):
            raise ValueError("finding outside assessment scope")
        if any(set(row.assets) - set(self.scope.assets) for row in self.coverage):
            raise ValueError("coverage outside assessment scope")
        return self

    def complete(self, now: datetime) -> bool:
        return (
            self.observed_at <= now < self.expires_at
            and all(row.status == "complete" for row in self.coverage)
            and set(self.scope.assets)
            <= {asset for row in self.coverage for asset in row.assets}
        )


class TriageDecision(Contract):
    finding_id: Text
    disposition: Literal[
        "accepted_risk", "false_positive", "confirmed_vulnerability", "fix_retest"
    ]
    reason: Text
    evidence_refs: Evidence
    author: Text
    source: Text
    scope: AssessmentScope
    review_at: AwareDatetime
    review_conditions: Annotated[Items, Field(min_length=1)]
    expires_at: AwareDatetime | None = None
    retest_outcome: Literal["passed", "failed", "inconclusive"] | None = None

    @model_validator(mode="after")
    def validate_retest(self):
        if (self.disposition == "fix_retest") != (self.retest_outcome is not None):
            raise ValueError("only fix_retest requires a retestOutcome")
        return self


class ComparedFinding(Contract):
    finding_id: Text
    finding: Finding
    state: FindingState
    observed_coverage_key: Text | None
    evidence_expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_identity(self):
        if self.finding_id != self.finding.finding_id:
            raise ValueError("finding identity does not match native evidence")
        return self


class ComparedDecision(Contract):
    decision: TriageDecision
    status: Literal["active", "review_required", "out_of_scope"]


class Comparison(Contract):
    schema_version: Literal["security-comparison/v1"] = "security-comparison/v1"
    observation: Observation
    baseline_status: Literal["available", "initial", "unavailable"]
    findings: Annotated[list[ComparedFinding], Field(max_length=8192)]
    decisions: Annotated[list[ComparedDecision], Field(max_length=4096)]
    counts: dict[Text, int]
    coverage_status: Literal["complete", "stale_or_partial"]
    baseline_ref: Text | None = None
    scan_ref: Text | None = None

    @model_validator(mode="after")
    def validate_unique_findings(self):
        ids = [row.finding_id for row in self.findings]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate baseline finding identity")
        return self


class InventoryComponent(Contract):
    asset: Text
    purl: Text
    version: Text


class Inventory(Contract):
    schema_version: Literal["security-inventory/v1"]
    scope: AssessmentScope
    observed_at: AwareDatetime
    expires_at: AwareDatetime
    config_ref: Text
    status: CoverageStatus
    components: Annotated[list[InventoryComponent], Field(max_length=4096)]


class AdvisoryRule(Contract):
    id: Text
    purl: Text
    affected_versions: Annotated[Items, Field(min_length=1)]


class AdvisoryFeed(Contract):
    schema_version: Literal["security-feed/v1"]
    source: Text
    revision: Text
    observed_at: AwareDatetime
    expires_at: AwareDatetime
    rules: Annotated[list[AdvisoryRule], Field(max_length=4096)]

    @model_validator(mode="after")
    def validate_rules(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("duplicate advisory rule identity")
        return self


class AssessmentRequest(Contract):
    scope: AssessmentScope
    tools: Annotated[
        list[Literal["inventory-feed", "retained-report"]],
        Field(max_length=2),
    ]
    budget: ExecutionBudget
    credential_refs: Items
    network_refs: Items
    inventory_ref: Text | None = None
    feed_ref: Text | None = None
    report_refs: Annotated[Items, Field(max_length=32)] = Field(default_factory=list)
    baseline_ref: Text | None = None
    decisions_ref: Text | None = None
    review_ref: Text | None = None

    @model_validator(mode="after")
    def validate_admission(self):
        if not self.tools and not self.review_ref:
            raise ValueError(
                "assessment requires native evidence or an explicit reviewRef"
            )
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("duplicate selected tool")
        if not 0 < self.budget.base_seconds <= self.budget.max_seconds <= 300:
            raise ValueError(
                "assessment budget must be positive and at most 300 seconds"
            )
        if self.budget.progress_stall_seconds <= 0:
            raise ValueError("assessment progress stall budget must be positive")
        # These adapters only read authorized artifacts. Active scanner adapters
        # must admit credentials/egress through their own existing owners.
        if self.credential_refs or self.network_refs:
            raise ValueError(
                "offline assessment tools require empty credentialRefs and networkRefs"
            )
        inventory_selected = "inventory-feed" in self.tools
        if inventory_selected != bool(self.inventory_ref and self.feed_ref):
            raise ValueError("inventory-feed requires inventoryRef and feedRef")
        if not inventory_selected and (self.inventory_ref or self.feed_ref):
            raise ValueError(
                "inventory inputs require the selected inventory-feed tool"
            )
        if ("retained-report" in self.tools) != bool(self.report_refs):
            raise ValueError("retained-report requires reportRefs")
        return self


def _coverage_key(
    finding: Finding, observation: Observation, now: datetime
) -> str | None:
    matching = [row for row in observation.coverage if row.covers(finding)]
    if not observation.complete(now) or not matching:
        return None
    return content_key(
        {
            "scope": {
                key: sorted(set(value))
                for key, value in observation.scope.model_dump().items()
            },
            "configuration": sorted({row.config_ref for row in matching}),
        }
    )


def _comparable(
    retained: ComparedFinding,
    current: Observation,
    previous: Observation,
    now: datetime,
) -> bool:
    if (
        not current.complete(now)
        or not previous.complete(now)
        or not current.scope.equivalent(previous.scope)
        or retained.evidence_expires_at <= now
    ):
        return False
    current_key = _coverage_key(retained.finding, current, now)
    # Keep the context of actual observation with each carried finding. A
    # succession of scans that did not reassess it cannot replace that context.
    return current_key is not None and current_key == retained.observed_coverage_key


def compare_observations(
    current: Observation,
    previous: dict | None,
    *,
    decisions=(),
    now: datetime,
    baseline_unavailable: bool = False,
    baseline_ref: str | None = None,
) -> dict:
    """Carry unobserved findings unless both observations prove comparable coverage.

    A disposition annotates a finding; it never changes the scanner's facts or
    asserts a fix. Absence is a coverage observation, separate from fix-retest.
    """
    prior = Comparison.model_validate(previous) if previous is not None else None
    old = {row.finding_id: row for row in prior.findings} if prior else {}
    compared = []
    for finding in current.findings:
        retained = old.pop(finding.finding_id, None)
        if retained:
            evidence_refs = list(
                dict.fromkeys([*retained.finding.evidence_refs, *finding.evidence_refs])
            )
            if len(evidence_refs) > 64 and baseline_ref is not None:
                # Keep the earlier evidence reachable through its immutable
                # baseline instead of growing every subsequent snapshot.
                evidence_refs = [baseline_ref, *finding.evidence_refs]
            finding = Finding.model_validate(
                {
                    **finding.model_dump(),
                    "evidence_refs": list(dict.fromkeys(evidence_refs)),
                }
            )
        observed_key = _coverage_key(finding, current, now)
        compared.append(
            ComparedFinding(
                finding_id=finding.finding_id,
                finding=finding,
                state=(
                    "still_observed"
                    if retained
                    else (
                        "observed_without_baseline" if baseline_unavailable else "new"
                    )
                ),
                observed_coverage_key=(
                    observed_key
                    if observed_key is not None
                    else (retained.observed_coverage_key if retained else None)
                ),
                evidence_expires_at=(
                    current.expires_at
                    if observed_key is not None or not retained
                    else retained.evidence_expires_at
                ),
            )
        )
    for retained in old.values():
        absent = _comparable(retained, current, prior.observation, now)
        compared.append(
            retained.model_copy(
                update={"state": "absent_comparable" if absent else "not_reassessed"}
            )
        )
    previous_decisions = (
        {row.decision.finding_id: row for row in prior.decisions} if prior else {}
    )
    all_decisions = {
        finding_id: row.decision for finding_id, row in previous_decisions.items()
    }
    for payload in decisions:
        decision = TriageDecision.model_validate(payload)
        all_decisions[decision.finding_id] = decision
    triage = []
    for finding_id, decision in all_decisions.items():
        retained = previous_decisions.get(finding_id)
        if not current.scope.equivalent(decision.scope):
            status = "out_of_scope"
        elif (
            now >= decision.review_at
            or (decision.expires_at is not None and now >= decision.expires_at)
            or (
                retained is not None
                and retained.decision == decision
                and (
                    retained.status != "active"
                    or prior.observation.provenance != current.provenance
                )
            )
        ):
            status = "review_required"
        else:
            status = "active"
        triage.append(ComparedDecision(decision=decision, status=status))
    counts = {
        key: sum(row.state == key for row in compared)
        for key in (
            "new",
            "still_observed",
            "absent_comparable",
            "not_reassessed",
            "observed_without_baseline",
        )
    }
    counts["review_required"] = sum(row.status == "review_required" for row in triage)
    return Comparison(
        observation=current,
        baseline_ref=baseline_ref,
        baseline_status=(
            "available"
            if prior
            else ("unavailable" if baseline_unavailable else "initial")
        ),
        findings=compared,
        decisions=triage,
        counts=counts,
        coverage_status="complete" if current.complete(now) else "stale_or_partial",
    ).model_dump(mode="json", by_alias=True)
