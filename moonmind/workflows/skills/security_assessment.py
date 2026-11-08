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

from moonmind.security.application_access import (
    ApplicationAccessReport,
    ApplicationAccessSpec,
    verify_report_evidence,
)
from moonmind.security.assessments import (
    MAX_RETAINED_REPORT_BYTES,
    AdvisoryFeed,
    AssessmentRequest,
    Comparison,
    ControlAssessment,
    Coverage,
    CoverageWindow,
    Finding,
    Inventory,
    Observation,
    ProductBaseline,
    SecurityReview,
    TriageDecision,
    compare_observations,
    content_key,
)
from moonmind.security.cloud_assessments import (
    CloudAssessmentSummary,
    review_cloud_mappings,
)
from moonmind.security.game_math import GameMathAssessment
from moonmind.security.github_alerts import alert_observation, correlate_alerts
from moonmind.security.image_assessments import ImageAssessmentSummary
from moonmind.security.passive_repo_scan import (
    PASSIVE_SCAN_TOOL_REF,
    parse_scan_report_json,
)
from moonmind.security.repository_scanners import ScannerSummary
from moonmind.security.rgs_assessment import RgsAssessment
from moonmind.security.rng_assessment import RngAssessment
from moonmind.security.service_inventory import (
    ServiceInventorySummary,
    build_service_inventory_plan,
    service_inventory_pinned_identities,
    summarize_service_inventory,
    validate_service_inventory_receipt,
)
from moonmind.security.tls_assessments import (
    TLSAssessment,
    TLSAssessmentSummary,
    summarize_tls_assessment,
)
from moonmind.security.web_assessments import WebAssessmentSummary
from moonmind.utils.logging import (
    EVIDENCE_TRANSFORMATION_VERSION,
    redact_sensitive_text,
)
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure, ToolResult
from moonmind.workflows.temporal.artifacts import (
    ARTIFACT_ID_PATTERN,
    ArtifactAccessContext,
    ArtifactDependency,
    TemporalArtifactAuthorizationError,
    TemporalArtifactNotFoundError,
    TemporalArtifactStateError,
    TemporalArtifactStorageAccessError,
    TemporalArtifactUnavailableError,
    TemporalArtifactValidationError,
)
from moonmind.workflows.temporal.report_artifacts import build_report_bundle_result

TOOL_NAME = "security.assess"
_MAX_INPUT_BYTES = MAX_RETAINED_REPORT_BYTES
# Retaining a comparison keeps its baseline's exact bytes, not the baseline's
# own lineage: evidence a comparison still cites is sealed directly, so every
# recurring occurrence does not retain all earlier history.
BASELINE_ROLE = "security.baseline"
# Tool selections whose work identity is reproducible from a scan's own record;
# a live GitHub import also hashes its admitted request, which is not recorded.
_RECORDED_TOOLS = (
    (),
    ("inventory-feed",),
    ("retained-report",),
    ("inventory-feed", "retained-report"),
    ("retained-report", "inventory-feed"),
)


def _check_cancelled() -> None:
    if activity.in_activity() and activity.is_cancelled():
        raise asyncio.CancelledError()


def _feed_intelligence(execution) -> dict[str, str]:
    """Scanner version and the vulnerability/check data snapshot it read."""
    if execution is None:
        return {}
    values = {"version": execution.version or "unavailable"}
    snapshot = execution.feed.snapshot if execution.feed else None
    if snapshot is not None:
        values["feedSnapshot"] = snapshot.snapshot_id
    elif execution.database_digest:
        values["database"] = execution.database_digest
    return values


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
                evidence_method="deterministic_check",
                assets=assets,
                rule_ids=[rule.id for rule in feed.rules],
                status=inventory.status,
                config_ref=inventory.config_ref,
                intelligence={"feedRevision": feed.revision},
            )
        ],
        coverage_windows=[
            CoverageWindow(
                observed_at=inventory.observed_at,
                expires_at=min(inventory.expires_at, feed.expires_at),
                valid_from=max(inventory.observed_at, feed.observed_at),
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
                evidence_method="deterministic_check",
                assets=[
                    path
                    for path in native.covered_files
                    if path in request.scope.assets
                ],
                rule_ids=["credential", "private_key", "authorization", "token"],
                config_ref=content_key(native.to_payload()["config"]),
                status="partial" if native.verdict == "incomplete" else "complete",
                intelligence={"rules": content_key(native.to_payload()["config"])},
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
                intelligence=_feed_intelligence(summary.execution),
            )
        ],
        provenance={
            **(
                {"findingLimit": "combined_finding_bound_exceeded"} if truncated else {}
            ),
            "scannerSummary": ref,
            "nativeReports": content_key(summary.native_refs),
            "scannerImage": (
                summary.execution.source_image
                if summary.execution and summary.execution.source_image
                else "unavailable"
            ),
            # Full licenses, notices and source URLs remain in the retained
            # summary; identify that exact rule corpus in composed reports.
            "scannerRules": content_key(
                [
                    r.model_dump(mode="json", by_alias=True)
                    for r in admission.rules or []
                ]
                if admission
                else []
            ),
            "scannerVersion": (
                summary.execution.version if summary.execution else "unavailable"
            ),
            "inputDigest": (
                summary.execution.input_digest if summary.execution else "unavailable"
            ),
        },
    )


def _image_observation(
    request, summary: ImageAssessmentSummary, ref: str, created_at: datetime
) -> Observation:
    """Image and SBOM matches are reported as target coverage, apart from source."""
    result, subject, execution = summary.result, summary.subject, summary.execution
    if result is None or summary.admission.operation == "sbom-generate":
        raise ValueError("SBOM generation is inventory, not a vulnerability result")
    findings = result.findings
    if any(not set(f.evidence_refs) <= {summary.native_ref} for f in findings):
        raise ValueError("finding evidence differs from retained native records")
    if any(f.asset not in request.scope.assets for f in findings):
        raise ValueError("image finding outside admitted assessment scope")
    complete = (
        summary.coverage == "complete"
        and not summary.limitations
        and execution is not None
        and not execution.limitations
        and execution.state == "succeeded"
        and subject is not None
        and result.coverage == "complete"
        and result.execution == "succeeded"
        and subject.asset in result.examined_assets
    )
    assets = [a for a in result.examined_assets if a in request.scope.assets]
    version = execution.version if execution else "unavailable"
    return Observation(
        scope=request.scope,
        observed_at=created_at,
        expires_at=created_at + timedelta(days=1),
        findings=findings,
        coverage=[
            Coverage(
                source="image",
                tool="trivy",
                assets=assets,
                rule_ids=[],
                all_rules=execution is not None,
                # Feed snapshots are recorded below, not part of comparability:
                # reassessment compares one inventory across vulnerability data.
                config_ref=content_key(["image-vulnerabilities", version]),
                status="complete" if complete and assets else "partial",
                intelligence=_feed_intelligence(execution),
            )
        ],
        provenance={
            "imageSummary": ref,
            "imageSubject": subject.asset if subject else "unavailable",
            "imageNativeReport": summary.native_ref or "unavailable",
            "imageFeedSnapshot": (execution.database_digest if execution else None)
            or "unavailable",
            "imageScannerVersion": version,
        },
    )


def _verify_service_inventory_report(summary, native_body, attestation_body):
    """Recompute all coverage from retained bytes authorized by the artifact owner."""
    from moonmind.security.egress_conformance_evidence import (
        parse_and_verify_conformance_evidence,
    )

    plan = build_service_inventory_plan(
        summary.native_network, summary.service_inventory
    )
    pins = ()
    receipt_mismatch = False
    native = json.loads(native_body) if native_body is not None else None
    if attestation_body is not None:
        try:
            attestation = parse_and_verify_conformance_evidence(
                attestation_body, location="service-inventory-attestation"
            )
        except RuntimeError as exc:
            raise ValueError(
                "Service inventory attestation failed verification"
            ) from exc
        if (
            attestation.get("serviceInventoryPlanKey") != plan.plan_key
            or attestation.get("containerJobState") != summary.job_state
        ):
            raise ValueError(
                "Service inventory attestation differs from admitted plan or job"
            )
        try:
            pins = service_inventory_pinned_identities(plan, attestation)
        except (ValueError, TypeError, KeyError):
            pins = ()
        if pins and native:
            try:
                validate_service_inventory_receipt(plan, native, attestation)
            except (ValueError, TypeError, KeyError):
                receipt_mismatch = True
    normalized = summarize_service_inventory(
        plan,
        native,
        summary.evidence_ref,
        pinned_identities=pins,
        attestation_ref=summary.attestation_ref,
        job_state=summary.job_state,
        native_receipt_mismatch=receipt_mismatch,
    )
    if normalized != summary:
        raise ValueError("Service inventory summary differs from retained evidence")
    return normalized


def _service_inventory_observation(request, summary, ref, created_at):
    """Open TCP services are informational exposure observations, not vulnerabilities."""
    assets = [f"tcp://{endpoint.authority}" for endpoint in summary.endpoints]
    if set(assets) - set(request.scope.assets) or set(assets) & set(
        request.scope.exclusions
    ):
        raise ValueError(
            "Service inventory endpoints outside admitted assessment scope"
        )
    if summary.started_at is not None:
        created_at = summary.started_at
    rule = "informational-open-tcp-service"
    return Observation(
        scope=request.scope,
        observed_at=created_at,
        expires_at=created_at + timedelta(days=1),
        findings=[
            Finding(
                source="service-inventory",
                tool="tcp-connect",
                rule_id=rule,
                asset=f"tcp://{endpoint.authority}",
                native_id=endpoint.authority,
                evidence_refs=[summary.evidence_ref],
            )
            for endpoint in summary.endpoints
            if endpoint.reachability == "open" and summary.evidence_ref != "unavailable"
        ],
        coverage=[
            Coverage(
                source="service-inventory",
                tool="tcp-connect",
                assets=assets,
                rule_ids=[rule],
                all_rules=False,
                config_ref=summary.comparison_key or summary.plan_key,
                status="complete" if summary.status == "complete" else "partial",
                evidence_method="deterministic_check",
            )
        ],
        provenance={
            "serviceInventorySummary": ref,
            "serviceInventoryNativeReport": summary.evidence_ref,
            "serviceInventoryAttestation": summary.attestation_ref or "unavailable",
            "serviceInventoryInterpretation": summary.interpretation,
        },
    )


def _tls_observation(
    request, summary: TLSAssessmentSummary, ref: str, created_at: datetime
) -> Observation:
    """Positive sampled TLS evidence, without claiming backend-wide absence."""
    if (
        summary.origin not in request.scope.assets
        or summary.origin in request.scope.exclusions
    ):
        raise ValueError("TLS origin outside admitted assessment scope")
    findings = []
    for item in summary.findings:
        if (
            item.get("evidenceRef") != summary.evidence_ref
            or summary.evidence_ref == "unavailable"
        ):
            raise ValueError(
                "TLS finding evidence differs from retained native records"
            )
        findings.append(
            Finding(
                source="tls",
                tool="openssl",
                rule_id=item["code"],
                asset=summary.origin,
                native_id=content_key(
                    [item["code"], item["protocol"], item.get("cipher")]
                ),
                evidence_refs=[summary.evidence_ref],
            )
        )
    if summary.execution.get("startedAt"):
        created_at = datetime.fromisoformat(summary.execution["startedAt"])
    return Observation(
        scope=request.scope,
        observed_at=created_at,
        expires_at=created_at + timedelta(days=1),
        findings=findings,
        coverage=[
            Coverage(
                source="tls",
                tool="openssl",
                assets=[summary.origin],
                rule_ids=sorted({f.rule_id for f in findings}),
                all_rules=False,
                config_ref=summary.comparison_key,
                status="partial",
                evidence_method="deterministic_check",
            )
        ],
        provenance={
            "tlsSummary": ref,
            "tlsNativeReport": summary.evidence_ref,
            "tlsInterpretation": summary.interpretation,
        },
    )


def _web_observation(
    request, summary: WebAssessmentSummary, ref: str, created_at: datetime
) -> Observation:
    """ZAP findings as web coverage of one origin, mode and identity."""
    result, admission = summary.result, summary.admission
    if any(not set(f.evidence_refs) <= {summary.native_ref} for f in result.findings):
        raise ValueError("finding evidence differs from retained native records")
    if any(f.asset not in request.scope.assets for f in result.findings):
        raise ValueError("web finding outside admitted assessment scope")
    assets = [a for a in result.examined_assets if a in request.scope.assets]
    identity = admission.identity.name if admission.identity else "anonymous"
    return Observation(
        scope=request.scope,
        observed_at=created_at,
        expires_at=created_at + timedelta(days=1),
        findings=result.findings,
        coverage=[
            Coverage(
                source="web",
                tool="zap",
                assets=assets,
                rule_ids=[],
                all_rules=summary.status == "complete",
                # Mode, identity, admitted routes and the routes actually
                # tested decide what absence can mean: a finding on a route
                # this crawl never reached was not reassessed.
                config_ref=content_key(
                    [
                        "web",
                        admission.mode,
                        admission.active_strength,
                        identity,
                        [r.model_dump(mode="json") for r in admission.routes],
                        admission.exclusions,
                        sorted(summary.coverage.tested_routes),
                    ]
                ),
                status=(
                    "complete"
                    if summary.status == "complete" and assets
                    else (
                        "authentication_failed"
                        if summary.coverage.authentication is not None
                        and summary.coverage.authentication.status == "failed"
                        else "partial"
                    )
                ),
                intelligence={"version": summary.execution.version or "unavailable"},
            )
        ],
        provenance={
            "webSummary": ref,
            "webNativeReport": summary.native_ref or "unavailable",
            "webPlan": summary.plan_ref or "unavailable",
            "webMode": admission.mode,
            "webIdentity": identity,
        },
    )


def _cloud_control_evidence(summary: CloudAssessmentSummary):
    """Positive observations prove only executed checks in the admitted population.

    Prowler's retained epoch is in Unix seconds (also retained unchanged). Aware
    ISO timestamps are supported when no epoch is supplied; unknown timestamps
    cannot acquire the artifact's publication time as a new observation date.
    """
    checks = summary.check_evidence or []
    reasons, observed = set(), []
    admission = summary.admission
    if not checks or not summary.native_ref:
        reasons.add("check_records_unavailable")
    if summary.status != "complete" or summary.coverage.checks_without_results:
        reasons.add("collection_incomplete")
    by_rule = {}
    for check in checks:
        if admission.checks and check.check_id not in admission.checks:
            reasons.add("unadmitted_check_result")
        by_rule.setdefault(check.check_id, set()).add(check.region)
        if (
            check.provider != admission.provider
            or check.account != admission.account
            or check.region not in admission.regions
            or not check.resource_uids
        ):
            reasons.add("record_scope_unknown_or_unadmitted")
        if check.muted or check.status == "MANUAL":
            reasons.add("suppressed_or_manual_result")
        try:
            if check.observed_timestamp is not None:
                stamp = datetime.fromtimestamp(check.observed_timestamp, UTC)
            elif isinstance(check.observed_time, str):
                stamp = datetime.fromisoformat(check.observed_time)
                if stamp.tzinfo is None:
                    raise ValueError("unknown timezone")
            else:
                raise ValueError("no observation time")
            observed.append(stamp.astimezone(UTC))
        except (ValueError, OverflowError, OSError):
            reasons.add("observation_time_unknown")
    if any(set(admission.regions) - regions for regions in by_rule.values()):
        reasons.add("region_population_incomplete")
    if len({stamp.date() for stamp in observed}) > 1:
        reasons.add("multiple_observation_dates")
    known_times = observed and len(observed) == len(checks)
    return (
        sorted(reasons),
        min(observed) if known_times else None,
        max(observed) if known_times else None,
    )


def _cloud_observation(
    request, summary: CloudAssessmentSummary, ref: str, created_at: datetime
) -> Observation:
    """Prowler findings as cloud coverage of one account's admitted selection.

    The summary's own invariants already refuse a clean outcome over partial
    coverage; this keeps its status rather than deriving a better one.
    """
    result, admission = summary.result, summary.admission
    if any(not set(f.evidence_refs) <= {summary.native_ref} for f in result.findings):
        raise ValueError("finding evidence differs from retained native records")
    if any(f.asset not in request.scope.assets for f in result.findings):
        raise ValueError("cloud finding outside admitted assessment scope")
    assets = [a for a in result.examined_assets if a in request.scope.assets]
    if summary.status == "complete" and assets:
        status = "complete"
    elif any(d.kind == "credentials_rejected" for d in summary.diagnostics):
        status = "authentication_failed"
    elif summary.outcome == "execution_failed":
        status = "failed"
    else:
        status = "partial"
    control_limits, earliest, latest = _cloud_control_evidence(summary)
    # Every contributing record must already exist, while the oldest record
    # still decides expiry. Neither future nor stale rows borrow another's age.
    observed_at = latest or created_at
    return Observation(
        scope=request.scope,
        observed_at=observed_at,
        expires_at=(earliest or created_at) + timedelta(days=1),
        findings=result.findings,
        coverage=[
            Coverage(
                source="cloud",
                tool="prowler",
                assets=assets,
                rule_ids=summary.coverage.checks_with_results,
                # Only named results can prove a check, never an omitted rule.
                all_rules=False,
                evidence_method=(
                    "unclassified" if control_limits else "deterministic_check"
                ),
                # Absence is comparable only for the same regions, checks or
                # services and Prowler version.
                config_ref=content_key(
                    [
                        "cloud",
                        admission.provider,
                        sorted(admission.regions),
                        sorted(admission.checks),
                        sorted(admission.services),
                        summary.execution.version,
                    ]
                ),
                status=status,
                intelligence={"version": summary.execution.version or "unavailable"},
            )
        ],
        provenance={
            "cloudSummary": ref,
            "cloudNativeReport": summary.native_ref or "unavailable",
            "cloudAccount": f"{admission.provider}:{admission.account}",
            "cloudOutcome": summary.outcome,
            "cloudControlEvidence": (
                ", ".join(control_limits) if control_limits else "executed_checks_only"
            ),
        },
    )


def _game_math_observation(
    request,
    summary: GameMathAssessment,
    ref: str,
    created_at: datetime,
    *,
    verified=False,
    case_basis=None,
) -> Observation:
    """Specification violations as game-math coverage of one game.

    Coverage is complete only when every check reached a decision, so
    insufficient precision or a stopped run stays partial. The summary's own
    invariants refuse a consistent outcome over partial evidence.
    """
    if summary.game_ref not in request.scope.assets:
        raise ValueError("game outside admitted assessment scope")
    rules = {row.rule for row in summary.violations if row.rule != "simulated-rtp"}
    if any(row.declared_rtp is not None for row in summary.theoretical):
        rules.add("declared-rtp")
    if any(row.declared_immediate_bust is not None for row in summary.theoretical):
        rules.add("declared-immediate-bust")
    simulation = summary.simulation
    if summary.settlement_cases or (simulation and simulation.source == "records"):
        rules |= {"settlement", "outcome-support"}
    verified_basis = ["retained-input-replay"] if verified else []
    coverage = [
        Coverage(
            evidence_method="deterministic_check" if verified else "unclassified",
            source="game-math",
            tool="game-math",
            assets=[summary.game_ref],
            rule_ids=sorted(rules),
            config_ref=content_key(["game-math", summary.spec_digest, *verified_basis]),
            status="complete" if summary.covered else "partial",
        )
    ]
    if verified and rules & {"settlement", "outcome-support"}:
        coverage[0].rule_ids = sorted(rules - {"settlement", "outcome-support"})
        coverage.append(
            Coverage(
                source="game-math",
                tool="game-math",
                assets=[summary.game_ref],
                rule_ids=["settlement", "outcome-support"],
                evidence_method="deterministic_check",
                config_ref=content_key(["game-math", summary.spec_digest, case_basis]),
                status="complete" if summary.covered else "partial",
            )
        )
    if simulation is not None:
        coverage.append(
            Coverage(
                evidence_method="deterministic_check" if verified else "unclassified",
                source="game-math",
                tool="game-math",
                assets=[summary.game_ref],
                rule_ids=["simulated-rtp"],
                # A model run cannot establish absence of an implementation's
                # distribution defect, even under the same specification.
                config_ref=content_key(
                    [
                        "game-math",
                        summary.spec_digest,
                        simulation.source,
                        *verified_basis,
                    ]
                ),
                status="complete" if summary.covered else "partial",
            )
        )
    return Observation(
        scope=request.scope,
        observed_at=created_at,
        expires_at=created_at + timedelta(days=1),
        findings=[
            Finding(
                source="game-math",
                tool="game-math",
                rule_id=row.rule,
                asset=summary.game_ref,
                native_id=row.identity,
                evidence_refs=[ref, *summary.job.input_refs.values()]
                if verified
                else [ref],
            )
            for row in summary.violations
        ],
        coverage=coverage,
        provenance={
            "gameMathSummary": ref,
            "gameMathSpec": summary.spec_digest,
            "gameMathSource": summary.provenance.source_ref,
            "gameMathApproval": summary.provenance.approval_ref or "unavailable",
            "gameMathOutcome": summary.outcome,
            "gameMathInputVerification": "reproduced" if verified else "unverified",
        },
    )


def _verify_application_access_report(summary, spec_body, capture_body):
    from moonmind.security.application_access_target import (
        ApplicationAccessCapture,
        validate_capture_spec,
    )

    if summary.job is None:
        raise ValueError("application access report requires retained job inputs")
    spec = ApplicationAccessSpec.model_validate_json(spec_body)
    capture = ApplicationAccessCapture.model_validate_json(capture_body)
    validate_capture_spec(spec, capture)
    verify_report_evidence(summary, spec, capture.bundle, spec_body, capture_body)
    return spec, capture


def _application_access_observation(request, summary, spec, capture, ref, created_at):
    origin = capture.scope.web.origin
    if origin not in request.scope.assets:
        raise ValueError("application access target outside admitted assessment scope")
    evidence = [ref, summary.job.input_refs["spec"], summary.job.input_refs["capture"]]
    # The reviewed case and complete target scope are part of comparability.
    # Removing an identity, object, predicate or route cannot establish a fix.
    config = content_key(
        ["application-access", summary.spec_digest, capture.scope_digest]
    )
    return Observation(
        scope=request.scope,
        observed_at=created_at,
        expires_at=created_at + timedelta(days=1),
        findings=[
            Finding(
                source="application-access",
                tool="application-access",
                rule_id=row.case_ref,
                asset=origin,
                native_id=content_key(
                    [row.case_ref, row.identity_ref, row.resource_ref]
                ),
                evidence_refs=evidence,
            )
            for row in summary.cases
            if row.outcome == "finding"
        ],
        coverage=[
            Coverage(
                source="application-access",
                tool="application-access",
                assets=[origin],
                rule_ids=[row.case_ref for row in summary.cases],
                config_ref=config,
                status=summary.status,
                evidence_method="deterministic_check",
            )
        ],
        provenance={
            "applicationAccessSummary": ref,
            "applicationAccessSpec": summary.spec_digest,
            "applicationAccessScope": capture.scope_digest,
            "applicationAccessPolicy": spec.policy_ref,
            "applicationAccessPolicyReview": spec.policy_review_ref,
        },
    )


def _rgs_observation(request, summary: RgsAssessment, ref: str, created_at: datetime):
    if summary.game_ref not in request.scope.assets:
        raise ValueError("game outside admitted assessment scope")
    violations = [row for row in summary.checks if row.decision == "violation"]
    omitted = max(0, len(violations) - 4096)
    return Observation(
        scope=request.scope,
        observed_at=created_at,
        expires_at=created_at + timedelta(days=1),
        findings=[
            Finding(
                source="rgs-scenarios",
                tool="rgs-scenarios",
                rule_id=row.rule,
                asset=summary.game_ref,
                native_id=f"{row.scenario}:{row.rule}:{row.operation}",
                evidence_refs=[ref],
            )
            for row in violations[:4096]
        ],
        coverage=[
            Coverage(
                source="rgs-scenarios",
                tool="rgs-scenarios",
                assets=[summary.game_ref],
                rule_ids=sorted({row.rule for row in summary.checks}),
                config_ref=content_key(
                    ["rgs-scenarios", summary.spec_digest, summary.traces_source_ref]
                ),
                status=(
                    "complete"
                    if summary.status == "complete" and not omitted
                    else "partial"
                ),
            )
        ],
        provenance={
            "rgsSummary": ref,
            "rgsSpec": summary.spec_digest,
            "rgsTraces": summary.traces_digest,
            **({"rgsFindingsOmitted": str(omitted)} if omitted else {}),
        },
    )


def _rng_observation(request, summary: RngAssessment, ref: str, created_at: datetime):
    """Frequency coverage names only the performed raw or final-outcome tests."""
    if summary.game_ref not in request.scope.assets:
        raise ValueError("game outside admitted assessment scope")
    coverage, findings = [], []
    for section, run in (("raw", summary.raw), ("mapped", summary.mapped)):
        if run is None:
            continue
        for row in run.results:
            rule = f"rng-{section}-{row.test}"
            decided = row.decision in {"anomaly", "no_anomaly_detected"}
            coverage.append(
                Coverage(
                    source="rng",
                    tool="rng",
                    evidence_method="deterministic_check",
                    assets=[summary.game_ref],
                    rule_ids=[rule],
                    config_ref=content_key(
                        ["rng", summary.spec_digest, section, row.test]
                    ),
                    status=(
                        "complete"
                        if decided and summary.status == "complete"
                        else "partial"
                    ),
                )
            )
            if row.decision == "anomaly":
                findings.append(
                    Finding(
                        source="rng",
                        tool="rng",
                        rule_id=rule,
                        asset=summary.game_ref,
                        native_id=rule,
                        evidence_refs=[ref],
                    )
                )
    # A review-only report accounts for missing reviewer evidence explicitly;
    # diagnostic success never establishes implementation or crypto assurance.
    if not coverage:
        coverage.append(
            Coverage(
                source="rng",
                tool="rng",
                assets=[summary.game_ref],
                rule_ids=["implementation-review"],
                evidence_method="agent_interpretation",
                config_ref=summary.spec_digest,
                status=(
                    "complete"
                    if summary.status == "complete"
                    and all(
                        r.status != "unverified" for r in summary.implementation_review
                    )
                    else "partial"
                ),
            )
        )
    return Observation(
        scope=request.scope,
        observed_at=created_at,
        expires_at=created_at + timedelta(days=1),
        findings=findings,
        coverage=coverage,
        provenance={
            "rngSummary": ref,
            "rngSpec": summary.spec_digest,
            "rngOutcome": summary.outcome,
            "rngCryptographicSecurity": "unverified",
        },
    )


class AssessmentExecution:
    def __init__(
        self,
        service: Any,
        *,
        principal: str,
        context: Mapping[str, Any],
        access: ArtifactAccessContext,
    ):
        self.service = service
        self.principal = principal
        self.access = ArtifactAccessContext.require(access)
        self.context = context
        self.execution = {
            "namespace": context["namespace"],
            "workflow_id": context["workflow_id"],
            "run_id": context["run_id"],
        }
        # Digests of the exact bytes this execution read or wrote.
        self.digests: dict[str, str] = {}
        # Seals also verify history an admitted baseline's own seal verified.
        self.seal_access = self.access

    async def read(self, ref: str) -> tuple[Any, bytes]:
        _check_cancelled()
        # Always authorize current inputs before consulting a saved result.
        artifact, body = await self.service.read(artifact_id=ref, access=self.access)
        if len(body) > _MAX_INPUT_BYTES:
            raise ToolFailure(
                "INVALID_INPUT", "Security artifact exceeds the input bound.", False
            )
        self.digests[ref] = hashlib.sha256(body).hexdigest()
        return artifact, body

    async def digest(self, ref: str) -> str:
        if ref not in self.digests:
            await self.read(ref)
        return self.digests[ref]

    async def seal(self, root: str, dependencies) -> None:
        """Certify the exact evidence a root cites before it is reused or published.

        An existing seal is immutable, so a retry keeps it. Missing, denied,
        corrupt or mismatched required evidence fails the seal.
        """
        if await _proof(self.service, root, self.access) is None:
            await _record(self.service, root, await dependencies(), self.seal_access)

    async def _scan_dependencies(self, refs, work, history):
        return [
            *(
                ArtifactDependency(ref, self.digests[ref], True, "security.input")
                for ref in refs
            ),
            *[
                ArtifactDependency(ref, await self.digest(ref), True, "security.native")
                for ref in _native_refs(work)
            ],
            *_history_evidence(_scan_citations(work), history),
        ]

    async def game_math_inputs(self, ref, summary):
        """Recheck current job/input authority without repeating completed math."""
        from moonmind.security.game_math_evidence import MAX_INPUT_BYTES

        refs = {"assessment": ref}
        refs["jobManifest"] = await self.service.verify_game_math_job_output(
            artifact_id=ref, job=summary.job, access=self.access
        )
        bodies = {}
        for name, input_ref in summary.job.input_refs.items():
            _, bodies[name] = await self.read(input_ref)
            refs[name] = input_ref
        if (
            sum(map(len, bodies.values())) > MAX_INPUT_BYTES
            or {name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()}
            != summary.job.input_digests
        ):
            raise ValueError("game math retained inputs differ or exceed their bound")
        return refs, bodies

    async def revalidate_game_math_inputs(self, inputs):
        for ref, (_, raw) in inputs.items():
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            if (
                isinstance(payload, dict)
                and payload.get("schemaVersion") == "game-math-assessment/v1"
                and payload.get("job") is not None
            ):
                await self.game_math_inputs(
                    ref, GameMathAssessment.model_validate(payload)
                )
    async def product_evidence(self, components, records=()) -> list[str]:
        """Check retained evidence through the existing artifact authority.

        A URL or a producer's availability label does not prove record access.
        Missing private records remain reportable, without admitting their bytes
        or making them required dependencies of otherwise completed work.
        """
        rows = [*components, *records]
        refs = list(
            dict.fromkeys(
                ref for row in rows for ref in _artifact_refs(row.evidence_refs)
            )
        )
        if len(refs) > 128:
            raise ValueError("product evidence exceeds the reference bound")
        available, unavailable = [], {}
        total = 0
        for ref in refs:
            try:
                _, body = await self.read(ref)
                total += len(body)
                if total > _MAX_INPUT_BYTES * 2:
                    raise ValueError("product evidence exceeds the byte bound")
                available.append(ref)
            except (
                TemporalArtifactNotFoundError,
                TemporalArtifactAuthorizationError,
                TemporalArtifactStateError,
                TemporalArtifactValidationError,
                TemporalArtifactStorageAccessError,
                TemporalArtifactUnavailableError,
            ) as exc:
                unavailable[ref] = type(exc).__name__
        for row in rows:
            artifact_refs = _artifact_refs(row.evidence_refs)
            missing = [
                f"{ref} ({unavailable[ref]})"
                for ref in artifact_refs
                if ref in unavailable
            ]
            reason = (
                "No readable retained artifact evidence; external references are Unverified."
                if not artifact_refs
                else "Evidence unavailable: " + ", ".join(missing) if missing else None
            )
            if reason:
                if hasattr(row, "stage"):
                    row.status = "unverified"
                    row.reason = f"{row.reason}; {reason}"[:1024]
                else:
                    row.omission_reason = "; ".join(
                        filter(None, [row.omission_reason, reason])
                    )[:1024]
        return available

    async def write(
        self, payload: bytes, *, kind: str, key: str, content_type="application/json"
    ) -> str:
        _check_cancelled()
        artifact, _ = await self.service.put_content_addressed_payload_complete(
            principal=self.principal,
            payload=payload,
            content_type=content_type,
            scope="security-assessments",
            redaction_level="restricted",
            operator_output=True,
            link={**self.execution, "link_type": kind},
            metadata_json={
                "artifact_type": "security_assessment",
                "producer": TOOL_NAME,
                "subject": key,
                "sensitivity": "restricted",
                "step_id": self.context["node_id"],
            },
        )
        self.digests[artifact.artifact_id] = hashlib.sha256(payload).hexdigest()
        return artifact.artifact_id

    async def saved(
        self, *, kind: str, key: str, latest: bool = False
    ) -> tuple[str, dict] | None:
        artifacts = await self.service.list_for_execution(
            **self.execution, link_type=kind, access=self.access
        )
        # Immutable results reuse their original bytes; a checkpoint journal
        # resumes its newest recorded budget and cursor state.
        for artifact in artifacts if latest else reversed(artifacts):
            if (artifact.metadata_json or {}).get("subject") != key:
                continue
            if getattr(artifact.status, "value", artifact.status) != "complete":
                continue
            _, body = await self.read(artifact.artifact_id)
            return artifact.artifact_id, json.loads(body)
        return None

    async def _import_github_alerts(
        self, request: AssessmentRequest, observations: list, scan_key: str
    ) -> dict:
        from moonmind.workflows.adapters.github_service import GitHubService

        native, usage = await GitHubService().read_security_alerts(
            request.github_alerts, admitted_workflow_id=self.execution["workflow_id"]
        )
        # Sanitized native evidence is durable before any projection.
        native_ref = await self.write(
            _json(native), kind="security.github-alerts", key=scan_key
        )
        observed_at = datetime.fromisoformat(usage["completedAt"])
        imported, projection = alert_observation(
            request, native, native_ref, observed_at
        )
        projection["interval"] = {
            "startedAt": usage["startedAt"],
            "completedAt": usage["completedAt"],
        }
        projection["usage"] = {
            key: usage[key] for key in ("requests", "items", "bytes")
        }
        projection["correlation"] = correlate_alerts(native, imported, observations)
        observations.append(imported)
        return {
            "projection": projection,
            "outcome": "complete" if imported.complete(observed_at) else "incomplete",
        }

    async def prepare_framework(
        self, request: AssessmentRequest, *, collected_evidence_refs=()
    ) -> tuple[str, dict] | None:
        if request.framework is None:
            return None
        from moonmind.security.frameworks import (
            FrameworkEvidence,
            FrameworkPack,
            load_framework_pack,
        )

        selection = request.framework
        pack = load_framework_pack(selection.pack_id)
        if pack.id == "gli-19-readiness":
            from moonmind.security.gli_readiness import (
                GLIEvidence,
                GLISystemInputs,
                prepare_assessment,
            )

            system = GLISystemInputs.model_validate(selection.system_inputs)
            systems = set(system.systems)
            evidence_type, prepare = GLIEvidence, prepare_assessment
            if selection.audit_ref:
                raise ValueError("GLI evidence belongs in evidenceRefs")
        elif pack.id == "nj-dge-readiness":
            from moonmind.security.nj_dge import (
                AnnualAssessmentRecords,
                NJEvidence,
                NJSystemInputs,
                prepare_assessment,
            )

            systems = set(
                NJSystemInputs.model_validate(selection.system_inputs).systems
            )
            audit_type, evidence_type = AnnualAssessmentRecords, NJEvidence
            prepare = prepare_assessment
        else:
            from moonmind.security.ukgc_rts import (
                AuditRecord,
                UKGCSystemInputs,
                prepare_audit,
            )

            system = UKGCSystemInputs.model_validate(selection.system_inputs)
            systems = {
                s
                for row in system.critical_systems
                if row.in_scope
                for s in row.systems
            }
            audit_type, evidence_type = AuditRecord, FrameworkEvidence
            prepare = prepare_audit
        if not systems <= set(request.scope.assets):
            raise ValueError("Framework systems exceed admitted assessment scope")
        audit = None
        evidence_refs = list(
            dict.fromkeys([*selection.evidence_refs, *collected_evidence_refs])
        )
        refs = list(evidence_refs)
        previous = None
        if selection.previous_assessment_ref:
            _, body = await self.read(selection.previous_assessment_ref)
            previous = json.loads(body)
            previous_pack = FrameworkPack.model_validate(
                previous.get("pack") if isinstance(previous, dict) else None
            )
            if previous_pack.id != pack.id:
                raise ValueError(
                    "Previous assessment must be a report for the selected framework"
                )
            refs.append(selection.previous_assessment_ref)
        if pack.id == "gli-19-readiness":
            source_ref = system.jurisdiction_basis.source_ref
            if source_ref and ARTIFACT_ID_PATTERN.fullmatch(source_ref):
                refs.append(source_ref)
        if selection.audit_ref:
            _, body = await self.read(selection.audit_ref)
            audit = audit_type.model_validate_json(body)
            refs.append(selection.audit_ref)
            if pack.id == "nj-dge-readiness":
                refs.extend(audit.supporting_refs())
            elif audit.auditor.independence_ref:
                refs.append(audit.auditor.independence_ref)
        records, invalid = [], []
        for ref in evidence_refs:
            _, body = await self.read(ref)
            try:
                # The artifact owner supplies this identity, not authored JSON.
                payload = json.loads(body)
                evidence = evidence_type.model_validate({**payload, "artifactRef": ref})
            except (ValueError, TypeError):
                invalid.append(ref)
                continue
            records.append(evidence.model_dump(mode="json", by_alias=True))
            if pack.id == "nj-dge-readiness":
                refs.extend(evidence.supporting_refs())
            if ARTIFACT_ID_PATTERN.fullmatch(evidence.provenance.source_ref):
                refs.append(evidence.provenance.source_ref)
            if evidence.reliance:
                refs.append(evidence.reliance.review_ref)
        for ref in dict.fromkeys(refs):
            await self.digest(ref)
        report = prepare(
            selection,
            evidence=records,
            audit=audit.model_dump(mode="json", by_alias=True) if audit else None,
        )
        if previous is not None:
            if previous.get("schemaVersion") != report["schemaVersion"]:
                raise ValueError(
                    "Previous assessment must use the selected report schema"
                )
            report["reassessment"] = {
                "previousRef": selection.previous_assessment_ref,
                "previousEdition": previous["pack"]["edition"],
                "packChanged": previous["pack"] != report["pack"],
                "jurisdictionBasisChanged": previous.get("jurisdictionBasis")
                != report.get("jurisdictionBasis"),
            }
        for ref in invalid:
            prompt = "Supply a scoped evidence record with provenance, period, systems, control IDs and enquiry/evidence/observation method; an uploaded document alone cannot establish control coverage."
            if pack.id == "gli-19-readiness":
                prompt += " Include evidenceType, outcome, edition and provenance.standard (module and edition); regulator evidence also needs jurisdiction."
            report["evidenceRequests"].append(
                {
                    "id": "evidence." + ref,
                    "artifactRef": ref,
                    "prompt": prompt,
                }
            )
            report["readiness"] = "incomplete"
        key = content_key(
            {
                "request": selection.model_dump(mode="json", by_alias=True),
                "pack": pack.model_dump(mode="json", by_alias=True),
                "evidence": {ref: self.digests[ref] for ref in sorted(set(refs))},
            }
        )
        saved = await self.saved(kind="security.framework", key=key)
        if saved:
            root, report = saved
        else:
            root = await self.write(_json(report), kind="security.framework", key=key)

        async def dependencies():
            return [
                ArtifactDependency(
                    ref, await self.digest(ref), True, "security.framework-input"
                )
                for ref in dict.fromkeys(refs)
            ]

        await self.seal(root, dependencies)
        return root, report

    async def prepare_framework_pack(self, request):
        """Resolve exact artifact bytes before collecting any assessment evidence."""
        selection = request.framework_pack
        if selection is None:
            return request, None
        from moonmind.security.evidence_exports import validation_summary
        from moonmind.security.framework_packs import (
            VersionedFrameworkPack,
            compare_pack_metadata,
            compile_framework_plan,
            load_declared_pack,
            retained_pack_metadata,
        )

        try:
            _, raw = await self.read(selection.artifact_ref)
            if self.digests[selection.artifact_ref] != selection.sha256:
                raise ValueError("framework pack content digest differs from selection")
            pack = load_declared_pack(
                raw,
                pack_id=selection.pack_id,
                edition=selection.edition,
                revision=selection.revision,
            )
            now = datetime.now(UTC)
            plan, diagnostics = compile_framework_plan(
                pack,
                scope=request.scope,
                period=selection.period,
                choices=selection.requirement_choices,
                now=now,
            )
            metadata = retained_pack_metadata(
                pack,
                artifact_ref=selection.artifact_ref,
                sha256=selection.sha256,
                now=now,
            )
        except ValidationError as exc:
            # Both Pydantic values and authored extra-field locations can carry
            # private source text. Preserve only declared fields and error types.
            raise ToolFailure(
                "INVALID_INPUT",
                "Invalid framework pack: "
                + validation_summary(exc, VersionedFrameworkPack),
                False,
            ) from None
        except ValueError:
            raise ToolFailure(
                "INVALID_INPUT",
                "Invalid framework pack: value_error. Check declared identity, digest, distribution and assessment period.",
                False,
            ) from None
        previous, previous_ref = None, None
        if selection.previous_assessment_ref:
            try:
                _, raw = await self.read(selection.previous_assessment_ref)
                retained = ControlAssessment.model_validate_json(raw)
                previous = retained.framework_pack
                if previous is None or previous.get("id") != pack.id:
                    raise ValueError("previous assessment is for a different framework")
                if (
                    await _proof(
                        self.service, selection.previous_assessment_ref, self.access
                    )
                    is None
                ):
                    raise ValueError(
                        "previous assessment has no retained evidence seal"
                    )
                previous_ref = selection.previous_assessment_ref
            except (
                TemporalArtifactNotFoundError,
                TemporalArtifactAuthorizationError,
                TemporalArtifactStateError,
                TemporalArtifactValidationError,
                TemporalArtifactStorageAccessError,
                TemporalArtifactUnavailableError,
                ToolFailure,
                ValueError,
            ):
                previous = None
                diagnostics.append(
                    "Previous framework assessment is unavailable, invalid or unsealed; assessment-intelligence comparison is Unverified."
                )
        if selection.previous_assessment_ref:
            metadata["previousAssessmentRef"] = selection.previous_assessment_ref
            metadata["previousAssessmentStatus"] = (
                "available" if previous_ref else "unavailable"
            )
        return request.model_copy(update={"control_assessment": plan}), {
            "metadata": metadata,
            "diagnostics": diagnostics,
            "changes": compare_pack_metadata(metadata, previous),
            "previousRef": previous_ref,
        }

    async def prepare_controls(
        self,
        request,
        comparison,
        *,
        scan_ref,
        comparison_ref,
        now,
        review,
        review_status,
        review_readable,
        framework_pack_context=None,
    ):
        if request.control_assessment is None:
            return None
        from moonmind.security.control_assessments import assess_controls

        availability, readable, total = {}, [], 0
        refs = dict.fromkeys(
            evidence.artifact_ref
            for row in request.control_assessment.requirements
            for evidence in row.supporting_evidence
        )
        for ref in refs:
            try:
                if total > _MAX_INPUT_BYTES * 2:
                    raise ValueError("control supporting evidence exceeds byte bound")
                _, body = await self.read(ref)
                total += len(body)
                if total > _MAX_INPUT_BYTES * 2:
                    raise ValueError("control supporting evidence exceeds byte bound")
                readable.append(ref)
                availability[ref] = None
            except (
                TemporalArtifactNotFoundError,
                TemporalArtifactAuthorizationError,
                TemporalArtifactStateError,
                TemporalArtifactValidationError,
                TemporalArtifactStorageAccessError,
                TemporalArtifactUnavailableError,
                ToolFailure,
                ValueError,
            ) as exc:
                availability[ref] = type(exc).__name__
        report = assess_controls(
            request.control_assessment,
            Comparison.model_validate(comparison),
            scan_ref=scan_ref,
            comparison_ref=comparison_ref,
            now=now,
            coverage_windows=comparison["observation"].get("coverageWindows", []),
            evidence_availability=availability,
            review_ref=request.review_ref,
            review_status=review_status,
        )
        if framework_pack_context is not None:
            report = report.model_copy(
                update={
                    "framework_pack": framework_pack_context["metadata"],
                    "intelligence_changes": framework_pack_context["changes"],
                    "diagnostics": framework_pack_context["diagnostics"],
                }
            )
        document = report.model_dump(mode="json", by_alias=True)
        # Reevaluate accessibility, freshness and triage on every invocation;
        # identical effective evidence reuses its immutable original assessment.
        key = content_key(
            [
                {k: v for k, v in document.items() if k != "evaluatedAt"},
                {ref: self.digests[ref] for ref in readable},
            ]
        )
        saved = await self.saved(kind="security.control-assessment", key=key)
        if saved:
            root, document = saved
            report = ControlAssessment.model_validate(document)
        else:
            root = await self.write(
                _json(document), kind="security.control-assessment", key=key
            )

        async def dependencies():
            required = [scan_ref, comparison_ref, *readable]
            if framework_pack_context is not None:
                required.append(request.framework_pack.artifact_ref)
            if review_readable:
                required.append(request.review_ref)
            if review is not None:
                required += review.evidence_refs()
            entries = [
                ArtifactDependency(
                    ref, await self.digest(ref), True, "security.control-evidence"
                )
                for ref in dict.fromkeys(required)
            ]
            if framework_pack_context and framework_pack_context["previousRef"]:
                # Only the prior report's retained pack metadata contributes to
                # this comparison. Its old scan closure is not current evidence.
                ref = framework_pack_context["previousRef"]
                entries.append(
                    ArtifactDependency(ref, await self.digest(ref), True, BASELINE_ROLE)
                )
            return entries

        await self.seal(root, dependencies)
        return root, report

    async def execute(self, request: AssessmentRequest) -> ToolResult:
        request, framework_pack_context = await self.prepare_framework_pack(request)
        change_control = None
        if request.github_change_control is not None:
            from moonmind.workflows.adapters.github_service import GitHubService

            root, review_ref, report = await GitHubService().read_change_control(
                request.github_change_control, execution=self
            )
            change_control = {
                "evidenceRef": root,
                "reviewEvidenceRef": review_ref,
                **{
                    key: report[key]
                    for key in (
                        "populationRef",
                        "sampleRef",
                        "coverageStatus",
                        "counts",
                        "limitations",
                        "controlOperation",
                    )
                },
            }
        identity = None
        if request.identity_lifecycle is not None:
            from moonmind.security.okta_evidence import collect_identity

            try:
                root, review_ref, report = await collect_identity(
                    self, request.identity_lifecycle
                )
            except (ValueError, TypeError):
                raise ToolFailure(
                    "INVALID_INPUT",
                    "Invalid identity evidence inputs or egress admission.",
                    False,
                ) from None
            identity = {
                "evidenceRef": root,
                "reviewEvidenceRef": review_ref,
                "counts": report["counts"],
                "limitations": report["limitations"],
                "controlOperation": "not_established",
            }
        try:
            framework = await self.prepare_framework(
                request,
                collected_evidence_refs=(
                    ([identity["reviewEvidenceRef"]] if identity else [])
                    + ([change_control["reviewEvidenceRef"]] if change_control else [])
                ),
            )
        except (ValueError, TypeError) as exc:
            raise ToolFailure(
                "INVALID_INPUT", "Invalid framework assessment inputs.", False
            ) from exc
        now = datetime.now(UTC)
        refs = [*request.report_refs]
        if request.inventory_ref:
            refs += [request.inventory_ref, request.feed_ref]
        inputs = {ref: await self.read(ref) for ref in refs}
        baseline = None
        baseline_unavailable = False
        # Evidence identities the baseline's own seal already verified.
        history: dict[str, str] = {}
        diagnostics = []
        scan_diagnostics = []
        product_baseline = None
        product_baseline_error = None
        product_refs = []
        if request.product_baseline_ref:
            try:
                _, raw = await self.read(request.product_baseline_ref)
                product_refs.append(request.product_baseline_ref)
                product_baseline = ProductBaseline.model_validate_json(raw)
                if product_baseline.product not in request.scope.assets:
                    raise ValueError("product baseline outside admitted scope")
                product_refs += await self.product_evidence(
                    [
                        *product_baseline.components,
                        *[
                            change.component
                            for change in product_baseline.approved_changes
                        ],
                    ],
                    [
                        *product_baseline.records,
                        *[
                            change.record
                            for change in product_baseline.approved_changes
                        ],
                    ],
                )
            except (
                TemporalArtifactNotFoundError,
                TemporalArtifactAuthorizationError,
                TemporalArtifactStateError,
                TemporalArtifactValidationError,
                TemporalArtifactStorageAccessError,
                TemporalArtifactUnavailableError,
                ValidationError,
                ValueError,
            ) as exc:
                product_baseline = None
                product_baseline_error = f"Selected product baseline is unavailable or invalid: {type(exc).__name__}. Approval is Unverified."
                diagnostics.append(
                    {"boundary": "product-baseline", "error": type(exc).__name__}
                )
        if request.baseline_ref:
            try:
                artifact, raw = await self.read(request.baseline_ref)
                parsed = Comparison.model_validate_json(raw).model_dump(
                    mode="json", by_alias=True
                )
                # Admission consumes the baseline's seal rather than rediscovering
                # its references; expired or corrupt bytes are not a baseline.
                proof = await _proof(self.service, request.baseline_ref, self.access)
                if proof is None:
                    # An unsealed baseline gets lifecycle backfill's schema-aware
                    # seal first. No seal is an unverified history, not an
                    # empty one, so a baseline that cannot be sealed is
                    # unavailable.
                    await self.service.seal_admitted_assessment_root(
                        root_artifact_id=request.baseline_ref, access=self.access
                    )
                    proof = await _proof(
                        self.service, request.baseline_ref, self.access
                    )
                history = {
                    entry.artifact_id: entry.sha256 for entry in proof if entry.required
                }
                history[request.baseline_ref] = self.digests[request.baseline_ref]
                # This execution admitted the baseline, so its seal may verify
                # the history that seal certified, which a later occurrence in
                # another workflow cannot read. Ordinary reads stay unchanged.
                self.seal_access = self.access._with_verified_inputs(history)
                baseline = parsed
            except (
                TemporalArtifactNotFoundError,
                TemporalArtifactAuthorizationError,
                TemporalArtifactStateError,
                TemporalArtifactValidationError,
                ValidationError,
                ValueError,
            ) as exc:
                history = {}
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
        # A supplied decision is admitted only with artifact evidence readable
        # now; the comparison seals these exact bytes. Carried decisions use
        # history.
        decision_evidence = list(
            dict.fromkeys(
                _artifact_refs(ref for item in decisions for ref in item.evidence_refs)
            )
        )
        for ref in decision_evidence:
            await self.digest(ref)
        work_identity = {
            "scope": request.scope.model_dump(mode="json"),
            "tools": request.tools,
            "inputs": {
                ref: hashlib.sha256(body).hexdigest()
                for ref, (_, body) in inputs.items()
            },
        }
        if request.github_alerts is not None:
            # Live alerts are reread by each execution; only this execution's
            # retries reuse its saved import, with the same admitted request.
            work_identity["githubAlerts"] = {
                "request": request.github_alerts.model_dump(mode="json"),
                "execution": self.execution,
            }
        if identity:
            work_identity["identityEvidence"] = identity["evidenceRef"]
        if change_control:
            work_identity["githubChangeEvidence"] = change_control["evidenceRef"]
        scan_key = content_key(work_identity)
        saved = await self.saved(kind="security.scan", key=scan_key)
        if saved:
            await self.revalidate_game_math_inputs(inputs)
            scan_ref, work = saved
            observation = Observation.model_validate(work["observation"])
            await self.seal(
                scan_ref, lambda: self._scan_dependencies(refs, work, history)
            )
        else:
            observations = []
            product_scan_refs = []
            outcomes = []
            scanner_summaries = []
            github_alerts = None
            if (
                baseline
                and baseline["observation"]["provenance"].get("assessmentWork")
                == scan_key
            ):
                await self.revalidate_game_math_inputs(inputs)
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
                        elif payload.get("schemaVersion") == "image-assessment/v1":
                            image_summary = ImageAssessmentSummary.model_validate(
                                payload
                            )
                            if image_summary.native_ref:
                                await self.read(image_summary.native_ref)
                            observed_at = metadata.created_at
                            if observed_at.tzinfo is None:
                                observed_at = observed_at.replace(tzinfo=UTC)
                            observation = _image_observation(
                                request, image_summary, ref, observed_at
                            )
                            scanner_summaries.append(
                                {
                                    "scanner": "trivy",
                                    "target": (
                                        image_summary.subject.asset
                                        if image_summary.subject
                                        else "unavailable"
                                    ),
                                    "summaryRef": ref,
                                    "nativeRefs": (
                                        {"report": image_summary.native_ref}
                                        if image_summary.native_ref
                                        else {}
                                    ),
                                    "coverage": observation.coverage[0].status,
                                    "execution": {
                                        "report": image_summary.result.execution
                                    },
                                    "limitations": sorted(
                                        set(image_summary.limitations)
                                        | set(image_summary.result.limitations)
                                    ),
                                    "verification": image_summary.verification,
                                    "interpretation": image_summary.interpretation,
                                }
                            )
                        elif payload.get("schemaVersion") == "service-inventory/v1":
                            inventory_summary = ServiceInventorySummary.model_validate(payload)
                            native_body = attestation_body = None
                            native_refs = {}
                            if inventory_summary.evidence_ref != "unavailable":
                                _, native_body = await self.read(inventory_summary.evidence_ref)
                                native_refs["execution"] = inventory_summary.evidence_ref
                            if inventory_summary.attestation_ref:
                                _, attestation_body = await self.read(inventory_summary.attestation_ref)
                                native_refs["attestation"] = inventory_summary.attestation_ref
                            inventory_summary = _verify_service_inventory_report(
                                inventory_summary, native_body, attestation_body
                            )
                            observed_at = metadata.created_at
                            if observed_at.tzinfo is None:
                                observed_at = observed_at.replace(tzinfo=UTC)
                            observation = _service_inventory_observation(
                                request, inventory_summary, ref, observed_at
                            )
                            scanner_summaries.append(
                                {
                                    "scanner": "service-inventory",
                                    "summaryRef": ref,
                                    "nativeRefs": native_refs,
                                    "coverage": inventory_summary.status,
                                    "execution": {
                                        "jobState": inventory_summary.job_state,
                                        "stopReason": inventory_summary.stop_reason,
                                        "endpoints": [
                                            endpoint.model_dump(mode="json", by_alias=True)
                                            for endpoint in inventory_summary.endpoints
                                        ],
                                    },
                                    "missingEndpoints": [
                                        endpoint.authority
                                        for endpoint in inventory_summary.endpoints
                                        if endpoint.reachability == "untested"
                                    ],
                                    "reducedCoverage": inventory_summary.status != "complete",
                                    "limitations": list(inventory_summary.limitations),
                                    "verification": "observed" if native_body is not None else "unverified",
                                    "attribution": "Per-endpoint outcomes are fixed-runner observations; the gateway independently attests aggregate bounds, not each endpoint outcome.",
                                    "interpretation": inventory_summary.interpretation,
                                }
                            )
                        elif payload.get("schemaVersion") == "tls-assessment/v1":
                            tls_summary = TLSAssessmentSummary.model_validate(payload)
                            # Recompute normalized findings and identity from the retained
                            # native bytes, not editable summary assertions.
                            native = {}
                            if tls_summary.evidence_ref != "unavailable":
                                _, native_body = await self.read(tls_summary.evidence_ref)
                                native = json.loads(native_body)
                            normalized = summarize_tls_assessment(
                                TLSAssessment.model_validate(tls_summary.settings),
                                native,
                                tls_summary.evidence_ref,
                                resolution=tls_summary.resolution,
                            )
                            if (
                                normalized.comparison_key != tls_summary.comparison_key
                                or normalized.findings != tls_summary.findings
                                or normalized.origin != tls_summary.origin
                                or normalized.execution != tls_summary.execution
                                or normalized.unperformed_checks != tls_summary.unperformed_checks
                            ):
                                raise ValueError("TLS summary differs from retained evidence")
                            observed_at = metadata.created_at
                            if observed_at.tzinfo is None:
                                observed_at = observed_at.replace(tzinfo=UTC)
                            observation = _tls_observation(request, tls_summary, ref, observed_at)
                            scanner_summaries.append(
                                {
                                    "scanner": "openssl",
                                    "target": tls_summary.origin,
                                    "summaryRef": ref,
                                    "nativeRefs": (
                                        {"execution": tls_summary.evidence_ref}
                                        if tls_summary.evidence_ref != "unavailable"
                                        else {}
                                    ),
                                    "coverage": "partial",
                                    "execution": {
                                        "probes": [
                                            {k: p[k] for k in ("protocol", "cipher", "status", "trustResult")}
                                            for p in tls_summary.execution.get("probes", [])
                                        ],
                                        "unperformedChecks": tls_summary.unperformed_checks,
                                        "errors": tls_summary.execution.get("errors", []),
                                    },
                                    "limitations": sorted(tls_summary.limitations),
                                    "unperformedChecks": tls_summary.unperformed_checks,
                                    "verification": "observed" if tls_summary.execution else "unverified",
                                    "interpretation": tls_summary.interpretation,
                                }
                            )
                        elif payload.get("schemaVersion") == "web-assessment/v1":
                            web_summary = WebAssessmentSummary.model_validate(payload)
                            if web_summary.native_ref:
                                await self.read(web_summary.native_ref)
                            observed_at = metadata.created_at
                            if observed_at.tzinfo is None:
                                observed_at = observed_at.replace(tzinfo=UTC)
                            observation = _web_observation(
                                request, web_summary, ref, observed_at
                            )
                            scanner_summaries.append(
                                {
                                    "scanner": "zap",
                                    "target": web_summary.admission.origin,
                                    "summaryRef": ref,
                                    "nativeRefs": (
                                        {"report": web_summary.native_ref}
                                        if web_summary.native_ref
                                        else {}
                                    ),
                                    "coverage": observation.coverage[0].status,
                                    "execution": {
                                        "report": web_summary.result.execution
                                    },
                                    "limitations": sorted(web_summary.limitations),
                                    "verification": web_summary.verification,
                                    "interpretation": web_summary.interpretation,
                                }
                            )
                        elif payload.get("schemaVersion") == "cloud-posture/v1":
                            cloud_summary = CloudAssessmentSummary.model_validate(
                                payload
                            )
                            if cloud_summary.native_ref:
                                await self.read(cloud_summary.native_ref)
                            observed_at = metadata.created_at
                            if observed_at.tzinfo is None:
                                observed_at = observed_at.replace(tzinfo=UTC)
                            observation = _cloud_observation(
                                request, cloud_summary, ref, observed_at
                            )
                            scanner_summaries.append(
                                {
                                    "scanner": "prowler",
                                    "target": observation.provenance["cloudAccount"],
                                    "summaryRef": ref,
                                    "nativeRefs": (
                                        {"report": cloud_summary.native_ref}
                                        if cloud_summary.native_ref
                                        else {}
                                    ),
                                    "coverage": observation.coverage[0].status,
                                    "outcome": cloud_summary.outcome,
                                    "execution": {
                                        "report": cloud_summary.result.execution
                                    },
                                    "limitations": sorted(cloud_summary.limitations),
                                    "verification": cloud_summary.verification,
                                    "interpretation": cloud_summary.interpretation,
                                    "checkEvidenceRef": ref,
                                    "checkEvidenceCount": len(
                                        cloud_summary.check_evidence or []
                                    ),
                                    "controlEvidenceLimitations": _cloud_control_evidence(
                                        cloud_summary
                                    )[
                                        0
                                    ],
                                    "effectiveConfiguration": cloud_summary.admission.model_dump(
                                        mode="json", by_alias=True
                                    ),
                                }
                            )
                        elif payload.get("schemaVersion") == "game-math-assessment/v1":
                            game_summary = GameMathAssessment.model_validate(payload)
                            game_verified = False
                            game_refs = {"assessment": ref}
                            if game_summary.job is not None:
                                from moonmind.security.game_math_evidence import (
                                    comparison_basis,
                                    verify_inputs,
                                )

                                game_refs, game_bodies = await self.game_math_inputs(
                                    ref, game_summary
                                )
                                game_verified = await asyncio.to_thread(
                                    verify_inputs, game_summary, game_bodies
                                )
                            observed_at = metadata.created_at
                            if observed_at.tzinfo is None:
                                observed_at = observed_at.replace(tzinfo=UTC)
                            observation = _game_math_observation(
                                request,
                                game_summary,
                                ref,
                                observed_at,
                                verified=game_verified,
                                case_basis=comparison_basis(game_bodies)
                                if game_verified
                                else None,
                            )
                            simulation = game_summary.simulation
                            scanner_summaries.append(
                                {
                                    "scanner": "game-math",
                                    "target": game_summary.game_ref,
                                    "summaryRef": ref,
                                    "nativeRefs": game_refs,
                                    "inputVerification": "reproduced"
                                    if game_verified
                                    else "unverified",
                                    "coverage": observation.coverage[0].status,
                                    "outcome": game_summary.outcome,
                                    "execution": {
                                        "simulation": (
                                            simulation.stop_reason or simulation.status
                                            if simulation
                                            else "not_requested"
                                        )
                                    },
                                    "limitations": sorted(
                                        {*game_summary.limitations,
                                         *(["input_replay_limit_exceeded"]
                                           if game_summary.job and not game_verified else [])}
                                    ),
                                    "verification": game_summary.certification,
                                }
                            )
                        elif (
                            payload.get("schemaVersion")
                            == "application-access-report/v1"
                        ):
                            access_summary = ApplicationAccessReport.model_validate(
                                payload
                            )
                            if access_summary.job is None:
                                raise ValueError(
                                    "application access job inputs unavailable"
                                )
                            try:
                                _, spec_body = await self.read(
                                    access_summary.job.input_refs["spec"]
                                )
                                _, capture_body = await self.read(
                                    access_summary.job.input_refs["capture"]
                                )
                            except TemporalArtifactNotFoundError as exc:
                                raise ValueError(
                                    "application access input evidence unavailable"
                                ) from exc
                            access_spec, access_capture = (
                                _verify_application_access_report(
                                    access_summary, spec_body, capture_body
                                )
                            )
                            observed_at = metadata.created_at
                            if observed_at.tzinfo is None:
                                observed_at = observed_at.replace(tzinfo=UTC)
                            observation = _application_access_observation(
                                request,
                                access_summary,
                                access_spec,
                                access_capture,
                                ref,
                                observed_at,
                            )
                            scanner_summaries.append(
                                {
                                    "scanner": "application-access",
                                    "cases": [
                                        {
                                            "caseRef": row.case_ref,
                                            "identityRef": row.identity_ref,
                                            "resourceRef": row.resource_ref,
                                            "expected": row.expected,
                                            "observed": row.access.observed,
                                            "authenticationState": row.authentication_state,
                                            "outcome": row.outcome,
                                            "coverage": row.coverage,
                                            "reasons": row.reasons,
                                            "request": {
                                                "method": case.request.method,
                                                "path": case.request.path,
                                                **(
                                                    {
                                                        "protocol": case.request.protocol,
                                                        "messageType": case.request.message_type,
                                                        "effect": case.request.effect,
                                                        "realtimeCaseRef": case.request.realtime_case_ref,
                                                    }
                                                    if case.request.realtime_case_ref is not None
                                                    else {}
                                                ),
                                            },
                                        }
                                        for row, case in zip(
                                            access_summary.cases, access_spec.cases
                                        )
                                    ],
                                    "target": access_capture.scope.web.origin,
                                    "summaryRef": ref,
                                    "nativeRefs": {
                                        "assessment": ref,
                                        **access_summary.job.input_refs,
                                    },
                                    "coverage": access_summary.status,
                                    "outcome": access_summary.outcome,
                                    "execution": {
                                        "cases": access_capture.stop_reason
                                        or access_capture.status
                                    },
                                    "limitations": sorted(access_summary.limitations),
                                    "verification": access_summary.verification,
                                    "specDigest": access_summary.spec_digest,
                                    "inputDigests": access_summary.input_digests,
                                }
                            )
                        elif payload.get("schemaVersion") == "rgs-assessment/v1":
                            rgs_summary = RgsAssessment.model_validate(payload)
                            if rgs_summary.job:
                                for input_ref in rgs_summary.job.input_refs.values():
                                    await self.read(input_ref)
                            observed_at = metadata.created_at
                            if observed_at.tzinfo is None:
                                observed_at = observed_at.replace(tzinfo=UTC)
                            observation = _rgs_observation(
                                request, rgs_summary, ref, observed_at
                            )
                            scanner_summaries.append(
                                {
                                    "scanner": "rgs-scenarios",
                                    "target": rgs_summary.game_ref,
                                    "summaryRef": ref,
                                    "nativeRefs": {
                                        "assessment": ref,
                                        **(
                                            rgs_summary.job.input_refs
                                            if rgs_summary.job
                                            else {}
                                        ),
                                    },
                                    "coverage": observation.coverage[0].status,
                                    "outcome": rgs_summary.outcome,
                                    "execution": {
                                        "scenarios": rgs_summary.stop_reason
                                        or rgs_summary.status
                                    },
                                    "limitations": sorted(
                                        {
                                            *rgs_summary.limitations,
                                            *(
                                                ["finding_import_limit"]
                                                if "rgsFindingsOmitted"
                                                in observation.provenance
                                                else []
                                            ),
                                        }
                                    ),
                                    "verification": rgs_summary.verification,
                                    "specDigest": rgs_summary.spec_digest,
                                    "inputDigests": rgs_summary.input_digests,
                                    "retest": rgs_summary.retest,
                                }
                            )
                        elif payload.get("schemaVersion") == "rng-assessment/v1":
                            rng_summary = RngAssessment.model_validate(payload)
                            observed_at = metadata.created_at
                            if observed_at.tzinfo is None:
                                observed_at = observed_at.replace(tzinfo=UTC)
                            observation = _rng_observation(
                                request, rng_summary, ref, observed_at
                            )
                            scanner_summaries.append(
                                {
                                    "scanner": "rng",
                                    "target": rng_summary.game_ref,
                                    "summaryRef": ref,
                                    "nativeRefs": {"assessment": ref},
                                    "coverage": (
                                        "complete"
                                        if all(
                                            c.status == "complete"
                                            for c in observation.coverage
                                        )
                                        else "partial"
                                    ),
                                    "outcome": rng_summary.outcome,
                                    "execution": {
                                        name: run.stop_reason or run.status
                                        for name, run in (
                                            ("raw", rng_summary.raw),
                                            ("mapped", rng_summary.mapped),
                                        )
                                        if run
                                    },
                                    "implementationReview": [
                                        r.model_dump(mode="json", by_alias=True)
                                        for r in rng_summary.implementation_review
                                    ],
                                    "statisticalFindings": {
                                        name: run.model_dump(mode="json", by_alias=True)
                                        for name, run in (
                                            ("raw", rng_summary.raw),
                                            ("mapped", rng_summary.mapped),
                                        )
                                        if run
                                    },
                                    "externalClaims": [
                                        r.model_dump(mode="json", by_alias=True)
                                        for r in rng_summary.external_claims
                                    ],
                                    "cryptographicSecurity": rng_summary.cryptographic_security,
                                    "gameFairness": rng_summary.game_fairness,
                                    "limitations": sorted(rng_summary.limitations),
                                    "verification": rng_summary.certification,
                                    "interpretation": rng_summary.interpretation,
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
                        if observation.deployment:
                            product_scan_refs += await self.product_evidence(
                                observation.deployment.components
                            )
                        observations.append(observation)
                        outcomes.append(
                            "complete" if observation.complete(now) else "incomplete"
                        )
                    except (ValidationError, ValueError, AttributeError) as exc:
                        scan_diagnostics.append(
                            {"boundary": "retained-report", "error": type(exc).__name__}
                        )
                if request.github_alerts is not None:
                    github_alerts = await self._import_github_alerts(
                        request, observations, scan_key
                    )
                    outcomes.append(github_alerts["outcome"])
                    # The live import is observed after this execution began.
                    now = datetime.now(UTC)
            coverage = [
                row for observation in observations for row in observation.coverage
            ]
            coverage_windows = [
                window
                for item in observations
                for window in (
                    item.coverage_windows
                    or [
                        (
                            None
                            if item.provenance.get("assessmentWork")
                            else CoverageWindow(
                                observed_at=item.observed_at, expires_at=item.expires_at
                            )
                        )
                    ]
                    * len(item.coverage)
                )
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
            closures = {}
            for item in observations:
                for finding_id, closure in item.upstream_closed.items():
                    closures.setdefault(finding_id, set()).add(closure)
            # A finding another source still reports, or that sources close
            # differently, is not closed upstream.
            upstream_closed = {
                finding_id: next(iter(states))
                for finding_id, states in closures.items()
                if len(states) == 1 and finding_id not in findings_by_id
            }
            if scan_diagnostics or not observations:
                coverage_windows.append(None)
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
            deployments = {
                content_key(item.deployment.model_dump(mode="json")): item.deployment
                for item in observations
                if item.deployment is not None
            }
            if len(deployments) > 1:
                raise ToolFailure(
                    "INVALID_INPUT",
                    "Conflicting deployment observations require separate assessments.",
                    False,
                )
            observation = Observation(
                scope=request.scope,
                observed_at=max(
                    (item.observed_at for item in observations), default=now
                ),
                expires_at=min((item.expires_at for item in observations), default=now),
                findings=list(findings_by_id.values()),
                coverage=coverage,
                coverage_windows=coverage_windows,
                upstream_closed=upstream_closed,
                deployment=next(iter(deployments.values()), None),
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
                "productEvidenceRefs": list(dict.fromkeys(product_scan_refs)),
            }
            if github_alerts is not None:
                work["githubAlerts"] = github_alerts["projection"]
            scan_ref = await self.write(_json(work), kind="security.scan", key=scan_key)
            await self.seal(
                scan_ref, lambda: self._scan_dependencies(refs, work, history)
            )
        comparison = compare_observations(
            observation,
            baseline,
            decisions=decisions,
            now=now,
            baseline_unavailable=baseline_unavailable,
            baseline_ref=request.baseline_ref,
            product_baseline=product_baseline,
            product_baseline_ref=request.product_baseline_ref,
            product_baseline_error=product_baseline_error,
        )
        if comparison["productComparison"] is not None:
            comparison["productComparison"]["evidenceRefs"] = list(
                dict.fromkeys(product_refs)
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

        async def comparison_dependencies():
            entries = [
                ArtifactDependency(
                    scan_ref, await self.digest(scan_ref), True, "security.scan"
                ),
                *_history_evidence(_comparison_citations(comparison), history),
                *(
                    ArtifactDependency(
                        ref, self.digests[ref], True, "security.evidence"
                    )
                    for ref in decision_evidence
                ),
                *(
                    ArtifactDependency(
                        ref, self.digests[ref], True, "security.product-evidence"
                    )
                    for ref in dict.fromkeys(product_refs)
                ),
            ]
            if request.decisions_ref:
                entries.append(
                    ArtifactDependency(
                        request.decisions_ref,
                        self.digests[request.decisions_ref],
                        True,
                        "security.decisions",
                    )
                )
            if baseline is not None:
                entries.append(
                    ArtifactDependency(
                        request.baseline_ref,
                        history[request.baseline_ref],
                        True,
                        BASELINE_ROLE,
                    )
                )
            return entries

        await self.seal(comparison_ref, comparison_dependencies)
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
        controls = None
        if request.control_assessment is not None:
            try:
                controls = await self.prepare_controls(
                    request,
                    comparison,
                    scan_ref=scan_ref,
                    comparison_ref=comparison_ref,
                    now=now,
                    review=review,
                    review_status=review_status,
                    review_readable=review_readable,
                    framework_pack_context=framework_pack_context,
                )
            except Exception as exc:
                # Control/report failures cannot erase sealed native outcomes.
                diagnostics.append(
                    {"boundary": "control-assessment", "error": type(exc).__name__}
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
        # Mapping relationships depend on today's control plan, unlike the
        # immutable native scan. Never reuse a previous plan's review results.
        outputs["scannerSummaries"] = [dict(row) for row in outputs["scannerSummaries"]]
        for scanner in outputs["scannerSummaries"]:
            if scanner.get("scanner") != "prowler":
                continue
            source = inputs.get(scanner["summaryRef"])
            if source is None:
                continue
            current_cloud = CloudAssessmentSummary.model_validate_json(source[1])
            scanner["mappingReviews"] = review_cloud_mappings(
                current_cloud, request.control_assessment
            )
        if comparison["productComparison"] is not None:
            outputs.update(
                productBaselineRef=request.product_baseline_ref,
                productComparisonStatus=comparison["productComparison"]["status"],
                productApprovalStatus=comparison["productComparison"]["approvalStatus"],
            )
        if request.control_assessment is not None:
            outputs["controlAssessmentStatus"] = (
                "completed" if controls else "unavailable"
            )
        if controls:
            control_ref, control_report = controls
            outputs.update(
                controlAssessmentRef=control_ref,
                # Pack narrative belongs in the retained report, not a potentially
                # multi-megabyte Temporal activity result.
                frameworkPack=(
                    {
                        key: control_report.framework_pack[key]
                        for key in (
                            "id",
                            "edition",
                            "revision",
                            "artifactRef",
                            "sha256",
                        )
                    }
                    if control_report.framework_pack
                    else None
                ),
                assessmentIntelligenceChanges=control_report.intelligence_changes,
                frameworkPackDiagnostics=control_report.diagnostics,
                controlAssessmentCounts={
                    value: sum(
                        row.conclusion == value for row in control_report.requirements
                    )
                    for value in (
                        "satisfied",
                        "not_satisfied",
                        "inconclusive",
                        "not_assessed",
                    )
                },
            )
        github = work.get("githubAlerts")
        if github is not None:
            outputs["githubAlerts"] = {
                "nativeRef": github["nativeRef"],
                "subjectRevision": github["subjectRevision"],
                "categories": {
                    category: {
                        key: row[key]
                        for key in (
                            "outcome",
                            "coverage",
                            "findings",
                            "otherRevision",
                            "closed",
                            "outOfScope",
                        )
                    }
                    for category, row in github["categories"].items()
                },
                "unverified": github["unverified"],
            }
        evidence = [scan_ref] + ([github["nativeRef"]] if github else [])
        if controls:
            evidence += [comparison_ref, control_ref]
        if identity:
            outputs["identityLifecycle"] = identity
            evidence += [identity["evidenceRef"], identity["reviewEvidenceRef"]]
        if change_control:
            outputs["githubChangeControl"] = change_control
            evidence += [
                change_control["evidenceRef"],
                change_control["reviewEvidenceRef"],
            ]
        if framework:
            framework_ref, framework_report = framework
            evidence.append(framework_ref)
            outputs.update(
                frameworkRef=framework_ref,
                frameworkReadiness=framework_report["readiness"],
                frameworkEdition=framework_report["pack"]["edition"],
                evidenceRequests=framework_report["evidenceRequests"],
            )
            if framework_report["pack"]["id"] == "gli-19-readiness":
                outputs.update(
                    frameworkJurisdictionBasis=framework_report["jurisdictionBasis"],
                    frameworkScope=framework_report["systemInputs"],
                    frameworkUnresolvedRequirements=framework_report[
                        "unresolvedRequirements"
                    ],
                    externalVerification=framework_report["externalVerification"],
                )
        summary_ref = None
        try:
            project_summary = ""
            project_digest = None
            if request.assessment_project is not None:
                project = request.assessment_project
                project_digest = project.digest
                saved_project = await self.saved(
                    kind="security.assessment-project", key=project_digest
                )
                project_ref = (
                    saved_project[0]
                    if saved_project
                    else await self.write(
                        _json(project.model_dump(mode="json", by_alias=True)),
                        kind="security.assessment-project",
                        key=project_digest,
                    )
                )
                evidence.append(project_ref)
                outputs["assessmentProjectRef"] = project_ref
                # Retained configuration is context, not a claim that every
                # candidate asset was examined or retention intent enforced.
                project_summary = (
                    f"\n## Assessment context\n\n{project.project_name} / {project.environment_name} "
                    f"(saved version {project.source_version}).\n\n"
                    f"Purpose: {project.purpose}\n\nBoundaries: {project.boundaries}\n\n"
                    f"Exact configuration: {project_ref}. These are reusable inputs, not execution authority. "
                    "Candidate targets and retention intent do not establish tested coverage, grants, or retention policy.\n"
                )
            from moonmind.security.assessment_reports import render_assessment_details

            details = render_assessment_details(
                comparison,
                request=request,
                comparison_ref=comparison_ref,
                review_status=review_status,
                work=work,
                native_reports=[
                    (row["summaryRef"], inputs[row["summaryRef"]][1])
                    for row in work.get("scannerSummaries", [])
                    if row["summaryRef"] in inputs
                ],
            )
            if request.report_context:
                context_body = _json(
                    request.report_context.model_dump(mode="json", by_alias=True)
                )
                context_key = content_key([context_body.decode("utf-8")])
                saved_context = await self.saved(
                    kind="security.report-context", key=context_key
                )
                context_ref = (
                    saved_context[0]
                    if saved_context
                    else await self.write(
                        context_body,
                        kind="security.report-context",
                        key=context_key,
                    )
                )
                evidence.append(context_ref)
                outputs["reportContextRef"] = context_ref
                details += f"\nAttributed report context: {context_ref}.\n"
            summary = (
                _summary(comparison)
                + details
                + project_summary
                + _scanner_summary(work.get("scannerSummaries", []))
                + _github_summary(github)
                + _review_summary(review, review_status)
            ).encode()
            if identity:
                from moonmind.security.okta_evidence import render_identity_summary

                summary += render_identity_summary(identity).encode()
            if change_control:
                from moonmind.security.github_change_control import render_summary

                summary += render_summary(change_control).encode()
            if framework:
                from moonmind.security.frameworks import render_framework_preparation

                summary += (
                    "\n" + render_framework_preparation(framework_report)
                ).encode()
            if controls:
                from moonmind.security.control_assessments import (
                    render_control_assessment,
                )

                summary += render_control_assessment(control_report).encode()
            elif request.control_assessment is not None:
                summary += b"\nRequirement assessment is unavailable. Native results are retained; requirement conclusions remain Unverified.\n"
            from moonmind.security.assessment_reports import bound_report_presentation

            structured_refs = [comparison_ref, *evidence]
            if review_readable:
                structured_refs.append(request.review_ref)
            summary = bound_report_presentation(summary, structured_refs=structured_refs)
            summary_key = content_key(
                [
                    report_key,
                    "summary",
                    "professional-report/v3",
                    request.report_context.model_dump(mode="json", by_alias=True)
                    if request.report_context
                    else None,
                    EVIDENCE_TRANSFORMATION_VERSION,
                    request.review_ref,
                    review_status,
                ]
                + ([project_digest] if project_digest else [])
                + ([framework_ref] if framework else [])
                + (
                    [control_ref]
                    if controls
                    else [outputs.get("controlAssessmentStatus")]
                )
            )
            saved_summary = await self.service.list_for_execution(
                **self.execution, link_type="report.primary", access=self.access
            )
            summary_ref = next(
                (
                    item.artifact_id
                    for item in saved_summary
                    if (item.metadata_json or {}).get("subject") == summary_key
                    and getattr(item.status, "value", item.status) == "complete"
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
            else:
                # As with native saved work, incomplete upload intents are not
                # reusable; completed reports still require current read access.
                await self.read(summary_ref)

            async def summary_dependencies():
                # Interpretation is optional: it never holds native retention.
                optional = [request.review_ref] if review_readable else []
                if review is not None:
                    optional += review.evidence_refs()
                return [
                    ArtifactDependency(
                        comparison_ref,
                        await self.digest(comparison_ref),
                        True,
                        "security.comparison",
                    ),
                    *[
                        ArtifactDependency(
                            ref, await self.digest(ref), True, "security.evidence"
                        )
                        for ref in evidence
                    ],
                    *(
                        ArtifactDependency(
                            ref, self.digests[ref], False, "security.review"
                        )
                        for ref in optional
                    ),
                ]

            await self.seal(summary_ref, summary_dependencies)
            for ref, link_type in (
                (comparison_ref, "report.structured"),
                *((ref, "report.evidence") for ref in evidence),
            ):
                await self.service.link_artifact(
                    artifact_id=ref,
                    principal=self.principal,
                    execution_ref={**self.execution, "link_type": link_type},
                )
            if framework:
                await self.service.link_artifact(
                    artifact_id=framework_ref,
                    principal=self.principal,
                    execution_ref={**self.execution, "link_type": "report.structured"},
                )
            if controls:
                await self.service.link_artifact(
                    artifact_id=control_ref,
                    principal=self.principal,
                    execution_ref={**self.execution, "link_type": "report.structured"},
                )
            if review_readable:
                await self.service.link_artifact(
                    artifact_id=request.review_ref,
                    principal=self.principal,
                    execution_ref={**self.execution, "link_type": "report.evidence"},
                )
            outputs["reportBundle"] = build_report_bundle_result(
                primary_report_ref={"artifact_id": summary_ref},
                structured_ref={
                    "artifact_id": (
                        control_ref
                        if controls
                        else framework_ref if framework else comparison_ref
                    )
                },
                evidence_refs=[{"artifact_id": ref} for ref in evidence]
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
        if request.oscal_export is not None:
            from moonmind.workflows.skills.security_oscal import export_assessment

            outputs["oscalExport"] = await export_assessment(
                self,
                request=request,
                comparison=comparison,
                comparison_ref=comparison_ref,
                framework=framework,
            )
        if request.evidence_export is not None:
            from moonmind.workflows.skills.security_evidence_export import (
                associated_evidence,
                export_selected_evidence,
            )

            outputs["evidenceExport"] = await export_selected_evidence(
                self,
                selection=request.evidence_export,
                associated=associated_evidence(
                    request=request,
                    work=work,
                    outputs=outputs,
                    evidence=evidence,
                    summary_ref=summary_ref,
                    review=review,
                ),
            )
        return ToolResult(status="COMPLETED", outputs=outputs)


def _json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _native_refs(work: Mapping[str, Any]) -> list[str]:
    """Readable native and product evidence cited by a saved scan."""
    refs = [
        ref
        for summary in work.get("scannerSummaries", [])
        for ref in summary["nativeRefs"].values()
    ]
    if work.get("githubAlerts") is not None:
        refs.append(work["githubAlerts"]["nativeRef"])
    return list(dict.fromkeys([*refs, *work.get("productEvidenceRefs", [])]))


def _scan_citations(work: Mapping[str, Any]) -> set[str]:
    deployment = work["observation"].get("deployment")
    return {
        ref
        for finding in work["observation"]["findings"]
        for ref in finding["evidenceRefs"]
    } | {
        ref
        for component in (deployment["components"] if deployment else [])
        for ref in _artifact_refs(component["evidenceRefs"])
    }


def _artifact_refs(refs) -> list[str]:
    """Evidence that names an artifact; other references stay text."""
    return [ref for ref in refs if ARTIFACT_ID_PATTERN.fullmatch(ref)]


def _decision_citations(comparison: Mapping[str, Any]) -> set[str]:
    return set(
        _artifact_refs(
            ref
            for row in comparison["decisions"]
            for ref in row["decision"]["evidenceRefs"]
        )
    )


def _comparison_citations(comparison: Mapping[str, Any]) -> set[str]:
    return (
        {
            ref
            for row in comparison["findings"]
            for ref in row["finding"]["evidenceRefs"]
        }
        | _decision_citations(comparison)
        | _product_citations(comparison)
    )


def _product_citations(comparison: Mapping[str, Any]) -> set[str]:
    product = comparison.get("productComparison")
    return set(_artifact_refs(product.get("evidenceRefs", []))) if product else set()


def _history_evidence(
    cited: set[str], history: Mapping[str, str]
) -> list[ArtifactDependency]:
    """Cited earlier evidence, with the digest its baseline's seal verified.

    Other citations are not guessed into artifacts: a retained report is
    sealed as an input, while its own references remain its producer's.
    """
    return [
        ArtifactDependency(ref, history[ref], True, "security.evidence")
        for ref in sorted(cited & history.keys())
    ]


async def seal_retained_assessment_root(
    service: Any, *, artifact: Any, payload: bytes, access: ArtifactAccessContext
) -> None:
    """Seal a scan or comparison root its producer published without a seal.

    Only exact identities are certified: a scan's inputs must reproduce its
    recorded work identity, and a comparison requires its sealed scan, the
    seal of any baseline it compared against and its decisions' evidence. Any
    other root stays unsealed; completed scanners are never replayed.
    """
    document = json.loads(payload)
    if document.get("schemaVersion") != "security-comparison/v1":
        entries = await _retained_scan_dependencies(service, document, access)
    else:
        comparison = Comparison.model_validate(document)
        if not comparison.scan_ref:
            raise TemporalArtifactStateError("Retained comparison has no scan")
        scan, scan_payload = await service.read(
            artifact_id=comparison.scan_ref, access=access
        )
        if await _proof(service, comparison.scan_ref, access) is None:
            await _record(
                service,
                scan.artifact_id,
                await _retained_scan_dependencies(
                    service, json.loads(scan_payload), access
                ),
                access,
            )
        entries = [
            ArtifactDependency(scan.artifact_id, scan.sha256, True, "security.scan")
        ]
        history: dict[str, str] = {}
        if comparison.baseline_status == "available" and comparison.baseline_ref:
            proof = await _proof(service, comparison.baseline_ref, access)
            if proof is None:
                # Its cited history is unverified; an immutable seal without
                # it would certify an incomplete closure.
                raise TemporalArtifactStateError(
                    "Retained comparison baseline is unsealed"
                )
            history = {
                entry.artifact_id: entry.sha256 for entry in proof if entry.required
            }
            history[comparison.baseline_ref] = await _current_digest(
                service, comparison.baseline_ref, access
            )
            entries.append(
                ArtifactDependency(
                    comparison.baseline_ref,
                    history[comparison.baseline_ref],
                    True,
                    BASELINE_ROLE,
                )
            )
        entries += _history_evidence(_comparison_citations(document), history)
        # Decision evidence no baseline verified was admitted by this
        # comparison, so it is required exactly as its producer now seals it.
        entries += [
            ArtifactDependency(
                ref,
                await _current_digest(service, ref, access),
                True,
                "security.evidence",
            )
            for ref in sorted(_decision_citations(document) - history.keys())
        ]
        entries += [
            ArtifactDependency(
                ref,
                await _current_digest(service, ref, access),
                True,
                "security.product-evidence",
            )
            for ref in sorted(_product_citations(document) - history.keys())
        ]
        # History the baseline's seal verified may belong to an earlier
        # occurrence's workflow; only this seal verifies it through that proof.
        access = access._with_verified_inputs(history)
    await _record(service, artifact.artifact_id, entries, access)


async def _retained_scan_dependencies(service, work, access):
    observation = Observation.model_validate(work["observation"])
    if "retained_completed_work" in work["originalOutcomes"]:
        # Its evidence came from a baseline this root does not record.
        raise TemporalArtifactStateError("Retained scan baseline is unknown")
    inputs = [
        value
        for _, value in sorted(
            (int(key[5:]), value)
            for key, value in observation.provenance.items()
            if re.fullmatch(r"input\d+", key)
        )
    ]
    digests = {ref: await _current_digest(service, ref, access) for ref in inputs}
    scope = observation.scope.model_dump(mode="json")
    if observation.provenance.get("assessmentWork") not in {
        content_key({"scope": scope, "tools": list(tools), "inputs": digests})
        for tools in _RECORDED_TOOLS
    }:
        raise TemporalArtifactStateError(
            "Retained scan inputs do not reproduce its work identity"
        )
    return [
        *(
            ArtifactDependency(ref, digests[ref], True, "security.input")
            for ref in inputs
        ),
        *[
            ArtifactDependency(
                ref,
                await _current_digest(service, ref, access),
                True,
                "security.native",
            )
            for ref in _native_refs(work)
        ],
    ]


async def _current_digest(service, ref, access) -> str:
    # Completed artifacts are immutable; the owner still verifies stored bytes.
    item, *_ = await service.get_metadata(artifact_id=ref, access=access)
    return item.sha256


async def _proof(service, ref, access):
    return await service.dependency_proof(root_artifact_id=ref, access=access)


async def _record(service, root, entries, access) -> None:
    await service.record_verified_dependencies(
        root_artifact_id=root,
        dependencies=entries,
        access=access,
        bytes_only_roles={BASELINE_ROLE},
    )


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
            (
                "Remediated verified requires a linked passing retest or the "
                "source's own fixed state; closed upstream is a source decision, "
                "not a fix."
            ),
            *(
                [
                    "Refreshed intelligence may explain new or absent findings: "
                    + ", ".join(comparison["intelligenceChanges"])
                    + "."
                ]
                if comparison.get("intelligenceChanges")
                else []
            ),
            *[
                f"- decision on {row['decision']['findingId'][:12]} requires review: "
                + ", ".join(row["reviewReasons"]).replace("_", " ")
                for row in comparison["decisions"]
                if row["status"] == "review_required"
            ],
            "Stale or partial evidence and expired decisions require review.",
            "Finding evidence, provenance and attributed decisions are retained in the structured report.",
            "",
        ]
    ) + _product_summary(comparison.get("productComparison"))


def _product_summary(product: dict | None) -> str:
    if product is None:
        return ""

    def text(value):
        return html.escape(redact_sensitive_text(str(value)), quote=True)

    baseline = product["baseline"]
    deployment = product["deployment"]
    lines = [
        "",
        "## Assessed product baseline comparison",
        "",
        f"Product baseline: {text(product['baselineRef'])}. Comparison: {product['status']}.",
        f"Approval evidence: {product['approvalStatus']}. Independent assurance: Unverified.",
        f"Baseline identity: {text(baseline['baselineId'] if baseline else 'unavailable')}; state: {text(baseline['state'] if baseline else 'unavailable')}.",
        f"Observed deployment: {text(deployment['deploymentId'] if deployment else 'unavailable')}.",
        text(product["interpretation"]),
    ]
    for row in product["components"]:
        lines.append(
            f"- {text(row['kind'])} / {text(row['id'])}: {row['status']}. {text(row['reason'])}"
        )
        for name in ("expected", "observed"):
            component = row[name]
            if component:
                lines.append(
                    f"  {name}: {text(component['identity'])}; method: {text(component['method'])}; "
                    f"evidence: {text(', '.join(component['evidenceRefs']))}; "
                    f"omission: {text(component['omissionReason'] or 'none')}."
                )
        if row["approvedChangeId"]:
            lines.append(
                f"  Recorded approved change: {text(row['approvedChangeId'])}."
            )
    if baseline:
        for record in [
            *baseline["records"],
            *[change["record"] for change in baseline["approvedChanges"]],
        ]:
            lines.append(
                f"- {record['stage']} record for {text(record['product'])} / {text(record['baselineId'])}: "
                f"{record['status']}; {text(record['reason'])}; verifier: {text(record['verifier'])}; "
                f"evidence: {text(', '.join(record['evidenceRefs']))}."
            )
    lines += [f"- Reason: {text(reason)}" for reason in product["reasons"]]
    lines += [f"- Scope omission: {text(reason)}" for reason in product["omissions"]]
    external = product["externalVerification"]
    lines.append(
        f"Unverified: {text(external['reason'])} Verifiable by: {text(external['verifiableBy'])}"
    )
    return "\n".join(lines) + "\n"


def _scanner_summary(summaries: list[dict]) -> str:
    lines = []
    for summary in summaries:
        lines += [
            "",
            f"Scanner: {summary['scanner']}"
            + (f" on {summary['target']}" if summary.get("target") else "")
            + f". Coverage: {summary['coverage']}."
            + (f" Outcome: {summary['outcome']}." if summary.get("outcome") else ""),
            f"Execution: {json.dumps(summary['execution'], sort_keys=True)}.",
            "Verification: not performed.",
            "Native evidence: " + ", ".join(summary["nativeRefs"].values()) + ".",
        ]
        if summary["limitations"]:
            lines.append("Limitations: " + ", ".join(summary["limitations"]) + ".")
        if summary.get("interpretation"):
            lines.append(summary["interpretation"])
        if summary["scanner"] == "service-inventory":
            lines.append(summary["attribution"])
            lines.append(
                "Reduced coverage: " + ("yes" if summary["reducedCoverage"] else "no") + "."
            )
            if summary["missingEndpoints"]:
                lines.append(
                    "Untested endpoints: " + ", ".join(summary["missingEndpoints"]) + "."
                )
        if summary["scanner"] == "application-access":

            def safe(value):
                value = html.escape(redact_sensitive_text(str(value)), quote=True)
                return re.sub(r"([\\`*_[\]{}()#!|>])", r"\\\1", value)

            for row in summary["cases"]:
                request = row["request"]
                transport = (
                    f"{safe(request['protocol'])} {safe(request['messageType'])} "
                    f"({safe(request['effect'])}; operation {safe(request['realtimeCaseRef'])}), "
                    if request.get("realtimeCaseRef") is not None else ""
                )
                lines.append(
                    f"- Case {safe(row['caseRef'])}: {safe(row['identityRef'])} on {safe(row['resourceRef'])}, "
                    f"{transport}{safe(request['method'])} {safe(request['path'])}. "
                    f"Expected {row['expected']}; observed {row['observed']}. "
                    f"Authentication {row['authenticationState']}; outcome {row['outcome']}; coverage {row['coverage']}. "
                    f"Reasons: {', '.join(row['reasons']) or 'none'}."
                )
        if summary["scanner"] == "rng":
            for key, label in (
                ("implementationReview", "Implementation observations"),
                ("statisticalFindings", "Statistical findings"),
                ("externalClaims", "Externally reported claims (Unverified)"),
            ):
                lines.append(f"{label}: {json.dumps(summary[key], sort_keys=True)}")
    return "\n".join(lines) + ("\n" if lines else "")


def _github_summary(github: dict | None) -> str:
    if github is None:
        return ""
    lines = [
        "",
        f"GitHub alerts: {github['repository']} {github['ref'] or 'unresolved ref'} at "
        f"{github['subjectRevision'] or 'an unavailable revision'}.",
        "Imported upstream observations; MoonMind did not run these scanners.",
    ]
    for category, row in github["categories"].items():
        lines.append(
            f"- {category}: {row['outcome']}, coverage {row['coverage']}, "
            f"{row['findings']} findings, {row['otherRevision']} only at another "
            f"revision, {row['closed']} closed, {row['outOfScope']} outside scope."
        )
    lines += [
        f"- Unverified {row['category']} ({row['outcome']}): {row['nextStep']}"
        for row in github["unverified"]
    ]
    return "\n".join(lines) + "\n"


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
        from moonmind.security.evidence_exports import validation_summary

        raise ToolFailure(
            "INVALID_INPUT",
            "Invalid security assessment admission. "
            + validation_summary(exc, AssessmentRequest),
            False,
        ) from None
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
            access=ArtifactAccessContext.for_activity(
                principal="system:security-assessment"
            ),
        ).execute(request)


def register_security_assessment_tool_handler(dispatcher: Any) -> None:
    dispatcher.register_skill(skill_name=TOOL_NAME, handler=security_assessment_handler)
