"""OpenSCAP endpoint evaluation and result import on the scanner job contracts.

An offline job evaluates a ComplianceAsCode (SCAP Security Guide) profile
against a captured root filesystem or image archive, or imports ARF or XCCDF
results the operator produced with OpenSCAP on the endpoint. No job mounts a
container daemon socket, reaches a network, runs privileged or remediates.

Only an XCCDF ``TestResult`` rebuilt from allow-listed fields is retained: the
copied content, OVAL system characteristics (which can quote file contents)
and host identifiers in the native report never leave the job. The worker
summarizes the retained result again, so a job cannot assert its own summary.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from xml.etree import ElementTree as ET

from pydantic import AwareDatetime, Field

from moonmind.schemas.repository_scanner_models import EndpointAssessment
from moonmind.security.assessments import Contract, Items, Text

INPUT_NAME = "endpoint"
INPUT_FILES = {"rootfs": "rootfs.tar", "image": "image.tar", "results": "results.xml"}
TAILORING_INPUT, TAILORING_FILE = "tailoring", "tailoring.xml"
#: Shipped ComplianceAsCode datastreams and their manifest in the scanner image.
CONTENT_DIRECTORY = "/opt/moonmind-endpoint-content"
XCCDF = "http://checklists.nist.gov/xccdf/1.2"
_ARF = "http://scap.nist.gov/schema/asset-reporting-format/1.1"
RESULTS = (
    "pass",
    "fail",
    "error",
    "unknown",
    "notapplicable",
    "notchecked",
    "notselected",
    "informational",
    "fixed",
)
XccdfResult = Literal[
    "pass",
    "fail",
    "error",
    "unknown",
    "notapplicable",
    "notchecked",
    "notselected",
    "informational",
    "fixed",
]
#: Why a rule that neither passed nor failed is a coverage state, never a pass.
REASONS = {
    "error": "OpenSCAP could not complete the check.",
    "unknown": "OpenSCAP could not determine the result.",
    "notapplicable": "The rule's platform does not apply to the examined system.",
    "notchecked": "The rule has no automated check OpenSCAP could run.",
    "notselected": "The evaluated profile does not select the rule.",
    "informational": "The rule is informational; it neither passes nor fails.",
    "fixed": (
        "The run that produced the result changed the system for this rule; "
        "the changed state was not evaluated again."
    ),
}
#: Checks an offline copy cannot answer, whatever OpenSCAP printed for them.
OFFLINE_REASONS = {
    "runtime": (
        "The check reads the running system (processes, kernel parameters, "
        "services, mounts or interfaces), which an offline copy does not have."
    ),
    "metadata": (
        "The check reads file ownership or permission metadata, which an "
        "unprivileged offline copy does not keep."
    ),
}
#: Combining repeated results for one rule: a failure dominates, and a rule
#: passes only when every instance passed.
_PRECEDENCE = (
    "fail",
    "error",
    "unknown",
    "fixed",
    "notchecked",
    "informational",
    "notapplicable",
    "notselected",
    "pass",
)
UNSUPPORTED_PLATFORMS = (
    "windows",
    "macos",
    "mobile-devices",
    "regulated-gaming-devices",
)
OBSERVATION_STATEMENTS = {
    "offline-copy": (
        "MoonMind evaluated an offline copy identified by its digest. It is not "
        "MoonMind's own observation of the running device: runtime state, file "
        "ownership and permissions, and changes after capture are outside it."
    ),
    "operator-supplied": (
        "The operator ran OpenSCAP on the endpoint and supplied these results. "
        "MoonMind did not run the evaluation or observe the device; the target, "
        "content and timing are the result's own statements."
    ),
}
#: ComplianceAsCode datastreams the scanner image ships (``tools/ci``
#: ``repository_security_image.CONTENT_PRODUCTS``): dpkg-based distributions,
#: which the image's Debian-built OpenSCAP evaluates offline.
SHIPPED_DATASTREAMS = (
    "ssg-debian12-ds.xml",
    "ssg-debian13-ds.xml",
    "ssg-ubuntu2204-ds.xml",
    "ssg-ubuntu2404-ds.xml",
)
#: The general best-practices choice: ComplianceAsCode's standard profile.
GENERAL_PROFILE = "xccdf_org.ssgproject.content_profile_standard"
#: What an endpoint plan asks the operator for, in the plan and its context.
PLAN_REQUIRED_INPUTS = (
    "The endpoint's stable name as an asset of the plan subject; findings attach to it.",
    "A root filesystem tar or Docker/OCI image archive uploaded as an artifact of "
    "the saved environment, with a shipped ComplianceAsCode datastream "
    "(ssg-<product>-ds.xml) and one of its XCCDF profiles; or ARF or XCCDF "
    "results the operator produced by running OpenSCAP on the endpoint.",
    "Optionally an XCCDF tailoring file as its own artifact, and the XCCDF rule "
    "IDs the selected standards name, so a profile that leaves one unselected "
    "is reported as partial.",
)
CONTENT_RIGHTS = (
    "ComplianceAsCode (SCAP Security Guide) profiles are used under their own "
    "identity and BSD-3-Clause license. A profile aligned with a benchmark such "
    "as CIS or DISA STIG is reported as aligned with it, never as that benchmark "
    "or as a certification. MoonMind retains no benchmark text."
)
INTERPRETATION = (
    "Pass and fail cover only the rules evaluated, on the examined subject, "
    "under the recorded content and profile. Linux distributions supported by "
    "the shipped ComplianceAsCode content are assessed; Windows, macOS, mobile "
    "devices and regulated gaming devices are unsupported coverage."
)
#: Profile name tokens and the published benchmark the profile aligns with.
_ALIGNMENT = (
    ("cis", "CIS Benchmark"),
    ("stig", "DISA STIG"),
    ("anssi", "ANSSI BP-028"),
    ("pci_dss", "PCI DSS"),
    ("hipaa", "HIPAA"),
    ("ospp", "NIAP OSPP"),
    ("e8", "ACSC Essential Eight"),
    ("ism", "ACSC ISM"),
    ("cui", "NIST SP 800-171"),
)
_COMPLIANCE_AS_CODE = "xccdf_org.ssgproject.content_benchmark_"
_RULE_ID = re.compile(r"^xccdf_[A-Za-z0-9.-]+_rule_[A-Za-z0-9_.-]{1,128}$")
_PROFILE_ID = re.compile(r"^xccdf_[A-Za-z0-9.-]+_profile_[A-Za-z0-9_.-]{1,128}$")
_BENCHMARK_ID = re.compile(r"^xccdf_[A-Za-z0-9.-]+_benchmark_[A-Za-z0-9_.-]{1,128}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+#@-]{0,255}$")
_SEVERITY = {"unknown", "info", "low", "medium", "high"}
_MAX_RULES = 10_000
_MAX_MESSAGES = 4
_MAX_TEXT = 512


class EndpointResultsError(ValueError):
    """A bounded diagnostic code; never carries document text."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def build_endpoint_assessment_job(
    assessment: EndpointAssessment, *, image_source_ref: str = "repository-security"
) -> dict:
    """Workload only; admission stamps workspace and artifact authorization."""
    attachments = [
        {
            "name": INPUT_NAME,
            "artifactRef": assessment.input_artifact_ref,
            "fileName": INPUT_FILES[assessment.target],
            "maxBytes": assessment.max_input_bytes,
        }
    ]
    if assessment.tailoring_artifact_ref:
        attachments.append(
            {
                "name": TAILORING_INPUT,
                "artifactRef": assessment.tailoring_artifact_ref,
                "fileName": TAILORING_FILE,
                "maxBytes": 8 * 1024 * 1024,
            }
        )
    return {
        "imageSourceRef": image_source_ref,
        "endpointAssessment": assessment.model_dump(mode="json", by_alias=True),
        "inputAttachments": attachments,
        "entrypoint": ["python"],
        "command": [
            "-I",
            "-m",
            "moonmind.security.endpoint_assessment_runner",
            assessment.model_dump_json(by_alias=True),
            f"/outputs/{assessment.output_directory}",
        ],
        "networkMode": "none",
        "workdir": "/workspace",
        "resources": {"cpuMillis": 2000, "memoryMiB": 4096, "pids": 256},
        "timeoutSeconds": assessment.timeout_seconds + 15,
        "outputs": assessment.outputs(),
        "caches": [],
    }


def validate_endpoint_assessment_job(spec) -> None:
    """Rechecked wherever a spec is decoded: admission, persistence, worker.

    Every mount, network and environment entry must equal the canonical
    workload, so no request adds a network, daemon socket, host path,
    credential, remediation flag or command the admission did not name.
    """
    assessment = getattr(spec, "endpoint_assessment", None)
    if assessment is None:
        return
    if any(
        getattr(spec, field, None) is not None
        for field in (
            "repository_scan",
            "feed_refresh",
            "workload_profile",
            "image_assessment",
            "tls_assessment",
            "web_assessment",
            "cloud_assessment",
        )
    ):
        raise ValueError("endpoint assessment is its own workload")
    if spec.image_source_ref is None:
        raise ValueError("endpoint assessment uses a deployment-owned scanner image")
    if spec.input_attachments is None:
        raise ValueError("endpoint assessment never mounts the live workspace")
    canonical = build_endpoint_assessment_job(
        assessment, image_source_ref=spec.image_source_ref
    )
    for field, expected in (
        ("entrypoint", canonical["entrypoint"]),
        ("command", canonical["command"]),
        ("network_mode", "none"),
        ("egress_policy_ref", None),
        ("workdir", "/workspace"),
        ("environment", []),
        ("service_ref", None),
        ("registry_credential_ref", None),
        ("timeout_seconds", canonical["timeoutSeconds"]),
    ):
        if getattr(spec, field) != expected:
            raise ValueError("endpoint assessment workload differs from its admission")
    for field, key in (
        ("input_attachments", "inputAttachments"),
        ("outputs", "outputs"),
        ("caches", "caches"),
    ):
        actual = [v.model_dump(by_alias=True) for v in getattr(spec, field)]
        model = type(getattr(spec, field)[0]) if getattr(spec, field) else None
        expected = [
            model.model_validate(item).model_dump(by_alias=True) if model else item
            for item in canonical[key]
        ]
        if actual != expected:
            raise ValueError("endpoint assessment mounts differ from its admission")


# --- Native results -------------------------------------------------------


class NativeRule(Contract):
    rule_id: str = Field(pattern=_RULE_ID.pattern)
    result: XccdfResult
    severity: str | None = None
    idents: Items = Field(default_factory=list, max_length=8)
    messages: Items = Field(default_factory=list, max_length=_MAX_MESSAGES)


class NativeResults(Contract):
    """The allow-listed facts of one XCCDF ``TestResult``."""

    benchmark_id: str = Field(pattern=_BENCHMARK_ID.pattern)
    benchmark_version: Text | None = None
    profile_id: str | None = Field(None, pattern=_PROFILE_ID.pattern)
    test_system: Text | None = None
    start_time: AwareDatetime | None = None
    end_time: AwareDatetime | None = None
    target: Text | None = None
    platforms: Items = Field(default_factory=list, max_length=32)
    rules: list[NativeRule] = Field(default_factory=list, max_length=_MAX_RULES)


def _local(tag) -> str:
    return tag.rpartition("}")[2] if isinstance(tag, str) else ""


def _text(value, pattern=_TOKEN) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and pattern.fullmatch(value) else None


def _free_text(value) -> str | None:
    """Bounded, redacted prose such as an OpenSCAP message or a target name."""
    from moonmind.utils.logging import redact_sensitive_text

    if not isinstance(value, str):
        return None
    value = " ".join(redact_sensitive_text(value).split())[:_MAX_TEXT]
    return value or None


def _time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def test_result_element(body: bytes, limit: int) -> ET.Element:
    """The single XCCDF ``TestResult`` in an ARF, XCCDF results or result document.

    Only UTF-8 without a document type or entity declaration is read, so no
    external entity, entity expansion or network reference is ever resolved.
    """
    if len(body) > limit:
        raise EndpointResultsError("results_exceed_bound")
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise EndpointResultsError("results_malformed")
    try:
        body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EndpointResultsError("results_malformed") from exc
    parser = ET.XMLPullParser(events=("end",))
    found = []
    try:
        for start in range(0, len(body), 1 << 20):
            parser.feed(body[start : start + (1 << 20)])
            for _, element in parser.read_events():
                tag = element.tag
                if tag == f"{{{XCCDF}}}TestResult":
                    found.append(element)
                    if len(found) > 1:
                        raise EndpointResultsError("results_ambiguous")
                elif tag in (
                    f"{{{_ARF}}}report-requests",
                    f"{{{_ARF}}}report-request",
                ) or (
                    isinstance(tag, str)
                    and tag.startswith("{http://oval.mitre.org/XMLSchema/")
                    and _local(tag) in {"oval_results", "results", "system"}
                ):
                    # Copied content and system characteristics are dropped as
                    # soon as they are read; they are never results.
                    element.clear()
        parser.close()
    except (ET.ParseError, ValueError, RecursionError) as exc:
        if isinstance(exc, EndpointResultsError):
            raise
        raise EndpointResultsError("results_malformed") from exc
    if not found:
        raise EndpointResultsError("results_without_test_result")
    return found[0]


def parse_results(body: bytes, limit: int) -> NativeResults:
    """Allow-listed facts of the one ``TestResult``; anything else is ignored."""
    element = test_result_element(body, limit)
    ns = f"{{{XCCDF}}}"
    benchmark = element.find(f"{ns}benchmark")
    profile = element.find(f"{ns}profile")
    target = element.find(f"{ns}target")
    rules: dict[str, dict] = {}
    for row in element.iterfind(f"{ns}rule-result"):
        rule_id = _text(row.get("idref"), _RULE_ID)
        result = (row.findtext(f"{ns}result") or "").strip()
        if rule_id is None or result not in RESULTS:
            raise EndpointResultsError("results_malformed")
        current = rules.get(rule_id)
        if current is None:
            if len(rules) >= _MAX_RULES:
                raise EndpointResultsError("results_exceed_bound")
            current = rules[rule_id] = {
                "ruleId": rule_id,
                "result": result,
                "idents": [],
                "messages": [],
            }
        elif _PRECEDENCE.index(result) < _PRECEDENCE.index(current["result"]):
            current["result"] = result
        severity = row.get("severity")
        if severity in _SEVERITY:
            current["severity"] = severity
        for ident in row.iterfind(f"{ns}ident"):
            value = _text(ident.text)
            if value and value not in current["idents"] and len(current["idents"]) < 8:
                current["idents"].append(value)
        for message in row.iterfind(f"{ns}message"):
            value = _free_text(message.text)
            if (
                value
                and value not in current["messages"]
                and len(current["messages"]) < _MAX_MESSAGES
            ):
                current["messages"].append(value)
    try:
        return NativeResults(
            benchmarkId=_text(
                benchmark.get("id") if benchmark is not None else None,
                _BENCHMARK_ID,
            )
            or "",
            benchmarkVersion=_text(element.get("version")),
            profileId=_text(
                profile.get("idref") if profile is not None else None, _PROFILE_ID
            ),
            testSystem=_text(element.get("test-system")),
            startTime=_time(element.get("start-time")),
            endTime=_time(element.get("end-time")),
            target=_free_text(target.text if target is not None else None),
            platforms=[
                value
                for value in (
                    _text(item.get("idref"))
                    for item in element.iterfind(f"{ns}platform")
                )
                if value
            ][:32],
            rules=list(rules.values()),
        )
    except ValueError as exc:
        raise EndpointResultsError("results_malformed") from exc


def sanitize_results(body: bytes, limit: int, *, keep_target: bool) -> bytes:
    """Rebuild a standalone XCCDF ``TestResult`` from allow-listed facts.

    The copied datastream, OVAL results, system characteristics, target
    addresses and facts are dropped. An evaluated copy's ``target`` names the
    scanner, not the endpoint, so only imports keep the declared target.
    """
    native = parse_results(body, limit)
    # Unqualified tags under one declared default namespace, so the rebuilt
    # document carries no prefix or namespace the source chose.
    root = ET.Element(
        "TestResult",
        {
            key: value
            for key, value in (
                ("xmlns", XCCDF),
                (
                    "id",
                    "xccdf_org.moonmind_testresult_"
                    + (native.profile_id or "default").rpartition("_profile_")[2],
                ),
                ("version", native.benchmark_version),
                ("test-system", native.test_system),
                (
                    "start-time",
                    native.start_time.isoformat() if native.start_time else None,
                ),
                ("end-time", native.end_time.isoformat() if native.end_time else None),
            )
            if value
        },
    )
    ET.SubElement(root, "benchmark", {"id": native.benchmark_id})
    if native.profile_id:
        ET.SubElement(root, "profile", {"idref": native.profile_id})
    if keep_target and native.target:
        ET.SubElement(root, "target").text = native.target
    for platform in native.platforms:
        ET.SubElement(root, "platform", {"idref": platform})
    for rule in native.rules:
        attributes = {"idref": rule.rule_id}
        if rule.severity:
            attributes["severity"] = rule.severity
        row = ET.SubElement(root, "rule-result", attributes)
        ET.SubElement(row, "result").text = rule.result
        for ident in rule.idents:
            ET.SubElement(row, "ident").text = ident
        for message in rule.messages:
            ET.SubElement(row, "message").text = message
    safe = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if len(safe) > limit:
        raise EndpointResultsError("results_exceed_bound")
    return safe


# --- Evidence -------------------------------------------------------------


class EndpointOs(Contract):
    """``/etc/os-release`` of the examined copy, as the copy states it."""

    id: Text | None = None
    version_id: Text | None = None
    pretty_name: Text | None = None


class ExtractionRecord(Contract):
    members: int = Field(ge=0)
    special_files_skipped: int = Field(0, ge=0)
    unsafe_members_refused: int = Field(0, ge=0)
    members_not_extracted: int = Field(0, ge=0)
    layers: int = Field(0, ge=0)


class EndpointExecution(Contract):
    """What the job did, written by the runner and checked by the worker."""

    target: Literal["rootfs", "image", "results"]
    version: str = Field(
        pattern=r"^(?:[0-9]+\.){1,3}[0-9]+(?:[-+][A-Za-z0-9.-]+)?$|^unavailable$|^not_run$"
    )
    exit_code: int | None = None
    state: Literal["succeeded", "failed", "timed_out", "canceled"]
    limitations: list[str] = Field(default_factory=list, max_length=128)
    input_digest: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    os_release: EndpointOs | None = None
    extraction: ExtractionRecord | None = None
    content_file: str | None = Field(None, pattern=r"^ssg-[a-z0-9]{2,32}-ds\.xml$")
    content_digest: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    content_release: Text | None = None
    tailoring_digest: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    # Rules an offline copy cannot answer, by what they need; never imports.
    offline_rules: dict[str, Literal["runtime", "metadata"]] = Field(
        default_factory=dict, max_length=_MAX_RULES
    )


class EndpointTailoring(Contract):
    state: Literal["none", "applied", "not_recorded"]
    digest: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")


class EndpointContent(Contract):
    """The content and profile that interpreted the subject.

    A change here is an intelligence change: the rules changed, not the
    endpoint.
    """

    benchmark_id: Text
    benchmark_version: Text | None = None
    profile_id: Text | None = None
    family: Literal["ComplianceAsCode", "other"]
    aligned_with: Text | None = None
    content_file: Text | None = None
    content_digest: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    content_release: Text | None = None
    tailoring: EndpointTailoring


class EndpointSubject(Contract):
    """The examined subject: the scope asset and the exact bytes examined."""

    asset: Text
    kind: Literal["rootfs", "image", "results"]
    identity: str = Field(
        pattern=r"^(?:rootfs|image-archive|openscap-results)@sha256:[0-9a-f]{64}$"
    )
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    os: EndpointOs | None = None
    declared_target: Text | None = None
    observation: Literal["offline-copy", "operator-supplied"]
    statement: Text


class EndpointRule(Contract):
    rule_id: Text
    result: XccdfResult
    state: Literal["passed", "failed", "not_assessed"]
    reason: Text | None = None
    severity: Text | None = None
    idents: Items = Field(default_factory=list, max_length=8)


class EndpointAssessmentSummary(Contract):
    schema_version: Literal["endpoint-assessment/v1"] = "endpoint-assessment/v1"
    tool: Literal["openscap"] = "openscap"
    admission: EndpointAssessment
    execution: EndpointExecution | None
    subject: EndpointSubject | None
    content: EndpointContent | None
    rules: list[EndpointRule] = Field(default_factory=list, max_length=_MAX_RULES)
    counts: dict[str, int] = Field(default_factory=dict)
    platform: Literal[
        "applicable", "partially_applicable", "not_applicable", "unknown"
    ] = "unknown"
    platforms: Items = Field(default_factory=list, max_length=32)
    observed_at: AwareDatetime | None = None
    native_ref: Text | None = None
    coverage: Literal["complete", "partial"]
    limitations: Items
    unsupported_platforms: Items = Field(
        default_factory=lambda: list(UNSUPPORTED_PLATFORMS)
    )
    content_rights: Text = CONTENT_RIGHTS
    interpretation: Text = INTERPRETATION


def aligned_with(profile_id: str | None) -> str | None:
    name = (profile_id or "").rpartition("_profile_")[2].lower()
    tokens = set(re.split(r"[_.-]", name)) | {name}
    for token, title in _ALIGNMENT:
        if token in tokens or name.startswith(token + "_"):
            return title
    return None


def _identity(kind: str, digest: str) -> str:
    prefix = {
        "rootfs": "rootfs",
        "image": "image-archive",
        "results": "openscap-results",
    }
    return f"{prefix[kind]}@sha256:{digest}"


def summarize(
    assessment: EndpointAssessment,
    execution: EndpointExecution | None,
    native: NativeResults | None,
    *,
    native_ref: str | None,
    limitations: list[str] | tuple[str, ...] = (),
) -> EndpointAssessmentSummary:
    """Typed per-rule outcomes, coverage states and identity, from native facts.

    Pass and fail become findings and coverage. Every other result is a
    coverage state with its reason. Errors, unknown results and required
    rules that were not evaluated leave the run partial.
    """
    limits = set(limitations)
    evaluate = assessment.target != "results"
    if execution is None:
        limits.add("missing_or_invalid_execution_evidence")
    else:
        limits.update(execution.limitations)
        if execution.state != "succeeded":
            limits.add(f"scanner_{execution.state}")
    if native is None:
        limits.add("native_results_unavailable")
    offline = execution.offline_rules if execution and evaluate else {}
    rules = []
    for rule in native.rules if native else ():
        kind = offline.get(rule.rule_id)
        if rule.result in {"pass", "fail"} and kind:
            state, reason = "not_assessed", OFFLINE_REASONS[kind]
        elif rule.result in {"pass", "fail"}:
            state, reason = ("passed" if rule.result == "pass" else "failed"), None
        else:
            state, reason = "not_assessed", REASONS[rule.result]
        rules.append(
            EndpointRule(
                ruleId=rule.rule_id,
                result=rule.result,
                state=state,
                reason=reason,
                severity=rule.severity,
                idents=rule.idents,
            )
        )
    population = [rule for rule in rules if rule.result != "notselected"]
    evaluated = {rule.rule_id for rule in rules if rule.state != "not_assessed"}
    if native is not None:
        if not population:
            limits.add("no_rule_selected")
        elif not evaluated:
            limits.add("no_rule_evaluated")
        if any(rule.result in {"error", "unknown"} for rule in population):
            limits.add("rule_check_errors")
        if any(rule.result == "fixed" for rule in rules):
            limits.add("results_include_remediation")
        if set(assessment.rule_ids) - evaluated:
            limits.add("required_rules_not_evaluated")
    inapplicable = sum(rule.result == "notapplicable" for rule in population)
    platform = (
        "unknown"
        if not population
        else (
            "not_applicable"
            if inapplicable == len(population)
            else "partially_applicable" if inapplicable else "applicable"
        )
    )
    if platform == "not_applicable":
        limits.add("content_not_applicable")
    content = subject = None
    if native is not None:
        if evaluate and native.profile_id != assessment.profile:
            limits.add("profile_differs_from_admission")
        if (
            not evaluate
            and assessment.profile
            and native.profile_id != assessment.profile
        ):
            limits.add("profile_differs_from_admission")
        tailoring = (
            EndpointTailoring(state="not_recorded")
            if not evaluate
            else (
                EndpointTailoring(
                    state="applied",
                    digest=execution.tailoring_digest if execution else None,
                )
                if assessment.tailoring_artifact_ref
                else EndpointTailoring(state="none")
            )
        )
        content = EndpointContent(
            benchmarkId=native.benchmark_id,
            benchmarkVersion=native.benchmark_version,
            profileId=native.profile_id,
            family=(
                "ComplianceAsCode"
                if native.benchmark_id.startswith(_COMPLIANCE_AS_CODE)
                else "other"
            ),
            alignedWith=aligned_with(native.profile_id),
            contentFile=execution.content_file if execution and evaluate else None,
            contentDigest=execution.content_digest if execution and evaluate else None,
            contentRelease=(
                execution.content_release if execution and evaluate else None
            ),
            tailoring=tailoring,
        )
        if evaluate and (
            content.content_digest is None or content.content_file is None
        ):
            limits.add("content_identity_unavailable")
        if evaluate and content.content_file not in (None, assessment.datastream):
            limits.add("content_differs_from_admission")
        if not evaluate:
            limits.add("content_digest_not_recorded")
    if execution is not None and execution.input_digest:
        observation = "offline-copy" if evaluate else "operator-supplied"
        subject = EndpointSubject(
            asset=assessment.asset,
            kind=assessment.target,
            identity=_identity(assessment.target, execution.input_digest),
            inputDigest=execution.input_digest,
            os=execution.os_release if evaluate else None,
            declaredTarget=native.target if native and not evaluate else None,
            observation=observation,
            statement=OBSERVATION_STATEMENTS[observation],
        )
    else:
        limits.add("target_identity_unavailable")
    observed_at = None
    if native is not None and not evaluate:
        # An import is observed as of the operator's own run.
        observed_at = native.end_time or native.start_time
        if observed_at is None:
            limits.add("observation_time_unknown")
    blocking = limits - {"content_digest_not_recorded"}
    return EndpointAssessmentSummary(
        admission=assessment,
        execution=execution,
        subject=subject,
        content=content,
        rules=rules,
        counts={
            result: sum(rule.result == result for rule in rules)
            for result in RESULTS
            if any(rule.result == result for rule in rules)
        },
        platform=platform,
        platforms=native.platforms if native else [],
        observedAt=observed_at,
        nativeRef=native_ref,
        coverage="partial" if blocking else "complete",
        limitations=sorted(limits),
    )


def verify_summary(
    summary: EndpointAssessmentSummary, native_body: bytes
) -> EndpointAssessmentSummary:
    """Recompute a retained summary from its retained native result.

    A summary whose per-rule outcomes, identity or coverage differ from what
    its own native result supports is refused rather than trusted.
    """
    native = parse_results(native_body, summary.admission.max_report_bytes)
    expected = summarize(
        summary.admission,
        summary.execution,
        native,
        native_ref=summary.native_ref,
        limitations=summary.limitations,
    )
    if expected.model_dump(mode="json") != summary.model_dump(mode="json"):
        raise ValueError("endpoint summary differs from its retained native result")
    return summary
