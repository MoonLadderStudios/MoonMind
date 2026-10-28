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
    "remediated_verified",
    "closed_upstream",
    "not_reassessed",
    "observed_without_baseline",
]
FINDING_STATES: tuple[str, ...] = FindingState.__args__
UpstreamClosure = Literal["fixed", "dismissed", "resolved"]
ReviewReason = Literal[
    "review_due",
    "expired",
    "review_pending",
    "intelligence_changed",
    "provenance_changed",
    "retest_contradicted",
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
    # Feed, rule, and tool identity that interpreted the inputs. A difference
    # here explains a changed result without implying the subject changed.
    intelligence: Annotated[dict[Text, Text], Field(max_length=16)] = Field(
        default_factory=dict
    )

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
    # Finding IDs the source itself reports closed, such as a GitHub alert
    # fixed by its own analysis. Only the source's own state is recorded here.
    upstream_closed: Annotated[dict[Text, UpstreamClosure], Field(max_length=4096)] = (
        Field(default_factory=dict)
    )

    @model_validator(mode="after")
    def validate_findings(self):
        ids = [finding.finding_id for finding in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate native finding identity")
        if set(ids) & set(self.upstream_closed):
            raise ValueError("an observed finding cannot be closed upstream")
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
    # How remediation was verified; set only for remediated_verified.
    verification: Literal["retest_passed", "upstream_fixed"] | None = None
    # Intelligence of the covering tool that differs from the baseline, so a
    # new or absent finding is not attributed to a subject change alone.
    changed_intelligence: Items = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identity(self):
        if self.finding_id != self.finding.finding_id:
            raise ValueError("finding identity does not match native evidence")
        return self


class ComparedDecision(Contract):
    decision: TriageDecision
    status: Literal["active", "review_required", "out_of_scope"]
    review_reasons: Annotated[list[ReviewReason], Field(max_length=6)] = Field(
        default_factory=list
    )


class Comparison(Contract):
    schema_version: Literal["security-comparison/v1"] = "security-comparison/v1"
    observation: Observation
    baseline_status: Literal["available", "initial", "unavailable"]
    findings: Annotated[list[ComparedFinding], Field(max_length=8192)]
    decisions: Annotated[list[ComparedDecision], Field(max_length=4096)]
    counts: dict[Text, int]
    coverage_status: Literal["complete", "stale_or_partial"]
    intelligence_changes: Items = Field(default_factory=list)
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


GitHubAlertCategory = Literal["code-scanning", "dependabot", "secret-scanning"]


class GitHubAlertImport(Contract):
    """Read existing GitHub alerts through the run's admitted repository connection.

    Bounds apply to the whole import. ``revision`` names the assessed checkout;
    when omitted, the ref's tip at import time is the subject.
    """

    repository: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    ]
    ref: (
        Annotated[str, StringConstraints(pattern=r"^refs/heads/[^\s]{1,255}$")] | None
    ) = None
    revision: (
        Annotated[str, StringConstraints(pattern=r"^([0-9a-f]{40}|[0-9a-f]{64})$")]
        | None
    ) = None
    categories: Annotated[list[GitHubAlertCategory], Field(min_length=1)] = Field(
        default_factory=lambda: ["code-scanning", "dependabot", "secret-scanning"]
    )
    state: Literal["open", "all"] = "open"
    include_analyses: bool = True
    per_page: Annotated[int, Field(ge=1, le=100)] = 100
    max_requests: Annotated[int, Field(ge=1, le=100)] = 30
    max_items: Annotated[int, Field(ge=1, le=4096)] = 1000
    max_bytes: Annotated[int, Field(ge=1, le=8 * 1024 * 1024)] = 4 * 1024 * 1024
    max_seconds: Annotated[int, Field(ge=1, le=300)] = 60

    @model_validator(mode="after")
    def validate_categories(self):
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("duplicate GitHub alert category")
        return self


class AssessmentRequest(Contract):
    scope: AssessmentScope
    tools: Annotated[
        list[Literal["inventory-feed", "retained-report", "github-alerts"]],
        Field(max_length=3),
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
    github_alerts: GitHubAlertImport | None = None

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
        # These adapters only read authorized artifacts, or GitHub through the
        # run's admitted repository connection. Active scanner adapters must
        # admit credentials/egress through their own existing owners.
        if self.credential_refs or self.network_refs:
            raise ValueError(
                "offline assessment tools require empty credentialRefs and networkRefs"
            )
        if ("github-alerts" in self.tools) != (self.github_alerts is not None):
            raise ValueError("github-alerts requires githubAlerts")
        if (
            self.github_alerts is not None
            and self.github_alerts.max_seconds > self.budget.base_seconds
        ):
            raise ValueError("GitHub alert import must fit the base assessment budget")
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


def _intelligence(observation: Observation, finding: Finding | None = None) -> dict:
    return {
        f"{row.tool}:{key}": value
        for row in observation.coverage
        if finding is None or row.covers(finding)
        for key, value in row.intelligence.items()
    }


def _intelligence_changes(
    current: Observation, previous: Observation | None, finding: Finding | None = None
) -> list[str]:
    """Keys both observations recorded with different values.

    A tool absent from either side was not refreshed; it was added or lost,
    which coverage already reports.
    """
    if previous is None:
        return []
    now, before = _intelligence(current, finding), _intelligence(previous, finding)
    return sorted(key for key in now.keys() & before.keys() if now[key] != before[key])


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

    A disposition annotates a finding; it never changes the scanner's facts.
    Absence alone is a coverage observation. It becomes verified remediation
    only with a linked passing fix-retest, or when the source itself reports
    the finding fixed.
    """
    prior = Comparison.model_validate(previous) if previous is not None else None
    previous_observation = prior.observation if prior else None
    old = {row.finding_id: row for row in prior.findings} if prior else {}
    previous_decisions = (
        {row.decision.finding_id: row for row in prior.decisions} if prior else {}
    )
    all_decisions = {
        finding_id: row.decision for finding_id, row in previous_decisions.items()
    }
    for payload in decisions:
        decision = TriageDecision.model_validate(payload)
        all_decisions[decision.finding_id] = decision
    passing_retests = {
        finding_id
        for finding_id, decision in all_decisions.items()
        if decision.disposition == "fix_retest"
        and decision.retest_outcome == "passed"
        and current.scope.equivalent(decision.scope)
    }
    # A contradiction stays with its unchanged decision; only a replacement
    # retest, or one still within its validity, can verify later absence.
    contradicted = {
        finding_id
        for finding_id in passing_retests
        if (retained := previous_decisions.get(finding_id)) is not None
        and retained.decision == all_decisions[finding_id]
        and "retest_contradicted" in retained.review_reasons
    }
    retest_passed = {
        finding_id
        for finding_id in passing_retests - contradicted
        if all_decisions[finding_id].expires_at is None
        or now < all_decisions[finding_id].expires_at
    }
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
                changed_intelligence=(
                    []
                    if retained
                    else _intelligence_changes(current, previous_observation, finding)
                ),
            )
        )
    for retained in old.values():
        closure = current.upstream_closed.get(retained.finding_id)
        verification = None
        if closure == "fixed":
            state, verification = "remediated_verified", "upstream_fixed"
        elif closure is not None:
            state = "closed_upstream"
        elif _comparable(retained, current, prior.observation, now):
            if retained.finding_id in retest_passed:
                state, verification = "remediated_verified", "retest_passed"
            else:
                state = "absent_comparable"
        else:
            state = "not_reassessed"
        compared.append(
            retained.model_copy(
                update={
                    "state": state,
                    "verification": verification,
                    "changed_intelligence": (
                        _intelligence_changes(
                            current, previous_observation, retained.finding
                        )
                        if state in {"absent_comparable", "remediated_verified"}
                        else []
                    ),
                }
            )
        )
    by_id = {row.finding_id: row for row in compared}
    triage = []
    for finding_id, decision in all_decisions.items():
        retained = previous_decisions.get(finding_id)
        row = by_id.get(finding_id)
        reasons = []
        if now >= decision.review_at:
            reasons.append("review_due")
        if decision.expires_at is not None and now >= decision.expires_at:
            reasons.append("expired")
        if retained is not None and retained.decision == decision:
            if retained.status != "active":
                reasons.append("review_pending")
            if row is not None and _intelligence_changes(
                current, prior.observation, row.finding
            ):
                reasons.append("intelligence_changed")
            elif prior.observation.provenance != current.provenance:
                reasons.append("provenance_changed")
        if finding_id in contradicted or (
            finding_id in passing_retests
            and row is not None
            and row.state in {"still_observed", "new"}
        ):
            # The scanner still reports what the retest said was fixed.
            reasons.append("retest_contradicted")
        if not current.scope.equivalent(decision.scope):
            status, reasons = "out_of_scope", []
        else:
            status = "review_required" if reasons else "active"
        triage.append(
            ComparedDecision(decision=decision, status=status, review_reasons=reasons)
        )
    counts = {key: sum(row.state == key for row in compared) for key in FINDING_STATES}
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
        intelligence_changes=_intelligence_changes(current, previous_observation),
    ).model_dump(mode="json", by_alias=True)
