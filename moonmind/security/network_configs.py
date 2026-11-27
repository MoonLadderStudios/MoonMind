"""Assess networks from exported device configurations, offline.

An operator uploads device, firewall and cloud network configuration exports,
and topology or segmentation descriptions, as artifacts of a saved environment.
This module owns what MoonMind does with them without any new network,
credential or target authority:

* sanitization: secret constructs are replaced in place through the shared
  redaction owner, so every sanitized line keeps its original line number;
* the typed, network-none analyzer job, which parses only sanitized copies;
* ``network-config/v1``, the normalized summary of the analyzer's answers, with
  per-file vendor, operating system, version and parse coverage, and explicit
  coverage gaps for anything unsupported or unparsed;
* comparison of the exported rules with an admitted service inventory.

An export is what the operator supplied at a point in time. MoonMind did not
observe the running device, so every result names the export time, the device
and the supplier, and ages out under the evidence freshness rules.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Iterable, Mapping
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field

from moonmind.schemas.repository_scanner_models import (
    Contract,
    NetworkConfigAssessment,
    NetworkExportKind,
    Text,
)
from moonmind.utils.logging import redact_sensitive_text

SUMMARY_VERSION = "network-config/v1"
NATIVE_VERSION = "network-config-native/v1"
IMAGE_SOURCE_REF = "network-config"
#: Finding and coverage identity of the analyzer's deterministic checks.
SOURCE = "network-config"
TOOL = "batfish"
RUNNER_FILE = "runner.py"
FLOWS_FILE = "flows.json"
SNAPSHOT_DIRECTORY = "snapshot"
SNAPSHOT_CONFIGS = "configs"
#: Configuration exports are confidential unless the operator says otherwise.
SENSITIVITY = "confidential"
RESOURCES = {"cpuMillis": 2000, "memoryMiB": 3072, "pids": 512}
#: Startup and teardown the job budget adds to the analysis timeout.
OVERHEAD_SECONDS = 60

#: Deterministic checks the analyzer decides. Intent, any-to-any rules,
#: management exposure, ciphers and default credentials are agent review.
RULES: dict[str, str] = {
    "acl-line-unreachable": (
        "A filter line can never match because earlier lines already match all "
        "of its traffic."
    ),
    "cleartext-protocol-permitted": (
        "An inbound interface filter permits Telnet, FTP or TFTP traffic."
    ),
    "ntp-not-configured": "The device names no NTP time source.",
    "remote-logging-not-configured": "The device sends no logs to a remote collector.",
    "undefined-reference": "The configuration refers to a structure it never defines.",
}
#: The security-review Skill's network procedures, for what tools cannot decide.
#: Each result is a cited ``security-review/v1`` interpretation, never a check.
REVIEW_PROCEDURES: dict[str, str] = {
    "network-segmentation-intent": (
        "Compare the rules with the stated segmentation intent and topology."
    ),
    "network-permissive-rules": (
        "Judge any-to-any, overly broad and shadowed rules against their purpose."
    ),
    "network-management-exposure": (
        "Find who can reach management services and lines, from which networks."
    ),
    "network-logging-time": "Judge logging and time sources: coverage and protection.",
    "network-weak-protocols": "Find weak protocols, ciphers and protocol versions.",
    "network-default-credentials": (
        "Look for signs of default, shared or reversible credentials without "
        "reading any secret value."
    ),
}
#: What a network plan needs from the operator. Planning never invents them.
REQUIRED_INPUTS = (
    "Each export as an artifact of the saved environment: a device or firewall "
    "configuration, a cloud network export, or a topology or segmentation "
    "description.",
    "For each export, its device identity, its export time with a time zone and "
    "who supplied it, stated in Create.",
    "Optionally, destinations (IP address and port) to ask the exported rules "
    "about, such as those an admitted service inventory observes.",
)
#: Formats and major versions whose analyzer coverage MoonMind has shown with
#: weakened and corrected fixtures. Others still parse, as coverage gaps.
QUALIFIED_FORMATS: dict[str, tuple[int, int]] = {"CISCO_IOS": (12, 17)}
FORMAT_NAMES: dict[str, tuple[str, str]] = {
    "CISCO_IOS": ("Cisco", "IOS or IOS XE"),
    "CISCO_IOS_XR": ("Cisco", "IOS XR"),
    "CISCO_NX": ("Cisco", "NX-OS"),
    "CISCO_ASA": ("Cisco", "ASA"),
    "ARISTA": ("Arista", "EOS"),
    "JUNIPER": ("Juniper", "Junos"),
    "JUNIPER_SWITCH": ("Juniper", "Junos"),
    "FLAT_JUNIPER": ("Juniper", "Junos"),
    "FORTIOS": ("Fortinet", "FortiOS"),
    "PALO_ALTO": ("Palo Alto Networks", "PAN-OS"),
    "PALO_ALTO_NESTED": ("Palo Alto Networks", "PAN-OS"),
    "F5_BIGIP_STRUCTURED": ("F5", "BIG-IP"),
    "CHECK_POINT_GATEWAY": ("Check Point", "Gaia"),
    "CUMULUS_CONCATENATED": ("NVIDIA", "Cumulus Linux"),
}
_VERSIONS: dict[str, re.Pattern] = {
    "CISCO_IOS": re.compile(r"^version\s+(\d+(?:\.\d+)+)\s*$", re.M),
    "CISCO_NX": re.compile(r"^version\s+(\S+)", re.M),
    "CISCO_ASA": re.compile(r"^(?:ASA|PIX) Version\s+(\S+)", re.M),
    "ARISTA": re.compile(r"\bEOS-(\d[\w.]*)"),
    "JUNIPER": re.compile(r"^\s*(?:set\s+)?version\s+\"?([\w.-]+)", re.M),
    "FLAT_JUNIPER": re.compile(r"^\s*set\s+version\s+\"?([\w.-]+)", re.M),
    "FORTIOS": re.compile(r"#config-version=[^-\s]+-(\d+\.\d+\.\d+)"),
    "PALO_ALTO": re.compile(r'<config\s+version="([^"]+)"'),
}
#: What replaces a secret value in a sanitized export. A plain token, because
#: analyzers parse it: a bracketed marker turns some lines into parse gaps.
REDACTION_TOKEN = "MOONMIND-REDACTED"
_MARKER = re.compile(r"\[REDACTED[A-Z_]*\]")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.I)
_PRIVATE_KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----", re.I)

GapCode = Literal[
    "not_analyzed",
    "undecodable",
    "input_too_large",
    "empty",
    "unsupported_format",
    "parse_failed",
    "unparsed_lines",
    "format_not_qualified",
    "version_unidentified",
    "version_not_qualified",
    "question_failed",
    "analyzer_unavailable",
]
PROVENANCE = (
    "Each result describes the export the operator supplied, as of its stated "
    "export time. MoonMind did not observe the running device; a device changed "
    "since its export, or an export from someone other than its stated supplier, "
    "is outside this evidence."
)
QUALIFICATION = (
    "Analyzer coverage is qualified on weakened and corrected Cisco IOS fixtures "
    "only. Qualification on real device exports is Unverified: run this job over "
    "operator-supplied exports and review each file's parse coverage and gaps."
)
INTERPRETATION = (
    "Deterministic analyzer checks of exported configuration. A missing finding "
    "is not evidence for lines the analyzer did not parse, and intent, any-to-any "
    "rules, management exposure, ciphers and credentials need agent review."
)


# --- Sanitization and the job ----------------------------------------------


def sanitize_export(
    raw: bytes, *, max_bytes: int
) -> tuple[bytes | None, str | None, list[int]]:
    """``(sanitized bytes, disposition, redacted line numbers)`` for one export.

    Each line is redacted on its own and private-key blocks are masked line by
    line, so the sanitized copy has the original's line count and locators.
    """
    if len(raw) > max_bytes:
        return None, "input_too_large", []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "undecodable", []
    if not text.strip():
        return None, "empty", []
    # Only a newline ends a line, as analyzers number them; form feeds and
    # Unicode separators stay inside their line.
    lines, redacted, in_key = [], [], False
    for number, line in enumerate(text.split("\n"), start=1):
        body = line.removesuffix("\r")
        ending = line[len(body) :]
        if in_key or _PRIVATE_KEY.search(body):
            closing = _PRIVATE_KEY_END.search(body) is not None
            in_key = not closing
            clean = REDACTION_TOKEN
        else:
            clean = redact_sensitive_text(body)
        if clean != body:
            redacted.append(number)
            clean = _MARKER.sub(REDACTION_TOKEN, clean)
        lines.append(clean + ending)
    return "\n".join(lines).encode("utf-8"), None, redacted


def line_count(text: str) -> int:
    """Newline-terminated lines, as locators and analyzers count them."""
    return text.count("\n") + (0 if not text or text.endswith("\n") else 1)


class PreparedExports(Contract):
    """Sanitized exports and why any export is not analyzer input."""

    sanitized: dict[str, bytes] = Field(default_factory=dict)
    dispositions: dict[str, str] = Field(default_factory=dict)
    redacted_lines: dict[str, list[int]] = Field(default_factory=dict)


def prepare_exports(
    settings: NetworkConfigAssessment, raw: Mapping[str, bytes | None]
) -> PreparedExports:
    """Sanitize every captured export; only device configurations are parsed."""
    prepared = PreparedExports()
    for export in settings.exports:
        body = raw.get(export.file_name)
        if body is None:
            prepared.dispositions[export.file_name] = "input_too_large"
            continue
        clean, disposition, lines = sanitize_export(
            body, max_bytes=settings.max_input_bytes
        )
        if clean is not None:
            prepared.sanitized[export.file_name] = clean
            prepared.redacted_lines[export.file_name] = lines
        if disposition is not None:
            prepared.dispositions[export.file_name] = disposition
        elif export.kind != "device_config":
            prepared.dispositions[export.file_name] = "not_analyzed"
    return prepared


def snapshot_files(
    settings: NetworkConfigAssessment, prepared: PreparedExports
) -> dict[str, bytes]:
    """Relative paths and bytes the worker writes into the job's output."""
    root = settings.output_directory
    files = {
        f"{root}/{RUNNER_FILE}": runner_source(),
        f"{root}/{FLOWS_FILE}": json.dumps(
            [flow.model_dump(mode="json") for flow in settings.flows]
        ).encode(),
    }
    for export in settings.exports:
        if export.file_name in prepared.dispositions:
            continue
        files[f"{root}/{SNAPSHOT_DIRECTORY}/{SNAPSHOT_CONFIGS}/{export.file_name}"] = (
            prepared.sanitized[export.file_name]
        )
    return files


def runner_source() -> bytes:
    from moonmind.security import network_config_runner

    return Path(network_config_runner.__file__).read_bytes()


def input_name(index: int) -> str:
    return f"network-export-{index}"


def build_network_config_job(
    settings: NetworkConfigAssessment, *, image_source_ref: str = IMAGE_SOURCE_REF
) -> dict:
    """The one job spec for an admitted analysis: no network, no workspace.

    Exports are captured read-only inputs. The worker writes the sanitized
    snapshot and the runner into the job's own output directory; the analyzer
    reads only those.
    """
    root = f"/outputs/{settings.output_directory}"
    return {
        "imageSourceRef": image_source_ref,
        "networkConfigAssessment": settings.model_dump(mode="json", by_alias=True),
        "inputAttachments": [
            {
                "name": input_name(index),
                "artifactRef": export.artifact_ref,
                "fileName": "export",
                "maxBytes": settings.max_input_bytes,
            }
            for index, export in enumerate(settings.exports)
        ],
        "entrypoint": ["python3"],
        "command": [
            "-I",
            f"{root}/{RUNNER_FILE}",
            f"{root}/{SNAPSHOT_DIRECTORY}",
            f"{root}/{FLOWS_FILE}",
            f"{root}/native.json",
        ],
        "networkMode": "none",
        "workdir": "/workspace",
        "resources": dict(RESOURCES),
        "timeoutSeconds": settings.timeout_seconds + OVERHEAD_SECONDS,
        "outputs": settings.outputs(),
    }


def validate_network_config_job(spec) -> None:
    """Refuse any change to the rendered analyzer job."""
    settings = getattr(spec, "network_config_assessment", None)
    if settings is None:
        return
    if any(
        getattr(spec, field, None) is not None
        for field in (
            "repository_scan",
            "feed_refresh",
            "image_assessment",
            "tls_assessment",
            "web_assessment",
            "cloud_assessment",
            "workload_profile",
        )
    ):
        raise ValueError("network configuration analysis is its own workload")
    if not spec.image_source_ref or spec.input_attachments is None:
        raise ValueError(
            "network configuration analysis requires a deployment image and "
            "isolated inputs"
        )
    canonical = build_network_config_job(
        settings, image_source_ref=spec.image_source_ref
    )
    for field, key in (
        ("command", "command"),
        ("entrypoint", "entrypoint"),
        ("network_mode", "networkMode"),
        ("timeout_seconds", "timeoutSeconds"),
        ("workdir", "workdir"),
    ):
        if getattr(spec, field) != canonical[key]:
            raise ValueError("network configuration job differs from its admission")
    if spec.resources != type(spec.resources).model_validate(canonical["resources"]):
        raise ValueError("network configuration resource bounds differ from admission")
    if (
        spec.environment
        or spec.caches
        or spec.service_ref
        or spec.registry_credential_ref
        or spec.egress_policy_ref
    ):
        raise ValueError(
            "network configuration analysis admits no environment, mounts or network"
        )
    for field, key in (
        ("input_attachments", "inputAttachments"),
        ("outputs", "outputs"),
    ):
        items = getattr(spec, field)
        model = type(items[0]) if items else None
        expected = [
            model.model_validate(value).model_dump(by_alias=True) if model else value
            for value in canonical[key]
        ]
        if [item.model_dump(by_alias=True) for item in items] != expected:
            raise ValueError("network configuration inputs differ from admission")


# --- The normalized summary --------------------------------------------------

Lines = Annotated[list[Annotated[int, Field(ge=1)]], Field(max_length=256)]


class NetworkGap(Contract):
    """Something the analyzer did not cover. Never read as a clean result."""

    code: GapCode
    file: str | None = Field(default=None, exclude_if=lambda value: value is None)
    lines: Lines = Field(default_factory=list, exclude_if=lambda value: not value)
    detail: Text


class NetworkFinding(Contract):
    rule_id: str
    file: str
    artifact_ref: str
    node: str | None = Field(default=None, exclude_if=lambda value: value is None)
    lines: Lines = Field(default_factory=list)
    detail: Text
    native_id: Text


class NetworkFile(Contract):
    """One export: its provenance, identification and parse coverage."""

    file_name: str
    artifact_ref: str
    device: Text
    exported_at: AwareDatetime
    expires_at: AwareDatetime
    supplier: Text
    kind: NetworkExportKind
    sha256: str | None = None
    sanitized_ref: str | None = None
    line_count: int = Field(default=0, ge=0)
    redacted_lines: Annotated[list[int], Field(max_length=65536)] = Field(
        default_factory=list
    )
    analyzer_format: str | None = None
    vendor: str | None = None
    os: str | None = None
    version: str | None = None
    parse_status: str | None = None
    nodes: Annotated[list[str], Field(max_length=64)] = Field(default_factory=list)
    coverage: Literal["complete", "partial", "not_analyzed"]


class FlowDecision(Contract):
    node: str
    filter: str
    action: Literal["PERMIT", "DENY", "MIXED"]
    file: str | None = None
    lines: Lines = Field(default_factory=list)


class FlowResult(Contract):
    host: str
    port: int
    protocol: Literal["tcp", "udp"]
    status: Literal["evaluated", "failed"]
    decisions: Annotated[list[FlowDecision], Field(max_length=256)] = Field(
        default_factory=list
    )


class NetworkConfigSummary(Contract):
    schema_version: Literal["network-config/v1"] = SUMMARY_VERSION
    settings: NetworkConfigAssessment
    status: Literal["complete", "partial", "failed"]
    native_ref: str
    analyzer: dict[str, str] = Field(default_factory=dict)
    checks: list[str] = Field(default_factory=list)
    files: list[NetworkFile]
    findings: Annotated[list[NetworkFinding], Field(max_length=4096)] = Field(
        default_factory=list
    )
    gaps: Annotated[list[NetworkGap], Field(max_length=4096)] = Field(
        default_factory=list
    )
    flows: list[FlowResult] = Field(default_factory=list)
    sensitivity: Literal["confidential"] = SENSITIVITY
    provenance: Text = PROVENANCE
    qualification: Text = QUALIFICATION
    interpretation: Text = INTERPRETATION
    comparison_key: str = Field(pattern=r"^[0-9a-f]{64}$")

    def file(self, name: str) -> NetworkFile | None:
        return next((row for row in self.files if row.file_name == name), None)


_SOURCE_LINES = re.compile(r"^configs/(?P<file>[^:]+):\[(?P<lines>[0-9, -]*)\]$")
_GAP_DETAIL = {
    "not_analyzed": (
        "Kept and sanitized for agent review; no qualified analyzer reads "
        "this kind of export."
    ),
    "undecodable": "The export is not UTF-8 text; nothing in it was analyzed.",
    "input_too_large": "The export exceeds the admitted input size and was not analyzed.",
    "empty": "The export is empty.",
    "unsupported_format": (
        "The analyzer does not recognize this vendor or format; nothing in it "
        "was analyzed."
    ),
    "parse_failed": "The analyzer could not parse this export.",
    "unparsed_lines": "The analyzer did not recognize these lines; they were not checked.",
    "format_not_qualified": (
        "MoonMind has not qualified analyzer coverage of this format with fixtures; "
        "its results are partial."
    ),
    "version_unidentified": (
        "The operating system version could not be identified, so its analyzer "
        "coverage is not established."
    ),
    "version_not_qualified": (
        "This operating system version is outside the qualified range, so its "
        "analyzer coverage is not established."
    ),
}
_RULE_QUESTIONS = {
    "acl-line-unreachable": ("filterLineReachability", "definedStructures"),
    "cleartext-protocol-permitted": (
        "searchFilters:telnet",
        "searchFilters:ftp",
        "searchFilters:tftp",
        "definedStructures",
    ),
    "ntp-not-configured": ("nodeProperties",),
    "remote-logging-not-configured": ("nodeProperties",),
    "undefined-reference": ("undefinedReferences",),
}


def _config_file(value: Any) -> str | None:
    text = str(value or "")
    return text.removeprefix("configs/") if text.startswith("configs/") else None


def _source_lines(value: Any) -> tuple[str | None, list[int]]:
    match = _SOURCE_LINES.match(str(value or ""))
    if match is None:
        return None, []
    lines = []
    for part in match["lines"].split(","):
        part = part.strip()
        if "-" in part:
            start, _, end = part.partition("-")
            if start.isdigit() and end.isdigit():
                lines.extend(range(int(start), min(int(end), int(start) + 255) + 1))
        elif part.isdigit():
            lines.append(int(part))
    return match["file"], sorted(set(lines))[:256]


def _safe(value: Any) -> str:
    text = redact_sensitive_text(str(value if value is not None else ""))
    return text.replace("\n", " ")[:900] or "-"


def _identify(
    file_format: str | None, text: str
) -> tuple[str | None, str | None, str | None]:
    vendor, os_name = FORMAT_NAMES.get(file_format or "", (None, None))
    pattern = _VERSIONS.get(file_format or "")
    found = pattern.search(text) if pattern else None
    return vendor, os_name, found.group(1) if found else None


def _major(version: str) -> int | None:
    head = re.match(r"(\d+)", version)
    return int(head.group(1)) if head else None


def summarize_network_config(
    settings: NetworkConfigAssessment,
    prepared: PreparedExports,
    native: Mapping[str, Any] | None,
    *,
    native_ref: str,
    sanitized_refs: Mapping[str, str],
) -> NetworkConfigSummary:
    """Normalize the analyzer's answers over the sanitized exports.

    Deterministic and recomputable from retained evidence: the sanitized
    copies, the native answers and the admitted settings. Anything the analyzer
    did not answer, parse or qualify is a named gap.
    """
    gaps: list[NetworkGap] = []
    answers = None
    if (
        isinstance(native, Mapping)
        and native.get("schemaVersion") == NATIVE_VERSION
        and not native.get("error")
        and isinstance(native.get("answers"), Mapping)
    ):
        answers = native["answers"]
    else:
        gaps.append(
            NetworkGap(
                code="analyzer_unavailable",
                detail="The analyzer produced no answers; no deterministic check ran.",
            )
        )
    failed_questions: set[str] = set()

    def rows(question: str) -> list[Mapping]:
        if answers is None:
            return []
        value = answers.get(question)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, Mapping)]
        if question not in failed_questions:
            failed_questions.add(question)
            gaps.append(
                NetworkGap(
                    code="question_failed",
                    detail=f"The analyzer question {question} returned no answer.",
                )
            )
        return []

    parse = {}
    for row in rows("fileParseStatus"):
        name = _config_file(row.get("File_Name"))
        if name:
            parse[name] = row
    unparsed: dict[str, set[int]] = {}
    for row in rows("parseWarning"):
        name = _config_file(row.get("Filename"))
        if name and isinstance(row.get("Line"), int):
            unparsed.setdefault(name, set()).add(row["Line"])
    defined: dict[tuple[str, str], list[int]] = {}
    for row in rows("definedStructures"):
        name, lines = _source_lines(row.get("Source_Lines"))
        if name:
            defined[(name, str(row.get("Structure_Name")))] = lines
    node_file: dict[str, str] = {}
    files: list[NetworkFile] = []
    analyzed: set[str] = set()
    for export in settings.exports:
        name = export.file_name
        text = prepared.sanitized.get(name, b"").decode("utf-8", "replace")
        row = {
            "file_name": name,
            "artifact_ref": export.artifact_ref,
            "device": export.device,
            "exported_at": export.exported_at,
            "expires_at": export.exported_at
            + timedelta(days=settings.max_export_age_days),
            "supplier": export.supplier,
            "kind": export.kind,
            "redacted_lines": prepared.redacted_lines.get(name, []),
            "coverage": "not_analyzed",
        }
        if name in prepared.sanitized:
            body = prepared.sanitized[name]
            row.update(
                sha256=sha256(body).hexdigest(),
                sanitized_ref=sanitized_refs.get(name),
                line_count=line_count(text),
            )
        disposition = prepared.dispositions.get(name)
        status = parse.get(name)
        if disposition is not None:
            detail = _GAP_DETAIL[disposition]
            if disposition == "not_analyzed":
                detail = f"A {export.kind.replace('_', ' ')} export. {detail}"
            gaps.append(NetworkGap(code=disposition, file=name, detail=detail))
        elif answers is None:
            pass
        elif status is None:
            gaps.append(
                NetworkGap(
                    code="parse_failed",
                    file=name,
                    detail="The analyzer reported no parse status for this export.",
                )
            )
        else:
            file_format = str(status.get("File_Format") or "UNKNOWN")
            parse_status = str(status.get("Status") or "UNKNOWN")
            nodes = [str(node) for node in status.get("Nodes") or []][:64]
            vendor, os_name, version = _identify(file_format, text)
            row.update(
                analyzer_format=file_format,
                parse_status=parse_status,
                nodes=nodes,
                vendor=vendor,
                os=os_name,
                version=version,
            )
            if file_format == "UNKNOWN" or parse_status in {"UNKNOWN", "UNSUPPORTED"}:
                gaps.append(
                    NetworkGap(
                        code="unsupported_format",
                        file=name,
                        detail=_GAP_DETAIL["unsupported_format"],
                    )
                )
            elif parse_status not in {"PASSED", "PARTIALLY_UNRECOGNIZED"}:
                gaps.append(
                    NetworkGap(
                        code="parse_failed",
                        file=name,
                        detail=f"{_GAP_DETAIL['parse_failed']} Status: {_safe(parse_status)}.",
                    )
                )
            else:
                analyzed.add(name)
                for node in nodes:
                    node_file.setdefault(node, name)
                complete = parse_status == "PASSED"
                if parse_status == "PARTIALLY_UNRECOGNIZED" or unparsed.get(name):
                    complete = False
                    gaps.append(
                        NetworkGap(
                            code="unparsed_lines",
                            file=name,
                            lines=sorted(unparsed.get(name, set()))[:256],
                            detail=_GAP_DETAIL["unparsed_lines"],
                        )
                    )
                qualified = QUALIFIED_FORMATS.get(file_format)
                code = None
                if qualified is None:
                    code = "format_not_qualified"
                elif version is None:
                    code = "version_unidentified"
                elif not (qualified[0] <= (_major(version) or -1) <= qualified[1]):
                    code = "version_not_qualified"
                if code is not None:
                    complete = False
                    gaps.append(
                        NetworkGap(code=code, file=name, detail=_GAP_DETAIL[code])
                    )
                row["coverage"] = "complete" if complete else "partial"
        files.append(NetworkFile.model_validate(row))
    refs = {export.file_name: export.artifact_ref for export in settings.exports}
    findings: dict[str, NetworkFinding] = {}

    def add(rule: str, node: str | None, name: str | None, lines, detail, key):
        if name is None or name not in analyzed:
            return
        native_id = "|".join([rule, *(str(part) for part in key)])
        findings.setdefault(
            native_id,
            NetworkFinding(
                rule_id=rule,
                file=name,
                artifact_ref=refs[name],
                node=node,
                lines=lines,
                detail=_safe(detail),
                native_id=native_id[:1024],
            ),
        )

    for row in rows("filterLineReachability"):
        line = str(row.get("Unreachable_Line") or "")
        blocking = ", ".join(str(item) for item in row.get("Blocking_Lines") or [])
        for source in row.get("Sources") or []:
            node, _, filter_name = str(source).partition(": ")
            name = node_file.get(node)
            add(
                "acl-line-unreachable",
                node,
                name,
                defined.get((name or "", f"{filter_name}: {line}"), []),
                f"{filter_name}: '{line}' never matches"
                + (
                    f"; earlier lines match all of it: {blocking}." if blocking else "."
                ),
                (node, filter_name, line),
            )
    for protocol in ("telnet", "ftp", "tftp"):
        for row in rows(f"searchFilters:{protocol}"):
            node = str(row.get("Node") or "")
            filter_name = str(row.get("Filter_Name") or "")
            line = str(row.get("Line_Content") or "")
            name = node_file.get(node)
            add(
                "cleartext-protocol-permitted",
                node,
                name,
                defined.get((name or "", f"{filter_name}: {line}"), []),
                f"Inbound filter {filter_name} permits {protocol} at '{line}'.",
                (node, filter_name, protocol),
            )
    for row in rows("nodeProperties"):
        node = str(row.get("Node") or "")
        name = node_file.get(node)
        if not row.get("NTP_Servers"):
            add(
                "ntp-not-configured",
                node,
                name,
                [],
                f"{node} names no NTP server.",
                (node,),
            )
        if not row.get("Logging_Servers"):
            add(
                "remote-logging-not-configured",
                node,
                name,
                [],
                f"{node} names no remote logging host.",
                (node,),
            )
    for row in rows("undefinedReferences"):
        name, lines = _source_lines(row.get("Lines"))
        name = name or _config_file(row.get("File_Name"))
        reference = str(row.get("Ref_Name") or "")
        kind = str(row.get("Struct_Type") or "structure")
        context = str(row.get("Context") or "")
        add(
            "undefined-reference",
            None,
            name,
            lines,
            f"Refers to undefined {kind} {reference} ({context}).",
            (name, kind, reference, context),
        )
    flows = []
    evaluated = {}
    if answers is not None:
        for item in native.get("flows") or []:
            if isinstance(item, Mapping) and isinstance(item.get("flow"), Mapping):
                flow = item["flow"]
                evaluated[
                    (str(flow.get("host")), flow.get("port"), str(flow.get("protocol")))
                ] = item.get("testFilters")
    for flow in settings.flows:
        result = evaluated.get((flow.host, flow.port, flow.protocol))
        if not isinstance(result, list):
            flows.append(
                FlowResult(
                    host=flow.host,
                    port=flow.port,
                    protocol=flow.protocol,
                    status="failed",
                )
            )
            if answers is not None:
                gaps.append(
                    NetworkGap(
                        code="question_failed",
                        detail=(
                            f"The exported rules were not evaluated for "
                            f"{flow.protocol}/{flow.port} to {flow.host}."
                        ),
                    )
                )
            continue
        grouped: dict[tuple[str, str], set[str]] = {}
        lines_for: dict[tuple[str, str], list[int]] = {}
        for row in result:
            if not isinstance(row, Mapping):
                continue
            key = (str(row.get("Node") or ""), str(row.get("Filter_Name") or ""))
            grouped.setdefault(key, set()).add(str(row.get("Action") or "").upper())
            name = node_file.get(key[0])
            lines_for.setdefault(key, []).extend(
                defined.get((name or "", f"{key[1]}: {row.get('Line_Content')}"), [])
            )
        decisions = [
            FlowDecision(
                node=node,
                filter=filter_name,
                action=(
                    next(iter(actions))
                    if len(actions) == 1 and actions <= {"PERMIT", "DENY"}
                    else "MIXED"
                ),
                file=node_file.get(node),
                lines=sorted(set(lines_for[(node, filter_name)]))[:256],
            )
            for (node, filter_name), actions in sorted(grouped.items())
        ]
        flows.append(
            FlowResult(
                host=flow.host,
                port=flow.port,
                protocol=flow.protocol,
                status="evaluated",
                decisions=decisions,
            )
        )
    checks = [
        rule
        for rule, questions in _RULE_QUESTIONS.items()
        if answers is not None and not set(questions) & failed_questions
    ]
    device_files = [row for row in files if row.kind == "device_config"]
    if answers is None:
        status = "failed"
    elif any(gap.code != "not_analyzed" for gap in gaps) or any(
        row.coverage != "complete" for row in device_files
    ):
        status = "partial"
    else:
        status = "complete"
    analyzer = {"name": TOOL}
    if isinstance(native, Mapping) and isinstance(native.get("analyzer"), Mapping):
        versions = native["analyzer"].get("versions")
        if isinstance(versions, Mapping) and versions.get("Batfish"):
            analyzer["version"] = _safe(versions["Batfish"])[:128]
    key = sha256(
        json.dumps(
            {
                "settings": settings.model_dump(mode="json", by_alias=True),
                "analyzer": analyzer,
                "checks": checks,
                "files": [row.sha256 for row in files],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return NetworkConfigSummary(
        settings=settings,
        status=status,
        native_ref=native_ref,
        analyzer=analyzer,
        checks=checks,
        files=files,
        findings=sorted(
            findings.values(), key=lambda f: (f.file, f.rule_id, f.native_id)
        ),
        gaps=gaps,
        flows=flows,
        comparison_key=key,
    )


# --- Composition with admitted live evidence -------------------------------


def _endpoint(authority: str) -> tuple[str, int] | None:
    host, _, port = authority.rpartition(":")
    host = host.strip("[]")
    try:
        return str(ipaddress.ip_address(host)), int(port)
    except ValueError:
        return None


def compare_with_inventory(network: NetworkConfigSummary, inventory) -> list[dict]:
    """Where a retained service inventory agrees or disagrees with the exports.

    Compares only destinations the plan asked the exported rules about. A port
    the inbound filters deny but the inventory found open is a contradiction;
    the inventory's vantage point and the export time can both explain it, so
    neither source wins and the affected evidence stays inconclusive.
    """
    flows = {
        (flow.host, flow.port): flow
        for flow in network.flows
        if flow.protocol == "tcp" and flow.status == "evaluated"
    }
    comparisons = []
    for endpoint in inventory.endpoints:
        parsed = _endpoint(endpoint.authority)
        flow = flows.get(parsed) if parsed else None
        if flow is None or endpoint.reachability not in {"open", "refused"}:
            continue
        actions = {decision.action for decision in flow.decisions}
        filters = ", ".join(f"{d.node} {d.filter}" for d in flow.decisions) or "none"
        files = sorted({d.file for d in flow.decisions if d.file})
        if not flow.decisions:
            relation = "not_comparable"
            detail = (
                "No inbound interface filter in the exports evaluates this destination."
            )
        elif endpoint.reachability == "open" and actions == {"DENY"}:
            relation = "contradiction"
            detail = (
                f"The exported rules deny tcp/{flow.port} to {flow.host} ({filters}), "
                "but the service inventory found it open."
            )
        elif endpoint.reachability == "open" and "PERMIT" in actions:
            relation = "corroboration"
            detail = f"The exported rules permit it ({filters}) and the inventory found it open."
        elif endpoint.reachability == "refused" and actions == {"DENY"}:
            relation = "corroboration"
            detail = f"The exported rules deny it ({filters}) and the inventory found it closed."
        else:
            relation = "not_comparable"
            detail = (
                f"The exported rules permit it ({filters}); the inventory found no "
                "listening service, which neither confirms nor contradicts the rules."
            )
        comparisons.append(
            {
                "endpoint": f"{flow.host}:{flow.port}",
                "relation": relation,
                "detail": detail,
                "files": files,
                "artifactRefs": sorted(
                    {
                        row.artifact_ref
                        for row in network.files
                        if row.file_name in files
                    }
                ),
            }
        )
    return comparisons


# --- Agent review over exports ----------------------------------------------

_LOCATOR = re.compile(
    r"^(?P<file>[A-Za-z0-9][A-Za-z0-9._-]{0,127}):(?P<start>[1-9][0-9]*)"
    r"(?:-(?P<end>[1-9][0-9]*))?$"
)


def review_locator_errors(
    review, summaries: Iterable[NetworkConfigSummary]
) -> list[str]:
    """An interpretation of an export cites its sanitized file and line range.

    Interpretations stay agent interpretations: they are never deterministic
    findings and cannot satisfy a requirement that needs a check or a record.
    """
    exports = {row.artifact_ref: row for summary in summaries for row in summary.files}
    errors = []
    for index, item in enumerate(review.interpretations):
        export = exports.get(item.subject.asset)
        if export is None:
            continue
        label = f"interpretations[{index}]"
        match = _LOCATOR.fullmatch(item.subject.locator)
        if match is None or match["file"] != export.file_name:
            errors.append(f"{label}.subject.locator: cite <file>:<line> of the export")
            continue
        start = int(match["start"])
        end = int(match["end"] or start)
        if end < start or end > export.line_count:
            errors.append(f"{label}.subject.locator: outside the export's lines")
        if (
            export.sanitized_ref is None
            or export.sanitized_ref not in item.evidence_refs
        ):
            errors.append(f"{label}.evidenceRefs: cite the sanitized export")
    return errors


# --- Retained-report verification -------------------------------------------

#: File-level gaps recorded when an export was never analyzer input.
DISPOSITIONS = frozenset({"not_analyzed", "undecodable", "input_too_large", "empty"})


def verify_retained_summary(
    summary: NetworkConfigSummary,
    native: Mapping[str, Any] | None,
    sanitized: Mapping[str, bytes],
) -> NetworkConfigSummary:
    """Recompute a retained summary from its retained evidence, or refuse it.

    Findings, coverage and gaps come from the sanitized exports and the native
    answers the summary cites, never from the summary's own assertions.
    """
    prepared = PreparedExports(
        sanitized=dict(sanitized),
        dispositions={
            gap.file: gap.code
            for gap in summary.gaps
            if gap.file and gap.code in DISPOSITIONS
        },
        redacted_lines={row.file_name: row.redacted_lines for row in summary.files},
    )
    normalized = summarize_network_config(
        summary.settings,
        prepared,
        native,
        native_ref=summary.native_ref,
        sanitized_refs={
            row.file_name: row.sanitized_ref
            for row in summary.files
            if row.sanitized_ref
        },
    )
    if normalized != summary:
        raise ValueError("network configuration summary differs from its evidence")
    return normalized


def report_files(summary: NetworkConfigSummary) -> list[dict]:
    """Per-export provenance, identification and coverage for a report."""
    return [
        {
            "fileName": row.file_name,
            "artifactRef": row.artifact_ref,
            "sanitizedRef": row.sanitized_ref,
            "lineCount": row.line_count,
            "device": row.device,
            "exportedAt": row.exported_at.isoformat(),
            "expiresAt": row.expires_at.isoformat(),
            "supplier": row.supplier,
            "kind": row.kind,
            "vendor": row.vendor,
            "os": row.os,
            "version": row.version,
            "parseStatus": row.parse_status,
            "coverage": row.coverage,
            "redactedLines": len(row.redacted_lines),
        }
        for row in summary.files
    ]
