"""Readable rendering for the existing security.assess report owner.

Native comparison and control results remain authoritative; supplied commentary
is attributed separately and cannot promote a finding or coverage conclusion.
"""

from __future__ import annotations

import html
import json
import re

from moonmind.utils.logging import redact_evidence_payload, redact_evidence_text

MAX_REPORT_BYTES = 1024 * 1024
MAX_FINDING_DETAIL_BYTES = 384 * 1024


def _text(value):
    if isinstance(value, (dict, list, tuple)):
        value = redact_evidence_payload(value)
    if re.fullmatch(r"art_[A-Za-z0-9_-]+", str(value)):
        return str(value)
    value = (
        html.escape(redact_evidence_text(str(value)))
        .replace("\n", " ")
        .replace("\r", " ")
    )
    return re.sub(r"([\\`*[\]{}()#!|>])", r"\\\1", value)


def render_assessment_details(
    comparison,
    *,
    request,
    comparison_ref,
    review_status,
    work,
    native_reports=(),
    coverage_status=None,
):
    observation = comparison["observation"]
    context = request.report_context
    native_ids = {row["findingId"] for row in comparison["findings"]}
    if context and any(row.finding_id not in native_ids for row in context.annotations):
        raise ValueError("report annotation references unknown finding")
    lines = [
        "",
        "## Executive assessment",
        "",
        f"Collection outcome: {_text(work['scanOutcome'])}. Coverage: {coverage_status or comparison['coverageStatus']}. Native coverage: {comparison['coverageStatus']}.",
        "Results describe only the admitted scope and recorded observation windows. No independent attestation, certification, or regulator approval is established.",
        "Zero findings means no findings in the recorded checks, not an assurance that the assets are secure. Accepted risks remain explicit decisions, not remediated findings.",
        f"Structured comparison: {comparison_ref}. Scan: {_text(comparison.get('scanRef') or 'unavailable')}. Baseline: {_text(comparison.get('baselineRef') or 'not supplied')}.",
        f"Author: {_text(context.author if context else 'MoonMind automated assessment')}. Reviewer: {_text(context.reviewer if context and context.reviewer else 'unspecified')}.",
        f"Review status: {context.review_status if context else 'not_requested'}. Agent interpretation availability: {review_status}.",
        "Review identities and status are attributed metadata, not independent verification or application roles.",
        "",
        "## Scope and admitted execution boundaries",
        "",
        f"Assets: {_text(', '.join(observation['scope']['assets']))}.",
        f"Exclusions: {_text(', '.join(observation['scope'].get('exclusions', [])) or 'none declared')}.",
        f"Repository exceptions: {_text(', '.join(observation['scope'].get('repositoryExceptions', [])) or 'none declared')}.",
        f"Selected assessment tools: {_text(', '.join(request.tools))}. Budget: {request.budget.base_seconds}/{request.budget.max_seconds} seconds (base/maximum).",
        "Retained inputs convey their original admission and scope; this report grants no execution, network, credential, or disclosure authority.",
        "",
        "## Methodology and test coverage",
        "",
        f"Observation: {observation['observedAt']}; expires: {observation['expiresAt']}.",
        "Method versions below are declared selections; only the executed checks below establish coverage. Unspecified methods or versions remain undefined.",
    ]
    if context:
        lines += [
            f"- Selected method: {_text(name)} / {_text(version)}."
            for name, version in context.methodology_versions.items()
        ]
    windows = observation.get("coverageWindows", [])
    for index, row in enumerate(observation["coverage"]):
        window = windows[index] if windows else None
        lines += [
            f"- {_text(row['source'])} / {_text(row['tool'])}: {row['status']}; method {row.get('evidenceMethod', 'unclassified')}.",
            f"  Assets: {_text(', '.join(row['assets'])) or 'none'}. Rules: {_text(', '.join(row['ruleIds'])) or 'none named'}. All configured rules: {row['allRules']}.",
            f"  Configuration: {_text(row['configRef'])}. Intelligence: {_text(row.get('intelligence', {}))}.",
            f"  Source observation window: {_text(window if window else 'not separately recorded; aggregate window above')}.",
        ]
    covered = {asset for row in observation["coverage"] for asset in row["assets"]}
    lines.append(
        "Unassessed assets: "
        + _text(
            ", ".join(sorted(set(observation["scope"]["assets"]) - covered))
            or "none outside listed coverage; incomplete checks remain qualified above"
        )
        + "."
    )
    lines += [
        f"- Provenance {_text(key)}: {_text(value)}"
        for key, value in observation["provenance"].items()
    ]
    if any(row["source"] == "game-math" for row in observation["coverage"]):
        lines.append(
            "Game mathematics concerns the supplied specification and sample only. Statistical sample evidence does not certify a deployed game, RNG, or regulatory compliance."
        )
    lines += ["", "## Technical findings and remediation", ""]
    annotations = (
        {row.finding_id: row for row in context.annotations} if context else {}
    )
    finding_bytes = omitted_findings = 0
    for row in comparison["findings"]:
        block_start = len(lines)
        finding = row["finding"]
        lines += [
            f"### Finding {row['findingId']}",
            f"State: {row['state']}. Asset: {_text(finding['asset'])}. Evidence expires: {row['evidenceExpiresAt']}.",
            f"Native source/tool: {_text(finding['source'])} / {_text(finding['tool'])}. Rule: {_text(finding['ruleId'])}. Native identity: {_text(finding['nativeId'])}.",
            "Reproduction evidence: "
            + ", ".join(_text(ref) for ref in finding["evidenceRefs"])
            + ".",
            f"Verification: {_text(row.get('verification') or 'not verified')}. Coverage identity: {_text(row.get('observedCoverageKey') or 'unavailable')}.",
        ]
        annotation = annotations.get(row["findingId"])
        if annotation:
            lines.append(
                f"Attributed commentary by {_text(annotation.author)}: {_text(annotation.annotation)}"
            )
            for field in (
                "reproduction",
                "remediation",
                "management_response",
                "priority",
                "business_risk",
                "severity",
                "cvss_vector",
                "risk_rationale",
                "environment_inputs",
            ):
                value = getattr(annotation, field)
                if value:
                    lines.append(
                        f"{field.replace('_', ' ').capitalize()}: {_text(value)}."
                    )
            lines.append(
                "CVSS vectors are supplied assessment inputs, not recalculated or independently validated scores. Omitted metrics remain undefined; severity is distinct from business risk."
            )
        else:
            lines.append(
                "Severity, CVSS metrics and business risk: undefined. Reproduce using the retained native evidence, assess environment-specific impact, remediate the affected rule, and run a scope-matched retest. No authored remediation or management response was supplied."
            )
        block_bytes = len(("\n".join(lines[block_start:]) + "\n").encode("utf-8"))
        if finding_bytes + block_bytes > MAX_FINDING_DETAIL_BYTES:
            del lines[block_start:]
            omitted_findings += 1
        else:
            finding_bytes += block_bytes
    if omitted_findings:
        lines.append(
            f"{omitted_findings} findings omitted from this presentation's detailed listing "
            f"to respect its byte budget; all {len(comparison['findings'])} authoritative findings "
            f"and their evidence remain in structured comparison {comparison_ref}. "
            "Selection follows native order, not risk priority. Omission is not a clean result."
        )
    if not comparison["findings"]:
        lines.append(
            "No findings recorded for these checks. Review incomplete or stale coverage before drawing conclusions."
        )
    lines += ["", "## Decisions and retest summary", ""]
    for row in comparison["decisions"]:
        decision = row["decision"]
        lines += [
            f"- Finding {decision['findingId']}: {decision['disposition']} ({row['status']}); author {_text(decision['author'])}; source {_text(decision['source'])}.",
            f"  Reason: {_text(decision['reason'])}. Review due: {decision['reviewAt']}. Retest: {_text(decision.get('retestOutcome') or 'not supplied')}.",
            "  Decision/retest evidence: "
            + ", ".join(_text(ref) for ref in decision["evidenceRefs"])
            + ".",
            f"  Review reasons: {_text(', '.join(row['reviewReasons']) or 'none')}. Conditions: {_text(', '.join(decision['reviewConditions']))}.",
        ]
    lines += [
        "Before evidence is linked through the baseline and finding references; after evidence is linked through the current scan and retest decisions. An absent finding alone is not a verified fix.",
        "",
        "## Limitations and unavailable material",
        "",
        "Partial, failed, authentication-failed, skipped/unsupported and stale checks do not establish clean coverage. Native artifacts remain restricted; offline packages include only selected authorized representations.",
        *[
            f"- Collection diagnostic: {_text(item)}"
            for item in work.get("diagnostics", [])
        ],
        "",
    ]
    return "\n".join(lines) + render_native_admission(native_reports)


def render_native_admission(native_reports):
    """Render bounded source context using the export owner's typed projection."""
    from moonmind.security.evidence_exports import (
        WithheldEvidence,
        prepare_evidence_member,
    )

    fields = {
        "service-inventory/v1": ("planKey", "nativeNetwork", "serviceInventory"),
        "tls-assessment/v1": ("settings",),
        "web-assessment/v1": ("admission",),
        "cloud-posture/v1": ("admission",),
        "image-assessment/v1": ("admission",),
        "game-math-assessment/v1": ("specDigest", "provenance", "limitations"),
    }
    if not native_reports:
        return ""
    lines = [
        "",
        "## Recorded source configuration",
        "",
        "These retained source declarations are context, not independent proof of execution or authorization. The admitted assessment above reads retained evidence; source configuration grants no new permission.",
    ]
    for ref, body in native_reports:
        lines.append(f"- Source summary: {_text(ref)}.")
        try:
            prepared, _, schema = prepare_evidence_member(
                body, content_type="application/json", hint=""
            )
        except WithheldEvidence:
            lines.append(
                "  Source configuration unavailable in the supported sanitized projection. Consult authorized native evidence; execution and authorization are not inferred."
            )
            continue
        document = json.loads(prepared)
        for field in fields.get(schema, ()):
            if field in document:
                lines.append(f"  {field}: {_text(document[field])}.")
        if schema == "game-math-assessment/v1":
            lines.append(
                "  Imported game mathematics is unclassified control evidence: a specification digest alone does not prove retained source inputs, actual execution, or deployed-game behavior."
            )
    return "\n".join(lines) + "\n"


def bound_report_presentation(body: bytes, *, structured_refs: list[str]) -> bytes:
    """Transform one bounded presentation with cumulative omission accounting.

    Both byte-selection stages keep whole UTF-8 lines. The single final footer
    is reserved before selection, never passed through a second truncation.
    Native records and transformation limits are unchanged.
    """
    from moonmind.security.evidence_exports import presentation_provenance

    lines = body.decode("utf-8").splitlines(keepends=True)
    links = ", ".join(_text(ref) for ref in dict.fromkeys(structured_refs))
    provenance = "\n" + presentation_provenance() + "\n"
    notice = (
        "\n\n## Presentation limit\n\n"
        "{count} presentation lines omitted to respect the UTF-8 byte budget. "
        "This is an abbreviated presentation, not a complete list of findings, "
        "decisions, controls or limitations. No omitted material is a clean result. "
        f"Full retained structured evidence and context: {links}. "
        "Native records are unchanged; select their authorized artifacts separately for offline review.\n"
    )
    reserve = len((notice.format(count=len(lines)) + provenance).encode("utf-8"))
    budget = MAX_REPORT_BYTES - reserve

    def prefix(source):
        kept, used = [], 0
        for line in source:
            size = len(line.encode("utf-8"))
            if used + size > budget:
                break
            kept.append(line)
            used += size
        return "".join(kept), len(source) - len(kept)

    selected, omitted = prefix(lines)
    transformed = redact_evidence_text(selected)
    selected, additional = prefix(transformed.splitlines(keepends=True))
    omitted += additional
    return (
        selected + (notice.format(count=omitted) if omitted else "") + provenance
    ).encode("utf-8")
