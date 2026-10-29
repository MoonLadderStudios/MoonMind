"""Read-only Prowler cloud-posture assessments on the ContainerJob contracts.

The upstream Prowler CLI image runs one argv rendered here from an admitted
:class:`CloudAssessment`, never from caller text, using the image's own
entrypoint. The argv selects the provider, regions and checks or services,
writes native JSON-OCSF and an error log into the job's outputs, and never
names remediation, export or notification options. The worker delivers an
issued read-only session for the admitted account as an AWS credentials file;
ambient worker credentials and instance metadata are unreachable.

Prowler omits results it could not collect and logs why, and reports some
denied evaluations as ``MANUAL``. Coverage therefore comes from both files:
a denied, throttled, unreachable or failed collection, a manual result, an
unadmitted account or an unfinished run makes the result incomplete, never
clean.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Literal, Sequence

from pydantic import Field, model_validator

from moonmind.schemas.repository_scanner_models import (
    CLOUD_ACTION_CLASS,
    CLOUD_OUTPUT_FILES,
    CloudAssessment,
)
from moonmind.security.assessments import Contract, Finding, Items, Text
from moonmind.security.repository_scanners import NativeResult
from moonmind.security.workload_credentials import READ_ONLY_POLICY_ARNS

#: Deployment image source; ``prowlercloud/prowler:stable`` by default.
CLOUD_IMAGE_SOURCE_REF = "cloud-posture"
REPORT_NAME = "prowler"
#: Prowler options that change the account, send findings elsewhere or
#: exercise discovered secrets. The rendered argv never contains them.
UNADMITTED_OPTIONS = frozenset(
    {
        "--fixer",
        "--fixer-config",
        "--security-hub",
        "-S",
        "--send-sh-only-fails",
        "--skip-sh-update",
        "--output-bucket",
        "-B",
        "--output-bucket-no-assume",
        "-D",
        "--push-to-cloud",
        "--slack",
        "--shodan",
        "-N",
        "--scan-secrets-validate",
        "--role",
        "-R",
        "--profile",
        "-p",
    }
)
_AWS_API_HOST = re.compile(r"^(?:[a-z0-9-]+\.)+amazonaws\.com$")


def prowler_argv(assessment: CloudAssessment) -> list[str]:
    out = f"/outputs/{assessment.output_directory}"
    selection = (
        ["--check", *assessment.checks]
        if assessment.checks
        else ["--service", *assessment.services]
    )
    return [
        assessment.provider,
        "--region",
        *assessment.regions,
        *selection,
        "--output-formats",
        "json-ocsf",
        "--output-directory",
        out,
        "--output-filename",
        REPORT_NAME,
        "--log-level",
        "ERROR",
        "--log-file",
        f"{out}/{CLOUD_OUTPUT_FILES['log']}",
        "--aws-retries-max-attempts",
        str(assessment.max_retry_attempts),
        "--no-banner",
        "--no-color",
        # Failed checks are findings, not a failed run.
        "--ignore-exit-code-3",
    ]


def build_cloud_assessment_job(
    assessment: CloudAssessment, *, image_source_ref: str = CLOUD_IMAGE_SOURCE_REF
) -> dict:
    """Workload only; admission stamps the workspace and scope authority."""
    return {
        "imageSourceRef": image_source_ref,
        "cloudAssessment": assessment.model_dump(mode="json", by_alias=True),
        # Job-owned storage holds outputs and the session; no live workspace.
        "inputAttachments": [],
        "command": prowler_argv(assessment),
        "networkMode": "bridge",
        "egressPolicyRef": assessment.egress_policy_ref,
        "workdir": "/workspace",
        "resources": {"cpuMillis": 2000, "memoryMiB": 4096, "pids": 512},
        "timeoutSeconds": assessment.timeout_seconds,
        "outputs": assessment.outputs(),
    }


def validate_cloud_assessment_job(spec) -> None:
    """Rechecked wherever a spec is decoded: admission, persistence, worker."""
    assessment = getattr(spec, "cloud_assessment", None)
    if assessment is None:
        return
    if (
        spec.repository_scan is not None
        or spec.feed_refresh is not None
        or spec.image_assessment is not None
        or spec.web_assessment is not None
        or spec.workload_profile is not None
    ):
        raise ValueError("cloud assessment is its own workload")
    if spec.image_source_ref is None:
        raise ValueError("cloud assessment uses a deployment-owned Prowler image")
    canonical = build_cloud_assessment_job(
        assessment, image_source_ref=spec.image_source_ref
    )
    if set(canonical["command"]) & UNADMITTED_OPTIONS:
        raise ValueError("cloud assessment argv names an unadmitted option")
    for field, expected in (
        ("entrypoint", []),
        ("command", canonical["command"]),
        ("network_mode", canonical["networkMode"]),
        ("egress_policy_ref", canonical["egressPolicyRef"]),
        ("workdir", "/workspace"),
        ("environment", []),
        ("caches", []),
        ("service_ref", None),
        ("registry_credential_ref", None),
        ("timeout_seconds", canonical["timeoutSeconds"]),
    ):
        if getattr(spec, field) != expected:
            raise ValueError("cloud assessment workload differs from its admission")
    if spec.input_attachments != [] or [
        item.model_dump(by_alias=True) for item in spec.outputs
    ] != [
        type(spec.outputs[0]).model_validate(item).model_dump(by_alias=True)
        for item in canonical["outputs"]
    ]:
        raise ValueError("cloud assessment mounts differ from its admission")


def cloud_policy_admits(policy, assessment: CloudAssessment) -> bool:
    """Egress reaches only the provider's HTTPS API endpoints.

    The issued session, not the network, confines the scan to the account:
    API hostnames are shared by every account.
    """
    return all(
        d.purpose == "provider-api"
        and d.protocol == "https"
        and d.port == 443
        and d.target_network_ref is None
        and _AWS_API_HOST.fullmatch(d.host)
        for d in policy.destinations
    )


# -- evidence ---------------------------------------------------------------

INTERPRETATION = (
    "Prowler results are read-only observations of the account, regions and "
    "checks listed in coverage. Denied, throttled, unreachable or failed "
    "collection was not assessed, and a passing check does not establish that "
    "the account is secure."
)
#: Findings go to these Skills for review; review grants no cloud authority.
REVIEW_SKILLS = ("security-investigate", "security-remediate")
MAX_FINDINGS = 4096
MAX_DIAGNOSTICS = 256
DiagnosticKind = Literal[
    "access_denied",
    "throttled",
    "credentials_rejected",
    "endpoint_unreachable",
    "invalid_selection",
    "collection_error",
]
_DENIED = {
    "AccessDenied",
    "AccessDeniedException",
    "UnauthorizedOperation",
    "UnauthorizedAccess",
    "UnauthorizedException",
    "AuthorizationError",
    "AuthorizationErrorException",
    "Forbidden",
    "ForbiddenException",
    "OptInRequired",
    "403",
}
_THROTTLED = {
    "Throttling",
    "ThrottlingException",
    "ThrottledException",
    "TooManyRequestsException",
    "RequestLimitExceeded",
    "RequestThrottled",
    "RequestThrottledException",
    "SlowDown",
    "ProvisionedThroughputExceededException",
    "PriorRequestNotComplete",
}
_CREDENTIALS = {
    "ExpiredToken",
    "ExpiredTokenException",
    "InvalidClientTokenId",
    "UnrecognizedClientException",
    "SignatureDoesNotMatch",
    "InvalidAccessKeyId",
    "AuthFailure",
    "RequestExpired",
    "InvalidToken",
    "NoCredentialsError",
    "PartialCredentialsError",
    "AWSSessionTokenExpiredError",
    "AWSAccessKeyIDInvalidError",
    "AWSSecretAccessKeyInvalidError",
    "AWSInvalidCredentialsError",
}
_UNREACHABLE = {
    "EndpointConnectionError",
    "ConnectTimeoutError",
    "ReadTimeoutError",
    "ConnectionClosedError",
    "ProxyConnectionError",
    "SSLError",
}
#: Answers that a configuration is absent, not failures to collect it.
_ABSENT = re.compile(r"NotFound|NoSuch|NotExist|^404$")
_LEVEL = re.compile(r'"level": "(ERROR|CRITICAL)"')
_MODULE = re.compile(r'"module": "([a-z0-9_]{1,64})"')
_MESSAGE = re.compile(r'"message": "(.*)"\}\s*$')
_SUBJECT = re.compile(r"^([a-z0-9_-]{1,128}) -- ")
_CLASS = re.compile(r"\b([A-Z][A-Za-z0-9]{1,63})\[\d+\]")
_CALL = re.compile(
    r"An error occurred \(([A-Za-z0-9._-]{1,64})\) when calling the "
    r"([A-Za-z0-9]{1,64}) operation"
)
_INVALID = re.compile(
    r"Invalid (?:check|service)\(s\) specified: ([a-z0-9_, -]{1,4096})"
)
_REGION = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-[0-9]{1,2}$")
_GAPS = frozenset(
    {
        "access_denied",
        "throttled",
        "credentials_rejected",
        "endpoint_unreachable",
        "invalid_selection",
        "collection_error",
    }
)


class CloudFinding(Contract):
    finding_uid: Text
    check_id: Text
    status: Literal["FAIL", "MANUAL"]
    muted: bool = False
    severity: Text
    title: Text | None = None
    account: Text | None = None
    region: Text | None = None
    resource_uids: Items = Field(default_factory=list)
    resource_type: Text | None = None
    detail: Text | None = None


class CloudDiagnostic(Contract):
    """One aggregated Prowler log error; provider message text is dropped."""

    kind: DiagnosticKind
    code: Text
    service: Text | None = None
    region: Text | None = None
    operation: Text | None = None
    check: Text | None = None
    count: int = Field(ge=1)


class CloudCoverage(Contract):
    provider: Text
    session_policies: Items = Field(default_factory=list)
    requested_accounts: Items
    discovered_accounts: Items = Field(default_factory=list)
    scanned_accounts: Items = Field(default_factory=list)
    requested_regions: Items
    regions_with_results: Items = Field(default_factory=list)
    regions_with_errors: Items = Field(default_factory=list)
    selected_checks: Items = Field(default_factory=list)
    selected_services: Items = Field(default_factory=list)
    checks_with_results: Items = Field(default_factory=list)
    #: Explicit checks that reported nothing: no matching resources or not
    #: run. Prowler does not say which; errors are in the diagnostics.
    checks_without_results: Items = Field(default_factory=list)
    checks_with_errors: Items = Field(default_factory=list)
    manual_checks: Items = Field(default_factory=list)
    compliant_resources: Items = Field(default_factory=list)
    noncompliant_resources: Items = Field(default_factory=list)
    unevaluated_resources: Items = Field(default_factory=list)
    passed: int = 0
    failed: int = 0
    manual: int = 0
    muted: int = 0


class CloudExecution(Contract):
    state: Literal["succeeded", "failed", "timed_out", "canceled"]
    exit_code: int | None = None
    #: Prowler finished its run; separate from container or transport failure.
    completed: bool
    version: Text | None = None
    started_at: Text | None = None
    finished_at: Text | None = None


class CloudImage(Contract):
    """What actually ran: the deployment source and the resolved image."""

    source_ref: Text
    requested_reference: Text | None = None
    resolved_ref: Text | None = None


class CloudAssessmentSummary(Contract):
    schema_version: Literal["cloud-posture/v1"] = "cloud-posture/v1"
    tool: Literal["prowler"] = "prowler"
    admission: CloudAssessment
    action_class: Literal["cloud-posture"] = CLOUD_ACTION_CLASS
    image: CloudImage
    execution: CloudExecution
    outcome: Literal["findings", "clean", "incomplete", "execution_failed"]
    status: Literal["complete", "partial"]
    findings: list[CloudFinding] = Field(default_factory=list, max_length=4096)
    coverage: CloudCoverage
    diagnostics: list[CloudDiagnostic] = Field(default_factory=list, max_length=256)
    limitations: Items
    result: NativeResult
    native_ref: Text | None = None
    log_ref: Text | None = None
    review_skills: Items = Field(default_factory=lambda: list(REVIEW_SKILLS))
    #: Findings are evidence only: nothing remediates, exports or notifies.
    target_actions: Literal["none"] = "none"
    interpretation: Text = INTERPRETATION
    verification: Literal["not_performed"] = "not_performed"

    @model_validator(mode="after")
    def observed_facts_decide_the_outcome(self):
        """No reader may promote incomplete visibility to a clean result."""
        gaps = any(d.kind in _GAPS for d in self.diagnostics)
        complete = (
            self.execution.completed
            and not gaps
            and not self.limitations
            and not self.coverage.manual
            and self.result.coverage == "complete"
        )
        if (self.status == "complete") != complete:
            raise ValueError("summary status contradicts its observed coverage")
        if any(f.status == "FAIL" for f in self.findings):
            admitted = {"findings"}
        elif complete:
            admitted = {"clean"}
        elif self.execution.completed:
            admitted = {"incomplete"}
        else:
            admitted = {"incomplete", "execution_failed"}
        if self.outcome not in admitted:
            raise ValueError("summary outcome contradicts its observed results")
        return self


def interpret_exit(exit_code: int | None, terminal_state: str) -> CloudExecution:
    """Prowler exits 0 after a run (exit 3 is disabled); anything else failed."""
    state = (
        terminal_state
        if terminal_state in {"succeeded", "timed_out", "canceled"}
        else "failed"
    )
    if state == "succeeded" and exit_code != 0:
        state = "failed"
    return CloudExecution(
        state=state, exitCode=exit_code, completed=state == "succeeded"
    )


def parse_report(body: bytes) -> tuple[list[dict], str | None]:
    """Native OCSF records and a limitation code when the array is unusable.

    A truncated array keeps every complete record before the cut.
    """
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return [], "report_malformed"
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        pass
    else:
        if isinstance(value, list) and all(isinstance(v, dict) for v in value):
            return value, None
        return [], "report_malformed"
    stripped = text.lstrip()
    if not stripped.startswith("["):
        return [], "report_malformed"
    decoder, records, index = json.JSONDecoder(), [], 1
    while True:
        while index < len(stripped) and stripped[index] in " \t\r\n,":
            index += 1
        try:
            record, index = decoder.raw_decode(stripped, index)
        except (ValueError, RecursionError):
            break
        if not isinstance(record, dict):
            return [], "report_malformed"
        records.append(record)
    return records, "report_truncated"


def _text(value, limit: int = 1024) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    return value


def _get(record: dict, *path):
    for key in path:
        if not isinstance(record, dict):
            return None
        record = record.get(key)
    return record


def parse_log(body: bytes, selected_checks: Sequence[str]) -> list[CloudDiagnostic]:
    """Aggregate ERROR and CRITICAL lines into value-free diagnostics.

    Prowler's file handler writes one JSON-like object per line without
    escaping messages, so fields are matched rather than parsed as JSON.
    """
    counts: Counter = Counter()
    checks = set(selected_checks)
    for line in body.decode("utf-8", errors="replace").splitlines():
        level = _LEVEL.search(line)
        if not level:
            continue
        message_match = _MESSAGE.search(line)
        message = message_match.group(1) if message_match else ""
        module_match = _MODULE.search(line)
        module = module_match.group(1) if module_match else None
        service = module.removesuffix("_service") if module else None
        invalid = _INVALID.search(message)
        if invalid:
            for name in invalid.group(1).split(","):
                counts[
                    (
                        "invalid_selection",
                        "invalid_selection",
                        None,
                        None,
                        None,
                        name.strip()[:128] or None,
                    )
                ] += 1
            continue
        subject = _SUBJECT.match(message)
        region = check = None
        if subject:
            token = subject.group(1)
            if _REGION.fullmatch(token):
                region = token
            elif token in checks or "_" in token:
                check = token
        call = _CALL.search(message)
        error_class = _CLASS.search(message)
        if not call and not error_class and level.group(1) == "CRITICAL":
            # Advice that follows a fatal error, which already failed the run.
            continue
        code = (
            call.group(1)
            if call
            else error_class.group(1) if error_class else "unrecognized_error"
        )
        if _ABSENT.search(code):
            continue
        operation = call.group(2) if call else None
        names = {code, error_class.group(1) if error_class else ""}
        if names & _DENIED:
            kind = "access_denied"
        elif names & _THROTTLED:
            kind = "throttled"
        elif names & _CREDENTIALS:
            kind = "credentials_rejected"
        elif names & _UNREACHABLE:
            kind = "endpoint_unreachable"
        else:
            kind = "collection_error"
        counts[(kind, code, service, region, operation, check)] += 1
    return [
        CloudDiagnostic(
            kind=kind,
            code=code,
            service=service,
            region=region,
            operation=operation,
            check=check,
            count=count,
        )
        for (kind, code, service, region, operation, check), count in sorted(
            counts.items(), key=lambda item: tuple(str(v) for v in item[0])
        )
    ]


def summarize_cloud_assessment(
    assessment: CloudAssessment,
    *,
    image: CloudImage,
    report: bytes | None,
    log: bytes | None,
    exit_code: int | None,
    terminal_state: str,
    native_ref: str | None = None,
    log_ref: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    limitations: Sequence[str] = (),
) -> CloudAssessmentSummary:
    """Classify one run from its native report, log and terminal state.

    ``report`` is ``None`` when Prowler wrote none: after a finished run that
    means it had no results, otherwise nothing was assessed. ``log`` is
    ``None`` when no log could be read; Prowler records failed collections
    only there, so its absence leaves collection unverified.
    """
    execution = interpret_exit(exit_code, terminal_state)
    gaps = list(limitations)
    if not execution.completed:
        gaps.append(f"execution_{execution.state}")
    records: list[dict] = []
    if report is not None:
        records, problem = parse_report(report)
        if problem:
            gaps.append(problem)
    if log is None:
        gaps.append("log_unavailable")
    diagnostics = parse_log(log, assessment.checks) if log else []
    if len(diagnostics) > MAX_DIAGNOSTICS:
        diagnostics = diagnostics[:MAX_DIAGNOSTICS]
        gaps.append("diagnostics_truncated")
    gaps.extend(sorted({d.kind for d in diagnostics}))

    findings: list[CloudFinding] = []
    statuses: Counter = Counter()
    accounts, regions, checks, manual_checks = set(), set(), set(), set()
    compliant, noncompliant, unevaluated = set(), set(), set()
    muted = 0
    version = None
    for record in records:
        status = _get(record, "status_code")
        check = _text(_get(record, "metadata", "event_code"), 128)
        uid = _text(_get(record, "finding_info", "uid"))
        if status not in {"PASS", "FAIL", "MANUAL"} or not check or not uid:
            gaps.append("report_record_malformed")
            continue
        version = version or _text(_get(record, "metadata", "product", "version"), 64)
        account = _text(_get(record, "cloud", "account", "uid"), 128)
        region = _text(_get(record, "cloud", "region"), 64)
        resources = [
            r for r in (_get(record, "resources") or []) if isinstance(r, dict)
        ]
        uids = [u for u in (_text(r.get("uid")) for r in resources) if u][:64]
        is_muted = _get(record, "status") == "Suppressed" or bool(
            _get(record, "unmapped", "muted")
        )
        muted += is_muted
        statuses[status] += 1
        checks.add(check)
        if account:
            accounts.add(account)
        if region:
            regions.add(region)
        {"PASS": compliant, "FAIL": noncompliant, "MANUAL": unevaluated}[status].update(
            uids
        )
        if status == "MANUAL":
            manual_checks.add(check)
        if status == "PASS":
            continue
        if len(findings) >= MAX_FINDINGS:
            gaps.append("findings_truncated")
            continue
        findings.append(
            CloudFinding(
                findingUid=uid,
                checkId=check,
                status=status,
                muted=is_muted,
                severity=_text(_get(record, "severity"), 32) or "unknown",
                title=_text(_get(record, "finding_info", "title")),
                account=account,
                region=region,
                resourceUids=uids,
                resourceType=(
                    _text(resources[0].get("type"), 128) if resources else None
                ),
                detail=_text(_get(record, "status_detail")),
            )
        )
    if statuses["MANUAL"]:
        # Prowler reports evaluations it could not complete, such as a denied
        # read, as MANUAL; they are neither compliant nor findings.
        gaps.append("manual_results")
    if accounts - {assessment.account}:
        gaps.append("unadmitted_account_results")
    credentials_rejected = any(d.kind == "credentials_rejected" for d in diagnostics)
    failed_regions = {d.region for d in diagnostics if d.region and d.kind in _GAPS}
    gaps = sorted(set(gaps))
    complete = execution.completed and not gaps
    execution = execution.model_copy(
        update={
            "version": version,
            "started_at": started_at,
            "finished_at": finished_at,
        }
    )
    if statuses["FAIL"]:
        outcome = "findings"
    elif complete:
        outcome = "clean"
    elif not execution.completed and not records:
        outcome = "execution_failed"
    else:
        outcome = "incomplete"
    asset = f"{assessment.provider}:{assessment.account}"
    result = NativeResult(
        execution=execution.state,
        coverage="complete" if complete else "partial",
        findings=[
            Finding(
                source="cloud",
                tool="prowler",
                rule_id=f.check_id,
                asset=asset,
                native_id=f.finding_uid,
                evidence_refs=[native_ref],
            )
            for f in findings
            if f.status == "FAIL" and native_ref
        ],
        examined_assets=(
            [asset] if execution.completed and not credentials_rejected else []
        ),
        unexamined_assets=[] if complete else [asset],
        limitations=gaps,
    )
    coverage = CloudCoverage(
        provider=assessment.provider,
        sessionPolicies=list(READ_ONLY_POLICY_ARNS),
        requestedAccounts=[assessment.account],
        discoveredAccounts=sorted(accounts),
        scannedAccounts=(
            [assessment.account]
            if execution.completed and not credentials_rejected
            else []
        ),
        requestedRegions=list(assessment.regions),
        regionsWithResults=sorted(regions),
        regionsWithErrors=sorted(failed_regions),
        selectedChecks=list(assessment.checks),
        selectedServices=list(assessment.services),
        checksWithResults=sorted(checks),
        checksWithoutResults=[c for c in assessment.checks if c not in checks],
        checksWithErrors=sorted({d.check for d in diagnostics if d.check}),
        manualChecks=sorted(manual_checks),
        # A resource is compliant only when every result for it passed.
        compliantResources=sorted(compliant - noncompliant - unevaluated)[:4096],
        noncompliantResources=sorted(noncompliant)[:4096],
        unevaluatedResources=sorted(unevaluated - noncompliant)[:4096],
        passed=statuses["PASS"],
        failed=statuses["FAIL"],
        manual=statuses["MANUAL"],
        muted=muted,
    )
    return CloudAssessmentSummary(
        admission=assessment,
        image=image,
        execution=execution,
        outcome=outcome,
        status="complete" if complete else "partial",
        findings=findings,
        coverage=coverage,
        diagnostics=diagnostics,
        limitations=gaps,
        result=result,
        nativeRef=native_ref,
        logRef=log_ref,
    )


__all__ = [
    "CLOUD_IMAGE_SOURCE_REF",
    "INTERPRETATION",
    "REVIEW_SKILLS",
    "UNADMITTED_OPTIONS",
    "CloudAssessmentSummary",
    "CloudCoverage",
    "CloudDiagnostic",
    "CloudExecution",
    "CloudFinding",
    "CloudImage",
    "build_cloud_assessment_job",
    "cloud_policy_admits",
    "interpret_exit",
    "parse_log",
    "parse_report",
    "prowler_argv",
    "summarize_cloud_assessment",
    "validate_cloud_assessment_job",
]
