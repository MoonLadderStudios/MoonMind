"""Native assessment tool on the existing scheduled Skill/artifact path."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from pydantic import ValidationError
from temporalio import activity

from moonmind.security.assessments import (
    AdvisoryFeed,
    AssessmentRequest,
    Comparison,
    Coverage,
    Finding,
    Inventory,
    Observation,
    SecurityReview,
    TriageDecision,
    compare_observations,
    content_key,
)
from moonmind.security.passive_repo_scan import (
    PASSIVE_SCAN_TOOL_REF,
    parse_scan_report_json,
)
from moonmind.security.repository_scanners import ScannerSummary
from moonmind.utils.logging import redact_sensitive_text
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure, ToolResult
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactAuthorizationError,
    TemporalArtifactBytesMissingError,
    TemporalArtifactNotFoundError,
)
from moonmind.workflows.temporal.report_artifacts import build_report_bundle_result

TOOL_NAME = "security.assess"
_MAX_INPUT_BYTES = 8 * 1024 * 1024


def _check_cancelled() -> None:
    if activity.in_activity() and activity.is_cancelled():
        raise asyncio.CancelledError()


def observe_inventory(
    request: AssessmentRequest, inventory: Inventory, feed: AdvisoryFeed
) -> Observation:
    """Match explicit affected versions in an admitted feed; never infer ranges."""
    if not request.scope.equivalent(inventory.scope):
        raise ValueError("inventory scope differs from admitted assessment scope")
    if any(
        component.asset not in request.scope.assets
        for component in inventory.components
    ):
        raise ValueError("inventory component outside admitted scope")
    findings = []
    rules_by_component = {}
    for rule in feed.rules:
        for version in rule.affected_versions:
            rules_by_component.setdefault((rule.purl, version), []).append(rule)
    for index, component in enumerate(inventory.components):
        if index % 32 == 0:
            _check_cancelled()
        for rule in rules_by_component.get((component.purl, component.version), ()):
            if len(findings) >= 4096:
                raise ValueError("assessment finding bound exceeded")
            findings.append(
                Finding(
                    source=feed.source,
                    tool="inventory-feed",
                    rule_id=rule.id,
                    asset=component.asset,
                    native_id=f"{component.purl}@{component.version}",
                    evidence_refs=[request.inventory_ref, request.feed_ref],
                )
            )
    # Duplicate inventory entries refer to the same native fact.
    findings = list({row.finding_id: row for row in findings}.values())
    assets = sorted(
        set(inventory.scope.assets)
        if inventory.status == "complete"
        else {component.asset for component in inventory.components}
    )
    return Observation(
        scope=request.scope,
        observed_at=max(inventory.observed_at, feed.observed_at),
        expires_at=min(inventory.expires_at, feed.expires_at),
        findings=findings,
        coverage=[
            Coverage(
                source=feed.source,
                tool="inventory-feed",
                assets=assets,
                rule_ids=[rule.id for rule in feed.rules],
                status=inventory.status,
                config_ref=inventory.config_ref,
            )
        ],
        provenance={
            "inventory": request.inventory_ref,
            "feed": request.feed_ref,
            "feedRevision": feed.revision,
        },
    )


def _passive_observation(
    request: AssessmentRequest, ref: str, body: bytes, created_at: datetime
) -> Observation:
    native = parse_scan_report_json(body)
    if native.tool_ref != PASSIVE_SCAN_TOOL_REF:
        raise ValueError("unsupported native security report")
    # Scope assets are snapshot-relative file paths. Native categories name the
    # maintained scanner's rule families; the location and redacted native
    # evidence distinguish instances within each family, without secret values.
    findings = []
    for item in native.findings:
        location = item.location.removeprefix("scan:")
        asset = (
            location.rsplit(":", 1)[0]
            if location.rsplit(":", 1)[-1].isdigit()
            else location
        )
        if asset not in request.scope.assets:
            raise ValueError("native finding outside admitted file scope")
        findings.append(
            Finding(
                source=native.tool_ref,
                tool=native.tool_ref,
                rule_id=item.category,
                asset=asset,
                native_id=content_key(
                    [item.location, item.category, item.redacted_preview]
                ),
                evidence_refs=[ref],
            )
        )
    return Observation(
        scope=request.scope,
        observed_at=created_at,
        expires_at=created_at + timedelta(days=1),
        findings=list({finding.finding_id: finding for finding in findings}.values()),
        coverage=[
            Coverage(
                source=native.tool_ref,
                tool=native.tool_ref,
                assets=[
                    path
                    for path in native.covered_files
                    if path in request.scope.assets
                ],
                rule_ids=["credential", "private_key", "authorization", "token"],
                config_ref=content_key(native.to_payload()["config"]),
                status="partial" if native.verdict == "incomplete" else "complete",
            )
        ],
        provenance={
            "nativeReport": ref,
            "snapshotDigest": native.input_digest or "unavailable",
            "feed": "offline-regex",
        },
    )


def _scanner_observation(
    request, summary: ScannerSummary, ref: str, created_at: datetime
):
    """Retained native facts compose with inventory/passive reports without an LLM."""
    admission = summary.admission
    modes = {"history", "worktree"} if summary.scanner == "gitleaks" else {"scan"}
    if set(summary.results) != modes:
        raise ValueError("scanner operation coverage is missing")
    if admission is not None:
        if summary.scanner != admission.scanner:
            raise ValueError("scanner identity differs from admission")
        if set(admission.exclusions) != set(request.scope.exclusions):
            raise ValueError("scanner exclusions differ from admitted assessment")
        if (
            admission.allow_inline_suppressions
            and "inline-suppressions" not in request.scope.repository_exceptions
        ):
            raise ValueError("scanner suppressions differ from admitted assessment")
    findings = [finding for row in summary.results.values() for finding in row.findings]
    if any(
        not set(f.evidence_refs) <= set(summary.native_refs.values()) for f in findings
    ):
        raise ValueError("finding evidence differs from retained native records")
    if any(f.asset not in request.scope.assets for f in findings):
        raise ValueError("scanner finding outside admitted assessment scope")
    findings = list({f.finding_id: f for f in findings}.values())
    truncated = len(findings) > 4096
    complete = (
        admission is not None
        and not truncated
        and summary.coverage == "complete"
        and not summary.limitations
        and summary.execution is not None
        and not summary.execution.limitations
        and set(summary.native_refs) == modes
        and all(
            r.coverage == "complete"
            and r.execution == "succeeded"
            and not r.limitations
            for r in summary.results.values()
        )
    )
    # Snapshot inventory is not evidence that Opengrep or Trivy examined a file.
    # Gitleaks' explicit directory invocation covers the bounded staged inputs;
    # its native finding list has no separate scanned-file inventory.
    assets = sorted(
        (
            set(summary.execution.assets if summary.execution else [])
            | {f.asset for f in findings}
        )
        if summary.scanner == "gitleaks"
        else {
            asset for row in summary.results.values() for asset in row.examined_assets
        }
    )
    if not assets:
        complete = False
    return Observation(
        scope=request.scope,
        observed_at=created_at,
        expires_at=created_at + timedelta(days=1),
        findings=findings[:4096],
        coverage=[
            Coverage(
                source="repository",
                tool=summary.scanner,
                assets=[a for a in assets if a in request.scope.assets],
                rule_ids=[],
                all_rules=admission is not None,
                config_ref=content_key(
                    [
                        (
                            admission.model_dump(
                                mode="json", exclude={"output_directory"}
                            )
                            if admission
                            else None
                        ),
                        (
                            summary.execution.version
                            if summary.execution
                            else "unavailable"
                        ),
                    ]
                ),
                status="complete" if complete else "partial",
            )
        ],
        provenance={
            **(
                {"findingLimit": "combined_finding_bound_exceeded"} if truncated else {}
            ),
            "scannerSummary": ref,
            "nativeReports": content_key(summary.native_refs),
            "scannerVersion": (
                summary.execution.version if summary.execution else "unavailable"
            ),
            "inputDigest": (
                summary.execution.input_digest if summary.execution else "unavailable"
            ),
        },
    )


class AssessmentExecution:
    def __init__(self, service: Any, *, principal: str, context: Mapping[str, Any]):
        self.service = service
        self.principal = principal
        self.context = context
        self.execution = {
            "namespace": context["namespace"],
            "workflow_id": context["workflow_id"],
            "run_id": context["run_id"],
        }

    async def read(self, ref: str) -> tuple[Any, bytes]:
        _check_cancelled()
        # Always authorize current inputs before consulting a saved result.
        artifact, body = await self.service.read(
            artifact_id=ref,
            principal=self.principal,
            allow_restricted_raw=True,
        )
        if len(body) > _MAX_INPUT_BYTES:
            raise ToolFailure(
                "INVALID_INPUT", "Security artifact exceeds the input bound.", False
            )
        return artifact, body

    async def write(
        self, payload: bytes, *, kind: str, key: str, content_type="application/json"
    ) -> str:
        _check_cancelled()
        artifact, _ = await self.service.put_content_addressed_payload_complete(
            principal=self.principal,
            payload=payload,
            content_type=content_type,
            scope="security-assessments",
            link={**self.execution, "link_type": kind},
            metadata_json={
                "artifact_type": "security_assessment",
                "producer": TOOL_NAME,
                "subject": key,
                "sensitivity": "restricted",
                "step_id": self.context["node_id"],
            },
        )
        return artifact.artifact_id

    async def saved(self, *, kind: str, key: str) -> tuple[str, dict] | None:
        artifacts = await self.service.list_for_execution(
            **self.execution,
            principal=self.principal,
            link_type=kind,
        )
        for artifact in reversed(artifacts):
            if (artifact.metadata_json or {}).get("subject") != key:
                continue
            if getattr(artifact.status, "value", artifact.status) != "complete":
                continue
            _, body = await self.read(artifact.artifact_id)
            return artifact.artifact_id, json.loads(body)
        return None

    async def execute(self, request: AssessmentRequest) -> ToolResult:
        now = datetime.now(UTC)
        refs = [*request.report_refs]
        if request.inventory_ref:
            refs += [request.inventory_ref, request.feed_ref]
        inputs = {ref: await self.read(ref) for ref in refs}
        baseline = None
        baseline_unavailable = False
        diagnostics = []
        scan_diagnostics = []
        if request.baseline_ref:
            try:
                _, raw = await self.read(request.baseline_ref)
                baseline = Comparison.model_validate_json(raw).model_dump(
                    mode="json", by_alias=True
                )
            except (
                TemporalArtifactNotFoundError,
                TemporalArtifactAuthorizationError,
                TemporalArtifactBytesMissingError,
                ValidationError,
                ValueError,
            ) as exc:
                baseline_unavailable = True
                diagnostics.append(
                    {"boundary": "baseline", "error": type(exc).__name__}
                )
        decisions = []
        if request.decisions_ref:
            _, raw = await self.read(request.decisions_ref)
            # A malformed disposition cannot silently drop existing triage.
            decisions = [
                TriageDecision.model_validate(item) for item in json.loads(raw)
            ]
        scan_key = content_key(
            {
                "scope": request.scope.model_dump(mode="json"),
                "tools": request.tools,
                "inputs": {
                    ref: hashlib.sha256(body).hexdigest()
                    for ref, (_, body) in inputs.items()
                },
            }
        )
        saved = await self.saved(kind="security.scan", key=scan_key)
        if saved:
            scan_ref, work = saved
            observation = Observation.model_validate(work["observation"])
        else:
            observations = []
            outcomes = []
            scanner_summaries = []
            if (
                baseline
                and baseline["observation"]["provenance"].get("assessmentWork")
                == scan_key
            ):
                # Duplicate feed delivery may reference the previous occurrence's
                # authorized baseline, so no scanner computation is repeated.
                observations.append(Observation.model_validate(baseline["observation"]))
                outcomes.append("retained_completed_work")
            else:
                if "inventory-feed" in request.tools:
                    try:
                        inventory = Inventory.model_validate_json(
                            inputs[request.inventory_ref][1]
                        )
                        feed = AdvisoryFeed.model_validate_json(
                            inputs[request.feed_ref][1]
                        )
                        observations.append(observe_inventory(request, inventory, feed))
                        outcomes.append(inventory.status)
                    except (ValidationError, ValueError) as exc:
                        scan_diagnostics.append(
                            {"boundary": "inventory-feed", "error": type(exc).__name__}
                        )
                for ref in request.report_refs:
                    metadata, raw = inputs[ref]
                    try:
                        payload = json.loads(raw)
                        if payload.get("schemaVersion") == "repository-scanner/v1":
                            native_summary = ScannerSummary.model_validate(payload)
                            # References are authorized through the existing
                            # artifact owner before they reach a report/model.
                            total = 0
                            for native_ref in native_summary.native_refs.values():
                                _, body = await self.read(native_ref)
                                total += len(body)
                                if total > _MAX_INPUT_BYTES * 2:
                                    raise ValueError(
                                        "scanner evidence exceeds input bound"
                                    )
                            observed_at = metadata.created_at
                            if observed_at.tzinfo is None:
                                observed_at = observed_at.replace(tzinfo=UTC)
                            observation = _scanner_observation(
                                request, native_summary, ref, observed_at
                            )
                            scanner_summaries.append(
                                {
                                    "scanner": native_summary.scanner,
                                    "summaryRef": ref,
                                    "nativeRefs": native_summary.native_refs,
                                    "coverage": observation.coverage[0].status,
                                    "execution": {
                                        mode: row.execution
                                        for mode, row in native_summary.results.items()
                                    },
                                    "limitations": sorted(
                                        set(native_summary.limitations)
                                        | {
                                            limit
                                            for row in native_summary.results.values()
                                            for limit in row.limitations
                                        }
                                        | (
                                            {observation.provenance["findingLimit"]}
                                            if "findingLimit" in observation.provenance
                                            else set()
                                        )
                                    ),
                                    "verification": native_summary.verification,
                                }
                            )
                        elif payload.get("toolRef") == PASSIVE_SCAN_TOOL_REF:
                            observed_at = metadata.created_at
                            if observed_at.tzinfo is None:
                                observed_at = observed_at.replace(tzinfo=UTC)
                            observation = _passive_observation(
                                request, ref, raw, observed_at
                            )
                        else:
                            observation = Observation.model_validate(payload)
                        if not request.scope.equivalent(observation.scope):
                            raise ValueError(
                                "report scope differs from admitted assessment scope"
                            )
                        observations.append(observation)
                        outcomes.append(
                            "complete" if observation.complete(now) else "incomplete"
                        )
                    except (ValidationError, ValueError, AttributeError) as exc:
                        scan_diagnostics.append(
                            {"boundary": "retained-report", "error": type(exc).__name__}
                        )
            coverage = [
                row for observation in observations for row in observation.coverage
            ]
            findings_by_id = {}
            for item in observations:
                for finding in item.findings:
                    previous = findings_by_id.get(finding.finding_id)
                    if previous is not None:
                        evidence_refs = list(
                            dict.fromkeys(
                                [*previous.evidence_refs, *finding.evidence_refs]
                            )
                        )
                        if len(evidence_refs) > 64:
                            # The bounded input refs retain every original
                            # report's evidence without copying an unbounded union.
                            evidence_refs = list(dict.fromkeys(refs))
                        finding = Finding.model_validate(
                            {
                                **finding.model_dump(),
                                "evidence_refs": evidence_refs,
                            }
                        )
                    findings_by_id[finding.finding_id] = finding
            if scan_diagnostics or not observations:
                coverage.append(
                    Coverage(
                        source="unavailable",
                        tool=TOOL_NAME,
                        assets=request.scope.assets,
                        rule_ids=[],
                        status="failed",
                        config_ref=scan_key,
                    )
                )
            observation = Observation(
                scope=request.scope,
                observed_at=max(
                    (item.observed_at for item in observations), default=now
                ),
                expires_at=min((item.expires_at for item in observations), default=now),
                findings=list(findings_by_id.values()),
                coverage=coverage,
                provenance={
                    **{f"input{index}": ref for index, ref in enumerate(refs)},
                    **{
                        key: value
                        for item in observations
                        for key, value in item.provenance.items()
                    },
                    "assessmentWork": scan_key,
                },
            )
            work = {
                "observation": observation.model_dump(mode="json", by_alias=True),
                "scanOutcome": (
                    "completed" if observation.complete(now) else "incomplete"
                ),
                "originalOutcomes": outcomes,
                "diagnostics": scan_diagnostics,
                "scannerSummaries": scanner_summaries,
            }
            scan_ref = await self.write(_json(work), kind="security.scan", key=scan_key)
        comparison = compare_observations(
            observation,
            baseline,
            decisions=decisions,
            now=now,
            baseline_unavailable=baseline_unavailable,
            baseline_ref=request.baseline_ref,
        )
        comparison["baselineRef"] = request.baseline_ref
        comparison["scanRef"] = scan_ref
        report_key = content_key(
            [scan_key, request.baseline_ref, request.decisions_ref, comparison]
        )
        saved_comparison = await self.saved(kind="security.comparison", key=report_key)
        comparison_ref = (
            saved_comparison[0]
            if saved_comparison
            else await self.write(
                _json(comparison),
                kind="security.comparison",
                key=report_key,
            )
        )
        # Interpretation is a separate, optional artifact phase. It cannot
        # change native finding identity, coverage, authority, or the scan key.
        review = None
        review_status = "not_requested"
        review_readable = False
        if request.review_ref:
            try:
                _, raw = await self.read(request.review_ref)
                review_readable = True
                review = SecurityReview.model_validate_json(raw)
                if not request.scope.equivalent(review.scope):
                    raise ValueError("review scope differs from admitted scope")
                native_ids = {row["findingId"] for row in comparison["findings"]}
                if any(
                    item.native_finding_id and item.native_finding_id not in native_ids
                    for item in review.interpretations
                ):
                    raise ValueError(
                        "interpretation references an unknown native finding"
                    )
                total = 0
                for ref in review.evidence_refs():
                    _, body = await self.read(ref)
                    total += len(body)
                    if total > _MAX_INPUT_BYTES:
                        raise ValueError("review evidence exceeds the input bound")
                review_status = "completed"
            except Exception as exc:
                review = None
                review_status = "unavailable"
                diagnostics.append(
                    {"boundary": "agent-review", "error": type(exc).__name__}
                )
        outputs = {
            "scanOutcome": work["scanOutcome"],
            "originalOutcomes": work["originalOutcomes"],
            "scanRef": scan_ref,
            "comparisonRef": comparison_ref,
            "baselineRef": request.baseline_ref,
            "baselineStatus": comparison["baselineStatus"],
            "coverageStatus": comparison["coverageStatus"],
            "counts": comparison["counts"],
            "diagnostics": [*work["diagnostics"], *diagnostics],
            "budget": request.budget.model_dump(mode="json", by_alias=True),
            "publicationOutcome": "published",
            "reviewRef": request.review_ref,
            "reviewStatus": review_status,
            "scannerSummaries": work.get("scannerSummaries", []),
        }
        try:
            summary = (
                _summary(comparison)
                + _scanner_summary(work.get("scannerSummaries", []))
                + _review_summary(review, review_status)
            ).encode()
            summary_key = content_key(
                [report_key, "summary", request.review_ref, review_status]
            )
            saved_summary = await self.service.list_for_execution(
                **self.execution,
                principal=self.principal,
                link_type="report.primary",
            )
            summary_ref = next(
                (
                    item.artifact_id
                    for item in saved_summary
                    if (item.metadata_json or {}).get("subject") == summary_key
                ),
                None,
            )
            if summary_ref is None:
                summary_ref = await self.write(
                    summary,
                    kind="report.primary",
                    key=summary_key,
                    content_type="text/markdown",
                )
            for ref, link_type in (
                (comparison_ref, "report.structured"),
                (scan_ref, "report.evidence"),
            ):
                await self.service.link_artifact(
                    artifact_id=ref,
                    principal=self.principal,
                    execution_ref={**self.execution, "link_type": link_type},
                )
            if review_readable:
                await self.service.link_artifact(
                    artifact_id=request.review_ref,
                    principal=self.principal,
                    execution_ref={**self.execution, "link_type": "report.evidence"},
                )
            outputs["reportBundle"] = build_report_bundle_result(
                primary_report_ref={"artifact_id": summary_ref},
                structured_ref={"artifact_id": comparison_ref},
                evidence_refs=[{"artifact_id": scan_ref}]
                + ([{"artifact_id": request.review_ref}] if review_readable else []),
                report_type="security_assessment",
                report_scope=comparison["coverageStatus"],
                sensitivity="restricted",
                counts=comparison["counts"],
            )
        except Exception as exc:
            # The durable scanner result and comparison remain readable even
            # when dashboard publication fails. Retrying reuses those results.
            outputs["publicationOutcome"] = "failed"
            outputs["diagnostics"].append(
                {"boundary": "report-publication", "error": type(exc).__name__}
            )
        return ToolResult(status="COMPLETED", outputs=outputs)


def _json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _summary(comparison: dict) -> str:
    counts = comparison["counts"]
    return "\n".join(
        [
            "# Security assessment",
            "",
            f"Coverage: {comparison['coverageStatus']}. Baseline: {comparison['baselineStatus']}.",
            *[f"- {key.replace('_', ' ')}: {value}" for key, value in counts.items()],
            "",
            "Absence describes comparable completed coverage only; it is not a confirmed fix.",
            "Stale or partial evidence and expired decisions require review.",
            "Finding evidence, provenance and attributed decisions are retained in the structured report.",
            "",
        ]
    )


def _scanner_summary(summaries: list[dict]) -> str:
    lines = []
    for summary in summaries:
        lines += [
            "",
            f"Scanner: {summary['scanner']}. Coverage: {summary['coverage']}.",
            f"Execution: {json.dumps(summary['execution'], sort_keys=True)}.",
            "Verification: not performed.",
            "Native evidence: " + ", ".join(summary["nativeRefs"].values()) + ".",
        ]
        if summary["limitations"]:
            lines.append("Limitations: " + ", ".join(summary["limitations"]) + ".")
    return "\n".join(lines) + ("\n" if lines else "")


def _review_summary(review: SecurityReview | None, status: str) -> str:
    if status == "not_requested":
        return ""
    if review is None:
        return "\nAgent interpretation unavailable. Native findings and coverage are retained.\n"

    def text(value: str) -> str:
        value = html.escape(
            redact_sensitive_text(value).replace("\n", " ").replace("\r", " ")
        )
        return re.sub(r"([\\`*_[\]{}()#!|>])", r"\\\1", value)

    lines = [
        "",
        "## Agent interpretation",
        "",
        f"Examined candidate: {text(review.candidate_ref)}.",
        "These are agent interpretations, not scanner facts, test outcomes or confinement attestations.",
        "",
        "### Threat model",
        "",
        *[f"- Entry point: {text(item)}" for item in review.threat_model.entry_points],
        *[
            f"- Trust boundary: {text(item)}"
            for item in review.threat_model.trust_boundaries
        ],
        *[f"- Invariant: {text(item)}" for item in review.threat_model.invariants],
    ]
    for item in review.interpretations:
        subject = item.subject
        lines += [
            "",
            f"- {text(item.id)}: {item.disposition} ({item.evidence_status}). {text(item.hypothesis)}",
            f"  Examined {text(subject.asset)} at {text(subject.locator)}; identity {text(subject.identity)}; resource {text(subject.resource)}.",
            "  Evidence: " + ", ".join(text(ref) for ref in item.evidence_refs),
            *[f"  Limit: {text(limit)}" for limit in item.limitations],
        ]
    for retest in review.retests:
        lines += [
            "",
            f"- Retest {text(retest.hypothesis_id)}: agent-described coverage {retest.coverage}.",
            "  Changed: " + ", ".join(text(asset) for asset in retest.changed_assets),
            "  Covered: " + ", ".join(text(asset) for asset in retest.covered_assets),
            "  Original evidence: "
            + ", ".join(text(ref) for ref in retest.original_evidence_refs),
            "  Current evidence: "
            + ", ".join(text(ref) for ref in retest.current_evidence_refs),
            *[f"  Limit: {text(limit)}" for limit in retest.limitations],
        ]
    lines += ["", *[f"- Limit: {text(limit)}" for limit in review.limitations], ""]
    return "\n".join(lines)


async def security_assessment_handler(
    inputs: Mapping[str, Any], context: Mapping[str, Any] | None = None
) -> ToolResult:
    context = dict(context or {})
    try:
        request = AssessmentRequest.model_validate(inputs)
    except ValidationError as exc:
        raise ToolFailure(
            "INVALID_INPUT", "Invalid security assessment admission.", False
        ) from exc
    required = ("namespace", "workflow_id", "run_id", "node_id")
    if any(not context.get(key) for key in required) or not context.get(
        "temporal_artifact_service"
    ):
        raise ToolFailure(
            "INVALID_INPUT",
            "Security assessment requires its execution and artifact context.",
            False,
        )
    principal = context.get("deployment_evidence_principal")
    if not principal:
        raise ToolFailure(
            "POLICY_VIOLATION", "Security assessment principal is unavailable.", False
        )
    effective_budget = (
        request.budget.as_flat() if request.budget.mode == "flat" else request.budget
    )
    remaining = effective_budget.max_seconds
    if activity.in_activity():
        remaining -= (
            datetime.now(UTC) - activity.info().scheduled_time
        ).total_seconds()
    if remaining <= 0:
        raise ToolFailure("TIMEOUT", "Original assessment budget is exhausted.", False)
    async with asyncio.timeout(remaining):
        return await AssessmentExecution(
            context["temporal_artifact_service"],
            principal=principal,
            context=context,
        ).execute(request)


def register_security_assessment_tool_handler(dispatcher: Any) -> None:
    dispatcher.register_skill(skill_name=TOOL_NAME, handler=security_assessment_handler)
