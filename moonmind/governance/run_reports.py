"""Per-run evidence-backed governance reports (MoonMind#3969)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

GOVERNANCE_REPORT_CONTRACT_VERSION = 1
GOVERNANCE_REPORT_KIND = "run_governance_report"

ProvenanceState = Literal[
    "observed", "unavailable", "unsupported", "not_applicable", "pending"
]
ReportStatus = Literal["ready", "partial", "pending", "failed"]
TerminalOutcome = Literal["succeeded", "failed", "cancelled", "timed_out"]
CleanupDisposition = Literal["succeeded", "partial", "pending", "failed", "unknown"]
ScanOutcome = Literal[
    "pass", "block", "unavailable", "unsupported", "not_applicable", "pending"
]
ReviewOutcome = Literal[
    "approved", "denied", "pending", "unavailable", "unsupported", "not_applicable"
]
CredentialOwnership = Literal["run_owned", "profile_owned"]
SpendProvenance = Literal["provider_reported", "estimate", "unavailable"]

TERMINAL_OUTCOMES: tuple[TerminalOutcome, ...] = (
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
)
PROVENANCE_STATES: tuple[ProvenanceState, ...] = (
    "observed",
    "unavailable",
    "unsupported",
    "not_applicable",
    "pending",
)

UNMEDIATED_ACTIONS_DISCLAIMER = (
    "Observed egress and scan entries cover MoonMind-mediated boundaries only. "
    "Arbitrary agent network/shell actions outside those boundaries were not "
    "universally mediated or observed."
)

OBSERVED_BLIND_SPOTS: tuple[str, ...] = (
    "Arbitrary agent-initiated network/shell activity outside MoonMind-mediated egress.",
    "Binary attachments, terminal input, and browser automation outside the text-scan contract.",
    "Provider-internal activity not exposed through provider-reported measurements.",
)

MAX_SECTION_ITEMS = 50
MAX_CREDENTIAL_ITEMS = 32
MAX_DIGEST_ENTRIES = 64
MAX_STRING_CHARS = 1024
MAX_REF_CHARS = 512
MAX_EVIDENCE_AGE = timedelta(hours=24 * 30)
MAX_RECONCILE_ITEMS = 100

_SECRET_KEY_PATTERN = re.compile(
    r"(?i)(token|password|secret|cookie|session|credential|grant|authorization|api[_-]?key|private[_-]?key)"
)
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(ghp_|github_pat_|AIza|ATATT|AKIA|sk-ant-|xox[bpas]-|token\s*[=:]|password\s*[=:]|"
    r"authorization\s*:|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
_RAW_URL_PATTERN = re.compile(r"(?i)^https?://[^\s]+$")
_SENSITIVE_URL_HINT = re.compile(r"(?i)(token|secret|password|key|auth|session|code=|signature=)")


class GovernanceReportError(ValueError):
    """Malformed governance-report input with a safe failure code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}{': ' + detail if detail else ''}")
        self.code = code


TOP_LEVEL_KEYS = frozenset(
    {
        "contract_version",
        "report_kind",
        "report_id",
        "logical_workflow_id",
        "run_id",
        "attempt",
        "created_at",
        "evidence_cutoff",
        "terminal_outcome",
        "status",
        "completeness",
        "input_digest",
        "supersedes",
        "policy",
        "profile",
        "image",
        "credentials",
        "egress",
        "container_jobs",
        "approvals",
        "outbound_scans",
        "workspace_changes",
        "publications",
        "cleanup",
        "spend_usage",
        "annotation",
        "source_artifact_digests",
        "observation_boundary",
        "owner",
    }
)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_time(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise GovernanceReportError("MALFORMED_SOURCE", f"{field_name} must be an ISO-8601 string")
    text = value.strip()
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise GovernanceReportError("MALFORMED_SOURCE", f"{field_name} is not parseable") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _require_short_str(value: object, *, field_name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise GovernanceReportError("MALFORMED_SOURCE", f"{field_name} must be a string")
    text = value.strip()
    if not text and not allow_empty:
        raise GovernanceReportError("MALFORMED_SOURCE", f"{field_name} must not be blank")
    if len(text) > MAX_REF_CHARS:
        raise GovernanceReportError("MALFORMED_SOURCE", f"{field_name} is too long")
    _reject_secret_value(text, path=field_name)
    return text


def _reject_secret_value(text: str, *, path: str) -> None:
    if _SECRET_VALUE_PATTERN.search(text):
        raise GovernanceReportError("UNSAFE_REPORT_FIELD", f"secret-like value at '{path}'")
    if _RAW_URL_PATTERN.search(text) and _SENSITIVE_URL_HINT.search(text):
        raise GovernanceReportError("UNSAFE_REPORT_FIELD", f"sensitive URL at '{path}'")


def _reject_secret_key(key: str, *, path: str) -> None:
    if _SECRET_KEY_PATTERN.search(key):
        raise GovernanceReportError("UNSAFE_REPORT_FIELD", f"secret-like key '{path}'")


def _provenance(value: object, *, field_name: str) -> ProvenanceState:
    text = str(value or "").strip()
    if text not in PROVENANCE_STATES:
        raise GovernanceReportError("MALFORMED_SOURCE", f"{field_name} has unknown provenance '{text}'")
    return text  # type: ignore[return-value]


def _artifact_ref(value: object, *, path: str) -> dict[str, str | int]:
    if not isinstance(value, Mapping):
        raise GovernanceReportError("MALFORMED_SOURCE", f"{path} must be an artifact ref")
    artifact_id = str(value.get("artifact_id") or "").strip()
    if not artifact_id or len(artifact_id) > MAX_REF_CHARS:
        raise GovernanceReportError("MALFORMED_SOURCE", f"{path}.artifact_id is required")
    _reject_secret_value(artifact_id, path=f"{path}.artifact_id")
    version = value.get("artifact_ref_v", 1)
    try:
        version_int = int(version)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise GovernanceReportError("MALFORMED_SOURCE", f"{path}.artifact_ref_v must be 1") from exc
    if version_int != 1:
        raise GovernanceReportError("MALFORMED_SOURCE", f"{path}.artifact_ref_v must be 1")
    return {"artifact_ref_v": 1, "artifact_id": artifact_id}


def _digest_of_canonical(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_input_digest(evidence: Mapping[str, Any]) -> str:
    """Compute the deterministic digest identifying one report input."""

    if not isinstance(evidence, Mapping):
        raise GovernanceReportError("MALFORMED_SOURCE", "evidence must be a mapping")
    return _digest_of_canonical({"v": GOVERNANCE_REPORT_CONTRACT_VERSION, "evidence": dict(evidence)})


def governance_report_id(*, logical_workflow_id: str, run_id: str, attempt: int, input_digest: str) -> str:
    """Return the idempotent report identity for one input digest."""

    seed = f"{logical_workflow_id}|{run_id}|{attempt}|{input_digest}"
    return f"govrep_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]}"


def _bounded_list(items: object, *, limit: int, field_name: str) -> list[Any]:
    if items is None:
        return []
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        raise GovernanceReportError("MALFORMED_SOURCE", f"{field_name} must be a list")
    if len(items) > limit:
        raise GovernanceReportError("MALFORMED_SOURCE", f"{field_name} exceeds bound of {limit}")
    return list(items)


def _provenanced_ref_section(
    raw: object, *, field_name: str, allow_empty_ref: bool = True
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise GovernanceReportError("MALFORMED_SOURCE", f"{field_name} must be a mapping")
    provenance = _provenance(raw.get("provenance"), field_name=f"{field_name}.provenance")
    ref_value = raw.get("ref")
    ref: str | None = None
    if ref_value is not None:
        if not isinstance(ref_value, str) or not ref_value.strip():
            raise GovernanceReportError("MALFORMED_SOURCE", f"{field_name}.ref must be a string")
        ref = _require_short_str(ref_value, field_name=f"{field_name}.ref")
    elif provenance == "observed" and not allow_empty_ref:
        raise GovernanceReportError("MALFORMED_SOURCE", f"{field_name}.ref is required when observed")
    source = str(raw.get("source") or "").strip()
    if source:
        if len(source) > MAX_REF_CHARS:
            raise GovernanceReportError("MALFORMED_SOURCE", f"{field_name}.source is too long")
        _reject_secret_value(source, path=f"{field_name}.source")
    return {"provenance": provenance, "ref": ref, "source": source or None}


def _build_credentials(raw: object) -> dict[str, Any]:
    items = _bounded_list(raw, limit=MAX_CREDENTIAL_ITEMS, field_name="credentials")
    leases: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        path = f"credentials[{index}]"
        if not isinstance(item, Mapping):
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path} must be a mapping")
        for key in item:
            _reject_secret_key(str(key), path=f"{path}.{key}")
        lease_ref = _require_short_str(item.get("lease_ref"), field_name=f"{path}.lease_ref")
        ownership = str(item.get("ownership") or "").strip()
        if ownership not in ("run_owned", "profile_owned"):
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path}.ownership must be run_owned|profile_owned")
        state = str(item.get("state") or "").strip() or "unknown"
        if len(state) > MAX_REF_CHARS:
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path}.state is too long")
        evidence_ref = None
        if item.get("evidence_ref") is not None:
            evidence_ref = _artifact_ref(item.get("evidence_ref"), path=f"{path}.evidence_ref")
        leases.append(
            {"lease_ref": lease_ref, "ownership": ownership, "state": state, "evidence_ref": evidence_ref}
        )
    provenance: ProvenanceState = "observed" if leases else "unavailable"
    return {"provenance": provenance, "leases": leases}


def _build_egress(raw: object) -> dict[str, Any]:
    items = _bounded_list(raw, limit=MAX_SECTION_ITEMS, field_name="egress")
    decisions: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        path = f"egress[{index}]"
        if not isinstance(item, Mapping):
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path} must be a mapping")
        for key in item:
            _reject_secret_key(str(key), path=f"{path}.{key}")
        destination = _require_short_str(item.get("destination_ref"), field_name=f"{path}.destination_ref")
        decision = str(item.get("decision") or "").strip()
        if decision not in ("allow", "deny", "unavailable"):
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path}.decision must be allow|deny|unavailable")
        policy_ref = None
        if item.get("policy_ref") is not None:
            policy_ref = _require_short_str(item.get("policy_ref"), field_name=f"{path}.policy_ref")
        decisions.append({"destination_ref": destination, "decision": decision, "policy_ref": policy_ref})
    provenance: ProvenanceState = "observed" if decisions else "unavailable"
    return {
        "provenance": provenance,
        "decisions": decisions,
        "coverage_note": UNMEDIATED_ACTIONS_DISCLAIMER,
    }


def _build_container_jobs(raw: object) -> dict[str, Any]:
    items = _bounded_list(raw, limit=MAX_SECTION_ITEMS, field_name="container_jobs")
    jobs: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        path = f"container_jobs[{index}]"
        if not isinstance(item, Mapping):
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path} must be a mapping")
        job_ref = _require_short_str(item.get("job_ref"), field_name=f"{path}.job_ref")
        status = str(item.get("status") or "").strip() or "unknown"
        if len(status) > MAX_REF_CHARS:
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path}.status is too long")
        jobs.append({"job_ref": job_ref, "status": status})
    return {"provenance": ("observed" if jobs else "unavailable"), "jobs": jobs}


def _build_approvals(raw: object) -> dict[str, Any]:
    items = _bounded_list(raw, limit=MAX_SECTION_ITEMS, field_name="approvals")
    reviews: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        path = f"approvals[{index}]"
        if not isinstance(item, Mapping):
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path} must be a mapping")
        review_ref = _require_short_str(item.get("review_ref"), field_name=f"{path}.review_ref")
        outcome = str(item.get("outcome") or "").strip()
        if outcome not in ("approved", "denied", "pending", "unavailable", "unsupported", "not_applicable"):
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path}.outcome is unknown")
        reviews.append({"review_ref": review_ref, "outcome": outcome})
    return {"provenance": ("observed" if reviews else "unavailable"), "reviews": reviews}


def _build_outbound_scans(raw: object) -> dict[str, Any]:
    items = _bounded_list(raw, limit=MAX_SECTION_ITEMS, field_name="outbound_scans")
    scans: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        path = f"outbound_scans[{index}]"
        if not isinstance(item, Mapping):
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path} must be a mapping")
        surface = _require_short_str(item.get("surface"), field_name=f"{path}.surface")
        outcome = str(item.get("outcome") or "").strip()
        if outcome not in ("pass", "block", "unavailable", "unsupported", "not_applicable", "pending"):
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path}.outcome is unknown")
        categories = _bounded_list(item.get("finding_categories", []), limit=20, field_name=f"{path}.finding_categories")
        for category in categories:
            if not isinstance(category, str) or not category.strip() or len(category) > MAX_REF_CHARS:
                raise GovernanceReportError("MALFORMED_SOURCE", f"{path}.finding_categories entries must be short strings")
            _reject_secret_value(category, path=f"{path}.finding_categories")
        locations = _bounded_list(item.get("location_refs", []), limit=20, field_name=f"{path}.location_refs")
        for location in locations:
            if not isinstance(location, str) or not location.strip() or len(location) > MAX_REF_CHARS:
                raise GovernanceReportError("MALFORMED_SOURCE", f"{path}.location_refs entries must be short strings")
            _reject_secret_value(location, path=f"{path}.location_refs")
        scans.append(
            {"surface": surface, "outcome": outcome, "finding_categories": list(categories), "location_refs": list(locations)}
        )
    return {"provenance": ("observed" if scans else "unavailable"), "scans": scans}


def _build_ref_list_section(raw: object, *, field_name: str, item_key: str, ref_key: str) -> dict[str, Any]:
    items = _bounded_list(raw, limit=MAX_SECTION_ITEMS, field_name=field_name)
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        path = f"{field_name}[{index}]"
        if not isinstance(item, Mapping):
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path} must be a mapping")
        ref = _require_short_str(item.get(ref_key), field_name=f"{path}.{ref_key}")
        status = str(item.get("status") or "").strip() or "unknown"
        if len(status) > MAX_REF_CHARS:
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path}.status is too long")
        evidence_ref = None
        if item.get("evidence_ref") is not None:
            evidence_ref = _artifact_ref(item.get("evidence_ref"), path=f"{path}.evidence_ref")
        entries.append({item_key: ref, "status": status, "evidence_ref": evidence_ref})
    return {"provenance": ("observed" if entries else "unavailable"), item_key + "s": entries}


def _build_cleanup(raw: object) -> dict[str, Any]:
    if raw is None:
        return {"provenance": "pending", "disposition": "unknown", "detail_ref": None}
    if not isinstance(raw, Mapping):
        raise GovernanceReportError("MALFORMED_SOURCE", "cleanup must be a mapping")
    disposition = str(raw.get("disposition") or "").strip() or "unknown"
    if disposition not in ("succeeded", "partial", "pending", "failed", "unknown"):
        raise GovernanceReportError("MALFORMED_SOURCE", "cleanup.disposition is unknown")
    detail_ref = None
    if raw.get("detail_ref") is not None:
        detail_ref = _artifact_ref(raw.get("detail_ref"), path="cleanup.detail_ref")
    provenance = _provenance(raw.get("provenance", "observed"), field_name="cleanup.provenance")
    return {"provenance": provenance, "disposition": disposition, "detail_ref": detail_ref}


def _build_spend_usage(raw: object) -> dict[str, Any]:
    if raw is None:
        return {"provenance": "unavailable", "measurements": []}
    if not isinstance(raw, Mapping):
        raise GovernanceReportError("MALFORMED_SOURCE", "spend_usage must be a mapping")
    provenance = _provenance(raw.get("provenance", "unavailable"), field_name="spend_usage.provenance")
    measurements = _bounded_list(raw.get("measurements", []), limit=20, field_name="spend_usage.measurements")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(measurements):
        path = f"spend_usage.measurements[{index}]"
        if not isinstance(item, Mapping):
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path} must be a mapping")
        kind = str(item.get("kind") or "").strip()
        if kind not in ("provider_reported", "estimate", "unavailable"):
            raise GovernanceReportError("MALFORMED_SOURCE", f"{path}.kind must distinguish provider-reported/estimate/unavailable")
        label = _require_short_str(item.get("label"), field_name=f"{path}.label")
        normalized.append({"kind": kind, "label": label})
    return {"provenance": provenance, "measurements": normalized}


def _build_source_digests(raw: object) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise GovernanceReportError("MALFORMED_SOURCE", "source_artifact_digests must be a mapping")
    if len(raw) > MAX_DIGEST_ENTRIES:
        raise GovernanceReportError("MALFORMED_SOURCE", "source_artifact_digests exceeds bound")
    digests: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key or "").strip()
        if not name or len(name) > MAX_REF_CHARS:
            raise GovernanceReportError("MALFORMED_SOURCE", "source_artifact_digests keys must be short strings")
        _reject_secret_key(name, path=f"source_artifact_digests.{name}")
        digest = str(value or "").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{16,128}", digest or ""):
            raise GovernanceReportError("MALFORMED_SOURCE", f"source_artifact_digests['{name}'] must be a hex digest")
        digests[name] = digest.lower()
    return digests


def scan_outcome_is_pass(entry: Mapping[str, Any]) -> bool:
    """Return True only for an explicitly observed pass; never upgrade missing data."""

    return bool(isinstance(entry, Mapping) and entry.get("outcome") == "pass")


def review_outcome_is_approved(entry: Mapping[str, Any]) -> bool:
    """Return True only for an explicitly observed approval."""

    return bool(isinstance(entry, Mapping) and entry.get("outcome") == "approved")


def build_governance_report(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Build a deterministic, allowlisted governance report from authoritative evidence."""

    if not isinstance(evidence, Mapping):
        raise GovernanceReportError("MALFORMED_SOURCE", "evidence must be a mapping")
    for key in evidence:
        name = str(key)
        if name not in TOP_LEVEL_KEYS:
            _reject_secret_key(name, path=name)
            raise GovernanceReportError("MALFORMED_SOURCE", f"unsupported evidence key '{name}'")

    logical_workflow_id = _require_short_str(evidence.get("logical_workflow_id"), field_name="logical_workflow_id")
    run_id = _require_short_str(evidence.get("run_id"), field_name="run_id")
    attempt_raw = evidence.get("attempt", 0)
    try:
        attempt = int(attempt_raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise GovernanceReportError("MALFORMED_SOURCE", "attempt must be an integer") from exc
    if attempt < 0 or attempt > 10_000:
        raise GovernanceReportError("MALFORMED_SOURCE", "attempt is out of range")

    terminal_outcome = str(evidence.get("terminal_outcome") or "").strip()
    if terminal_outcome not in TERMINAL_OUTCOMES:
        raise GovernanceReportError("MALFORMED_SOURCE", "terminal_outcome must be succeeded|failed|cancelled|timed_out")

    created_at = str(evidence.get("created_at") or _utcnow_iso())
    evidence_cutoff = str(evidence.get("evidence_cutoff") or created_at)
    _parse_time(created_at, field_name="created_at")
    _parse_time(evidence_cutoff, field_name="evidence_cutoff")

    completeness = str(evidence.get("completeness") or "partial").strip()
    if completeness not in ("complete", "partial", "pending", "failed"):
        raise GovernanceReportError("MALFORMED_SOURCE", "completeness is unknown")
    status: ReportStatus = "ready" if completeness == "complete" else ("failed" if completeness == "failed" else ("pending" if completeness == "pending" else "partial"))

    owner = _require_short_str(evidence.get("owner", "unknown-owner"), field_name="owner")

    policy = _provenanced_ref_section(evidence.get("policy", {"provenance": "unavailable"}), field_name="policy")
    profile = _provenanced_ref_section(evidence.get("profile", {"provenance": "unavailable"}), field_name="profile")
    image = _provenanced_ref_section(evidence.get("image", {"provenance": "unavailable"}), field_name="image")

    credentials = _build_credentials(evidence.get("credentials"))
    egress = _build_egress(evidence.get("egress"))
    container_jobs = _build_container_jobs(evidence.get("container_jobs"))
    approvals = _build_approvals(evidence.get("approvals"))
    outbound_scans = _build_outbound_scans(evidence.get("outbound_scans"))
    workspace_changes = _build_ref_list_section(
        evidence.get("workspace_changes"), field_name="workspace_changes", item_key="change", ref_key="change_ref"
    )
    publications = _build_ref_list_section(
        evidence.get("publications"), field_name="publications", item_key="publication", ref_key="publication_ref"
    )
    cleanup = _build_cleanup(evidence.get("cleanup"))
    spend_usage = _build_spend_usage(evidence.get("spend_usage"))
    source_digests = _build_source_digests(evidence.get("source_artifact_digests"))

    annotation_raw = evidence.get("annotation")
    annotation: str | None = None
    if annotation_raw is not None:
        if not isinstance(annotation_raw, str) or not annotation_raw.strip():
            raise GovernanceReportError("MALFORMED_SOURCE", "annotation must be a non-empty string")
        if len(annotation_raw) > MAX_STRING_CHARS:
            raise GovernanceReportError("MALFORMED_SOURCE", "annotation is too long")
        _reject_secret_value(annotation_raw, path="annotation")
        annotation = annotation_raw.strip()

    supersedes_raw = evidence.get("supersedes")
    supersedes: str | None = None
    if supersedes_raw is not None:
        supersedes = _require_short_str(supersedes_raw, field_name="supersedes")

    input_digest = build_input_digest(
        {
            "logical_workflow_id": logical_workflow_id,
            "run_id": run_id,
            "attempt": attempt,
            "terminal_outcome": terminal_outcome,
            "evidence_cutoff": evidence_cutoff,
            "policy": policy,
            "profile": profile,
            "image": image,
            "credentials": credentials,
            "egress": egress,
            "container_jobs": container_jobs,
            "approvals": approvals,
            "outbound_scans": outbound_scans,
            "workspace_changes": workspace_changes,
            "publications": publications,
            "cleanup": cleanup,
            "spend_usage": spend_usage,
            "source_artifact_digests": source_digests,
        }
    )
    report_id = governance_report_id(
        logical_workflow_id=logical_workflow_id, run_id=run_id, attempt=attempt, input_digest=input_digest
    )

    report: dict[str, Any] = {
        "contract_version": GOVERNANCE_REPORT_CONTRACT_VERSION,
        "report_kind": GOVERNANCE_REPORT_KIND,
        "report_id": report_id,
        "logical_workflow_id": logical_workflow_id,
        "run_id": run_id,
        "attempt": attempt,
        "created_at": created_at,
        "evidence_cutoff": evidence_cutoff,
        "terminal_outcome": terminal_outcome,
        "status": status,
        "completeness": completeness,
        "input_digest": input_digest,
        "supersedes": supersedes,
        "policy": policy,
        "profile": profile,
        "image": image,
        "credentials": credentials,
        "egress": egress,
        "container_jobs": container_jobs,
        "approvals": approvals,
        "outbound_scans": outbound_scans,
        "workspace_changes": workspace_changes,
        "publications": publications,
        "cleanup": cleanup,
        "spend_usage": spend_usage,
        "annotation": annotation,
        "source_artifact_digests": source_digests,
        "observation_boundary": {
            "coverage_note": UNMEDIATED_ACTIONS_DISCLAIMER,
            "blind_spots": list(OBSERVED_BLIND_SPOTS),
        },
        "owner": owner,
    }
    for key in report:
        if key not in TOP_LEVEL_KEYS:
            raise GovernanceReportError("MALFORMED_SOURCE", f"unsupported report key '{key}'")
    # Deterministic serialization check: the report must round-trip byte-identically.
    serialized = json.dumps(report, sort_keys=True, separators=(",", ":"), default=str)
    if json.loads(serialized) != report:
        raise GovernanceReportError("MALFORMED_SOURCE", "report is not deterministically serializable")
    return report


def render_governance_report_markdown(report: Mapping[str, Any]) -> str:
    """Render human-readable Markdown generated from the same JSON report."""

    if not isinstance(report, Mapping):
        raise GovernanceReportError("MALFORMED_SOURCE", "report must be a mapping")
    if report.get("contract_version") != GOVERNANCE_REPORT_CONTRACT_VERSION:
        raise GovernanceReportError("MALFORMED_SOURCE", "unsupported contract_version")
    if report.get("report_kind") != GOVERNANCE_REPORT_KIND:
        raise GovernanceReportError("MALFORMED_SOURCE", "unsupported report_kind")

    def section_state(section: object) -> str:
        if isinstance(section, Mapping):
            return str(section.get("provenance") or "unavailable")
        return "unavailable"

    scans = report.get("outbound_scans") if isinstance(report.get("outbound_scans"), Mapping) else {}
    scan_lines: list[str] = []
    for entry in scans.get("scans", []) if isinstance(scans.get("scans"), list) else []:
        if not isinstance(entry, Mapping):
            continue
        outcome = str(entry.get("outcome") or "unavailable")
        verdict = "PASS" if outcome == "pass" else ("BLOCK" if outcome == "block" else outcome.upper())
        scan_lines.append(f"- {entry.get('surface')}: {verdict}")
    scans_block = "\n".join(scan_lines) if scan_lines else "- no observed scans (unavailable is not a pass)"

    lines = [
        f"# Governance report {report.get('report_id')}",
        "",
        f"- Workflow: {report.get('logical_workflow_id')} / run {report.get('run_id')} (attempt {report.get('attempt')})",
        f"- Outcome: {report.get('terminal_outcome')} — status {report.get('status')} / {report.get('completeness')}",
        f"- Evidence cutoff: {report.get('evidence_cutoff')}",
        f"- Input digest: {report.get('input_digest')}",
    ]
    if report.get("supersedes"):
        lines.append(f"- Supersedes: {report.get('supersedes')}")
    lines += [
        f"- Policy: {section_state(report.get('policy'))} / Profile: {section_state(report.get('profile'))} / Image: {section_state(report.get('image'))}",
        f"- Credentials: {section_state(report.get('credentials'))} / Egress: {section_state(report.get('egress'))} / Cleanup: {report.get('cleanup', {}).get('disposition', 'unknown') if isinstance(report.get('cleanup'), Mapping) else 'unknown'}",
        "",
        "## Outbound scans (unavailable is never a pass)",
        "",
        scans_block,
        "",
        "## Observation boundary",
        "",
        str(
            report.get("observation_boundary", {}).get("coverage_note", UNMEDIATED_ACTIONS_DISCLAIMER)
            if isinstance(report.get("observation_boundary"), Mapping)
            else UNMEDIATED_ACTIONS_DISCLAIMER
        ),
    ]
    blind_spots = (
        report.get("observation_boundary", {}).get("blind_spots", [])
        if isinstance(report.get("observation_boundary"), Mapping)
        else []
    )
    if isinstance(blind_spots, list):
        for spot in blind_spots:
            lines.append(f"- {spot}")
    if report.get("annotation"):
        lines += ["", "> Model-generated annotation (non-authoritative):", "", f"> {report.get('annotation')}"]
    lines.append("")
    return "\n".join(lines)


@dataclass
class ReportGenerationStatus:
    """Visible, recoverable status when no report bytes exist yet."""

    status: ReportStatus
    reason_code: str
    detail: str = ""
    recoverable: bool = True


@dataclass
class ReportFinalization:
    """Outcome of finalizing one terminal execution without rewriting canonical state."""

    canonical_outcome: TerminalOutcome
    canonical_outcome_preserved: bool = True
    report: dict[str, Any] | None = None
    generation_status: ReportGenerationStatus | None = None


@dataclass
class GovernanceReportStore:
    """Immutable in-memory report store with idempotent identity.

    Retrying the same input digest reuses the stored report. Late or corrected
    evidence writes a new immutable version carrying a ``supersedes`` relation
    instead of overwriting the original.
    """

    _reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    _by_input: dict[str, str] = field(default_factory=dict)
    fail_writes: bool = False

    def put(self, report: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Store a report immutably; return (stored, reused)."""

        if self.fail_writes:
            raise GovernanceReportError("REPORT_STORE_UNAVAILABLE", "report store is unavailable")
        if not isinstance(report, Mapping):
            raise GovernanceReportError("MALFORMED_SOURCE", "report must be a mapping")
        report_id = str(report.get("report_id") or "").strip()
        input_digest = str(report.get("input_digest") or "").strip()
        if not report_id or not input_digest:
            raise GovernanceReportError("MALFORMED_SOURCE", "report_id and input_digest are required")
        existing_id = self._by_input.get(input_digest)
        if existing_id is not None and existing_id in self._reports:
            return self._reports[existing_id], True
        if report_id in self._reports:
            # Immutable write contract: never overwrite an existing report id.
            if self._reports[report_id] != dict(report):
                raise GovernanceReportError("REPORT_ID_COLLISION", "report_id already stored with different content")
            return self._reports[report_id], True
        stored = dict(report)
        self._reports[report_id] = stored
        self._by_input[input_digest] = report_id
        return stored, False

    def get(self, report_id: str) -> dict[str, Any] | None:
        """Return a stored report copy or None."""

        stored = self._reports.get(str(report_id or ""))
        return dict(stored) if stored is not None else None

    def supersede(self, old_report_id: str, new_evidence: Mapping[str, Any]) -> dict[str, Any]:
        """Write a new immutable version that explicitly supersedes the old report."""

        old = self._reports.get(str(old_report_id or ""))
        if old is None:
            raise GovernanceReportError("REPORT_NOT_FOUND", "superseded report is unknown")
        merged = dict(new_evidence)
        merged["supersedes"] = old["report_id"]
        # Preserve logical-run/attempt continuity across continuation and recovery.
        for key in ("logical_workflow_id", "run_id", "attempt"):
            merged.setdefault(key, old[key])
        new_report = build_governance_report(merged)
        stored, reused = self.put(new_report)
        if reused:
            return stored
        return stored

    def __len__(self) -> int:
        return len(self._reports)


def finalize_governance_report(
    evidence: Mapping[str, Any],
    *,
    store: GovernanceReportStore | None = None,
    cancelled_during_finalization: bool = False,
) -> ReportFinalization:
    """Finalize one terminal execution into a report or a recoverable status.

    Reporting failure is auxiliary: the canonical terminal outcome is always
    preserved, cancellation is never prevented, and no exception escapes for
    store failures or cancellation races.
    """

    canonical = str(evidence.get("terminal_outcome") or "").strip()
    if canonical not in TERMINAL_OUTCOMES:
        return ReportFinalization(
            canonical_outcome="failed",
            report=None,
            generation_status=ReportGenerationStatus(
                status="failed", reason_code="MALFORMED_SOURCE", detail="terminal_outcome is unknown", recoverable=False
            ),
        )
    outcome: TerminalOutcome = canonical  # type: ignore[assignment]
    if cancelled_during_finalization:
        return ReportFinalization(
            canonical_outcome=outcome,
            report=None,
            generation_status=ReportGenerationStatus(
                status="pending",
                reason_code="CANCELLED_DURING_FINALIZATION",
                detail="cancellation preserved; report generation can be retried by the reconciler",
                recoverable=True,
            ),
        )
    try:
        report = build_governance_report(evidence)
    except GovernanceReportError as exc:
        return ReportFinalization(
            canonical_outcome=outcome,
            report=None,
            generation_status=ReportGenerationStatus(
                status="failed", reason_code=exc.code, detail=str(exc), recoverable=True
            ),
        )
    if store is None:
        return ReportFinalization(canonical_outcome=outcome, report=report)
    try:
        stored, _ = store.put(report)
    except GovernanceReportError as exc:
        return ReportFinalization(
            canonical_outcome=outcome,
            report=None,
            generation_status=ReportGenerationStatus(
                status="failed", reason_code=exc.code, detail=str(exc), recoverable=True
            ),
        )
    return ReportFinalization(canonical_outcome=outcome, report=stored)


def reconcile_missing_reports(
    executions: Sequence[Mapping[str, Any]],
    *,
    known_report_ids: Sequence[str] = (),
    limit: int = MAX_RECONCILE_ITEMS,
) -> list[dict[str, Any]]:
    """Bounded reconciler for crash-before-finalization gaps.

    Pure function returning retryable work items so existing worker/schedule
    infrastructure can drive re-finalization without a new always-on service.
    """

    known = {str(report_id) for report_id in known_report_ids}
    items: list[dict[str, Any]] = []
    for execution in list(executions or [])[:limit]:
        if not isinstance(execution, Mapping):
            continue
        run_id = str(execution.get("run_id") or "").strip()
        if not run_id:
            continue
        report_id = str(execution.get("report_id") or "").strip()
        if report_id and report_id in known:
            continue
        outcome = str(execution.get("terminal_outcome") or "").strip()
        if outcome not in TERMINAL_OUTCOMES:
            continue
        items.append(
            {
                "run_id": run_id,
                "logical_workflow_id": str(execution.get("logical_workflow_id") or "").strip() or "unknown-workflow",
                "attempt": int(execution.get("attempt", 0) or 0),
                "terminal_outcome": outcome,
                "reason": "missing_report_after_terminal_outcome",
            }
        )
    return items


@dataclass(frozen=True)
class GovernanceAccessResult:
    allowed: bool
    code: str
    detail: str = ""


def authorize_governance_report_access(
    report: Mapping[str, Any],
    *,
    requester_owner: str,
    now: datetime | None = None,
    expected_digests: Mapping[str, str] | None = None,
) -> GovernanceAccessResult:
    """Enforce owner, expiry, digest, and shape checks with explicit safe outcomes."""

    if not isinstance(report, Mapping) or report.get("report_kind") != GOVERNANCE_REPORT_KIND:
        return GovernanceAccessResult(False, "MALFORMED_SOURCE", "report shape is not a governance report")
    owner = str(report.get("owner") or "").strip()
    requester = str(requester_owner or "").strip()
    if not requester or requester != owner:
        return GovernanceAccessResult(False, "WRONG_OWNER", "requester does not own this report")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        cutoff = _parse_time(report.get("evidence_cutoff"), field_name="evidence_cutoff")
    except GovernanceReportError as exc:
        return GovernanceAccessResult(False, exc.code, str(exc))
    if current - cutoff > MAX_EVIDENCE_AGE:
        return GovernanceAccessResult(False, "EVIDENCE_EXPIRED", "evidence cutoff is beyond retention")
    stored_digests = report.get("source_artifact_digests") if isinstance(report.get("source_artifact_digests"), Mapping) else {}
    for name, expected in dict(expected_digests or {}).items():
        actual = stored_digests.get(name) if isinstance(stored_digests, Mapping) else None
        if actual != expected:
            return GovernanceAccessResult(False, "DIGEST_MISMATCH", f"source artifact '{name}' digest mismatch")
    return GovernanceAccessResult(True, "OK")


@dataclass(frozen=True)
class WorkflowDetailGovernanceLink:
    href: str
    status: ReportStatus
    explanation: str
    download_ref: dict[str, str | int] | None = None


def build_workflow_detail_governance_link(
    *,
    logical_workflow_id: str,
    report: Mapping[str, Any] | None = None,
    generation_status: ReportGenerationStatus | None = None,
    download_ref: Mapping[str, Any] | None = None,
) -> WorkflowDetailGovernanceLink:
    """Build the Workflow Detail governance link with safe, authorized download."""

    workflow_id = str(logical_workflow_id or "").strip() or "unknown-workflow"
    safe_id = workflow_id.replace("/", "_")
    if report is not None:
        status = str(report.get("status") or "partial").strip()
        if status not in ("ready", "partial", "pending", "failed"):
            status = "partial"
        detail_ref: dict[str, str | int] | None = None
        if download_ref is not None:
            detail_ref = _artifact_ref(download_ref, path="download_ref")
        explanations = {
            "ready": "Governance report is ready from verified authoritative evidence.",
            "partial": "Governance report is partial: some evidence was unavailable and is shown as such.",
            "pending": "Governance report is pending: evidence collection has not completed.",
            "failed": "Report generation failed as an auxiliary step; task and publication state are unchanged.",
        }
        return WorkflowDetailGovernanceLink(
            href=f"/workflows/{safe_id}/evidence?report={report.get('report_id', 'unknown')}",
            status=status,  # type: ignore[assignment]
            explanation=explanations[status],
            download_ref=detail_ref,
        )
    reason = generation_status.reason_code if generation_status is not None else "REPORT_PENDING"
    status_value: ReportStatus = generation_status.status if generation_status is not None else "pending"
    return WorkflowDetailGovernanceLink(
        href=f"/workflows/{safe_id}/evidence",
        status=status_value,
        explanation=f"Governance report is {status_value}: {reason}. Historical entries never expose current credentials and never claim unresolved cleanup succeeded.",
    )
