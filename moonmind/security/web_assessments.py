"""ZAP web and API assessments on the ContainerJob contracts.

The upstream ZAP image runs one Automation Framework plan rendered here from
an admitted :class:`WebAssessment`, never from caller text. ``baseline``
crawls and checks passively, ``api`` imports an admitted definition and checks
passively, and only ``active`` adds the active scanner at exactly its declared
strength. The worker writes the plan into the job's outputs and delivers an
identity's secret as a read-only file, so neither the plan nor argv holds it.
"""

from __future__ import annotations

import json
from typing import Literal, Sequence
from urllib.parse import urljoin, urlsplit

from pydantic import Field

from moonmind.schemas.repository_scanner_models import (
    WEB_OUTPUT_FILES,
    WebAssessment,
    route_within,
)
from moonmind.security.assessments import Contract, Finding, Items, Text, content_key
from moonmind.security.repository_scanners import NativeResult
from moonmind.utils.logging import redact_sensitive_text

#: Deployment image source; ``ghcr.io/zaproxy/zaproxy:stable`` by default.
WEB_IMAGE_SOURCE_REF = "web-assessment"
PLAN_FILE = "plan.yaml"
CONTEXT = "moonmind"
API_INPUT = "api-definition"
API_FILES = {"openapi": "definition.json", "graphql": "schema.graphql"}
#: Read-only directory holding the identity secret as ``secret``.
CREDENTIAL_TARGET = "/run/moonmind-web-credential"
SECRET_ENV = "MOONMIND_WEB_SECRET"
#: The only Automation Framework jobs a plan may contain. No Nuclei, template
#: catalog or script job is reachable from an admission.
PLAN_JOB_TYPES = frozenset(
    {
        "passiveScan-config",
        "requestor",
        "openapi",
        "graphql",
        "spider",
        "spiderAjax",
        "passiveScan-wait",
        "report",
        "export",
        "activeScan",
    }
)
_STRENGTH = {"low": "Low", "medium": "Medium", "high": "High"}
# Arguments: output directory, then the bearer site (``-`` for none). The
# upstream image does not read proxy variables, so the admitted egress proxy
# is passed as ZAP's upstream proxy; the secret is read from its file only.
_LAUNCH = r"""out="$1"; bearer_site="$2"; set --
case "${MOONMIND_EGRESS_PROXY:-}" in
http://*:3128)
  p="${MOONMIND_EGRESS_PROXY#http://}"
  set -- -config network.connection.httpProxy.enabled=true \
    -config "network.connection.httpProxy.host=${p%:*}" \
    -config "network.connection.httpProxy.port=${p##*:}" ;;
esac
if [ -r /run/moonmind-web-credential/secret ]; then
  MOONMIND_WEB_SECRET="$(cat /run/moonmind-web-credential/secret)"
  export MOONMIND_WEB_SECRET
  if [ "$bearer_site" != - ]; then
    ZAP_AUTH_HEADER=Authorization
    ZAP_AUTH_HEADER_VALUE="Bearer $MOONMIND_WEB_SECRET"
    ZAP_AUTH_HEADER_SITE="$bearer_site"
    export ZAP_AUTH_HEADER ZAP_AUTH_HEADER_VALUE ZAP_AUTH_HEADER_SITE
  fi
fi
exec zap.sh -cmd -autorun "$out/plan.yaml" "$@"
"""


def _url_regex(origin: str, prefix: str) -> str:
    # Java regex over the whole URL: the prefix itself or below it.
    base = origin if prefix == "/" else origin + prefix
    return rf"\Q{base}\E(?:[/?#].*)?"


def _minutes(seconds: float) -> int:
    return max(1, int(seconds // 60))


def build_web_assessment_plan(assessment: WebAssessment) -> dict:
    """The Automation Framework plan for one admitted assessment.

    Deterministic, and free of secrets: an identity's password is the
    ``${MOONMIND_WEB_SECRET}`` placeholder ZAP resolves from its environment.
    The passive report and messages are written before any active test, so
    a timeout or cancellation during active testing keeps checked evidence.
    """
    a = assessment
    origin = a.origin
    out = f"/outputs/{a.output_directory}"
    identity = a.identity
    user = (
        {"user": identity.name}
        if identity is not None and identity.method == "form"
        else {}
    )
    context: dict = {
        "name": CONTEXT,
        "urls": [origin + path for path in a.start_paths],
        "includePaths": [_url_regex(origin, r.path) for r in a.routes],
        "excludePaths": [_url_regex(origin, e) for e in a.exclusions],
    }
    if identity is not None and identity.method == "form":
        login = origin + identity.login_path
        context["authentication"] = {
            "method": "form",
            "parameters": {
                "loginPageUrl": login,
                "loginRequestUrl": login,
                "loginRequestBody": (
                    f"{identity.username_field}={{%username%}}"
                    f"&{identity.password_field}={{%password%}}"
                ),
            },
            # Login answers are often bodiless redirects, so the session is
            # checked by polling the verification route.
            "verification": {
                "method": "poll",
                "loggedInRegex": rf"\Q{identity.logged_in_marker}\E",
                "pollUrl": origin + identity.verification_path,
                "pollFrequency": 20,
                "pollUnits": "requests",
            },
        }
        context["sessionManagement"] = {"method": "cookie"}
        context["users"] = [
            {
                "name": identity.name,
                "credentials": {
                    "username": identity.username,
                    "password": "${" + SECRET_ENV + "}",
                },
            }
        ]
    budget = a.timeout_seconds
    discovery_minutes = _minutes(budget * 0.3 / max(1, len(a.discovery)))
    jobs: list[dict] = [
        {
            "type": "passiveScan-config",
            "parameters": {"scanOnlyInScope": True, "maxAlertsPerRule": 10},
        }
    ]
    if identity is not None:
        # Anonymous first, then as the identity: authentication is checked
        # against the verification route rather than assumed.
        verify = [{"url": origin + identity.verification_path, "method": "GET"}]
        jobs.append({"type": "requestor", "name": "verify-anonymous", "requests": verify})
        jobs.append(
            {
                "type": "requestor",
                "name": "verify-identity",
                "parameters": dict(user),
                "requests": verify,
            }
        )
    api = a.api_definition
    if api is not None:
        path = f"/inputs/{API_INPUT}/{API_FILES[api.format]}"
        if api.format == "openapi":
            jobs.append(
                {
                    "type": "openapi",
                    "parameters": {
                        "apiFile": path,
                        "targetUrl": origin,
                        "context": CONTEXT,
                        **user,
                    },
                }
            )
        else:
            jobs.append(
                {
                    "type": "graphql",
                    "parameters": {
                        "endpoint": origin + api.endpoint_path,
                        "schemaFile": path,
                    },
                }
            )
    for method in a.discovery:
        if method == "spider":
            jobs.append(
                {
                    "type": "spider",
                    "parameters": {
                        "context": CONTEXT,
                        **user,
                        "maxDuration": discovery_minutes,
                        "maxDepth": a.max_depth,
                        "maxChildren": a.max_children,
                        "threadCount": 1,
                        # Crawling follows links; submitting forms is an
                        # application action reserved for active mode.
                        "postForm": a.mode == "active",
                        "parseRobotsTxt": False,
                        "parseSitemapXml": False,
                    },
                }
            )
        else:
            jobs.append(
                {
                    "type": "spiderAjax",
                    "parameters": {
                        "context": CONTEXT,
                        **user,
                        "maxDuration": discovery_minutes,
                        "maxCrawlDepth": a.max_depth,
                        "numberOfBrowsers": 1,
                        "browserId": "firefox-headless",
                        "inScopeOnly": True,
                        "randomInputs": False,
                    },
                }
            )
    passive_wait = {
        "type": "passiveScan-wait",
        "parameters": {"maxDuration": _minutes(budget * 0.1)},
    }
    jobs.append(passive_wait)
    if a.mode == "active":
        jobs.extend(_reports(a, out, passive=True))
        strength = _STRENGTH[a.active_strength]
        jobs.append(
            {
                "type": "activeScan",
                "parameters": {
                    "context": CONTEXT,
                    **user,
                    "maxScanDurationInMins": _minutes(budget * 0.4),
                    "maxRuleDurationInMins": _minutes(budget * 0.1),
                    "threadPerHost": 1,
                    "delayInMs": -(-1000 // a.max_requests_per_second),
                    "handleAntiCSRFTokens": True,
                },
                # Intensity is exactly the admitted strength, never inferred.
                "policyDefinition": {
                    "defaultStrength": strength,
                    "defaultThreshold": "Medium",
                },
            }
        )
        jobs.append(dict(passive_wait))
    jobs.extend(_reports(a, out, passive=False))
    return {
        "env": {
            "contexts": [context],
            "parameters": {
                "failOnError": False,
                "failOnWarning": False,
                "continueOnFailure": True,
                "progressToStdout": True,
            },
        },
        "jobs": jobs,
    }


def _reports(a: WebAssessment, out: str, *, passive: bool) -> list[dict]:
    report, messages = (
        ("passive-report", "passive-messages") if passive else ("report", "messages")
    )
    return [
        {
            "type": "report",
            "parameters": {
                "template": "traditional-json-plus",
                "reportDir": out,
                "reportFile": WEB_OUTPUT_FILES[report],
                "reportTitle": f"MoonMind {a.mode} web assessment",
            },
        },
        {
            "type": "export",
            "parameters": {
                "context": CONTEXT,
                "type": "har",
                # Crawl, verification and attack messages, not only history.
                "source": "all",
                "fileName": f"{out}/{WEB_OUTPUT_FILES[messages]}",
            },
        },
    ]


def render_web_assessment_plan(assessment: WebAssessment) -> bytes:
    """Plan bytes; JSON is valid YAML and keeps the rendering canonical."""
    return (
        json.dumps(build_web_assessment_plan(assessment), indent=1, sort_keys=True)
        + "\n"
    ).encode()


def _bearer_site(assessment: WebAssessment) -> str:
    identity = assessment.identity
    if identity is None or identity.method != "bearer":
        return "-"
    # ZAP matches the header site as host[:port] within the request URL.
    return assessment.origin.split("://", 1)[1]


def build_web_assessment_job(
    assessment: WebAssessment, *, image_source_ref: str = WEB_IMAGE_SOURCE_REF
) -> dict:
    """Workload only; admission stamps the workspace and scope authority."""
    api = assessment.api_definition
    return {
        "imageSourceRef": image_source_ref,
        "webAssessment": assessment.model_dump(mode="json", by_alias=True),
        "inputAttachments": (
            [
                {
                    "name": API_INPUT,
                    "artifactRef": api.input_artifact_ref,
                    "fileName": API_FILES[api.format],
                    "maxBytes": api.max_bytes,
                }
            ]
            if api is not None
            else []
        ),
        "entrypoint": ["sh"],
        "command": [
            "-c",
            _LAUNCH,
            "moonmind-web-assessment",
            f"/outputs/{assessment.output_directory}",
            _bearer_site(assessment),
        ],
        "networkMode": "bridge",
        "egressPolicyRef": assessment.egress_policy_ref,
        "workdir": "/workspace",
        "resources": {"cpuMillis": 2000, "memoryMiB": 4096, "pids": 512},
        # ZAP's own plan budget ends first; the margin covers its startup.
        "timeoutSeconds": assessment.timeout_seconds + 120,
        "outputs": assessment.outputs(),
    }


def validate_web_assessment_job(spec) -> None:
    """Rechecked wherever a spec is decoded: admission, persistence, worker."""
    assessment = getattr(spec, "web_assessment", None)
    if assessment is None:
        return
    if (
        spec.repository_scan is not None
        or spec.feed_refresh is not None
        or spec.image_assessment is not None
        or spec.workload_profile is not None
    ):
        raise ValueError("web assessment is its own workload")
    if spec.image_source_ref is None:
        raise ValueError("web assessment uses a deployment-owned ZAP image")
    if spec.input_attachments is None:
        raise ValueError("web assessment never mounts the live workspace")
    canonical = build_web_assessment_job(
        assessment, image_source_ref=spec.image_source_ref
    )
    for field, expected in (
        ("entrypoint", canonical["entrypoint"]),
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
            raise ValueError("web assessment workload differs from its admission")
    for field, key in (("input_attachments", "inputAttachments"), ("outputs", "outputs")):
        items = getattr(spec, field)
        model = type(items[0]) if items else None
        actual = [v.model_dump(by_alias=True) for v in items]
        expected = [
            model.model_validate(item).model_dump(by_alias=True) if model else item
            for item in canonical[key]
        ]
        if actual != expected:
            raise ValueError("web assessment mounts differ from its admission")


def origin_endpoint(assessment: WebAssessment) -> tuple[str, str, int]:
    scheme, _, rest = assessment.origin.partition("://")
    host, _, port = rest.partition(":")
    return scheme, host, int(port) if port else (443 if scheme == "https" else 80)


def web_policy_admits(policy, assessment: WebAssessment) -> bool:
    """Egress reaches only admitted live targets, including this origin.

    Network permission is necessary, not sufficient: what the job may do to
    the application is the scope's action class, checked separately.
    """
    scheme, host, port = origin_endpoint(assessment)
    return all(
        d.purpose == "live-target" for d in policy.destinations
    ) and any(
        (d.host, d.port, d.protocol) == (host, port, scheme)
        for d in policy.destinations
    )


def path_admitted(assessment: WebAssessment, path: str, method: str = "GET") -> bool:
    """Whether a request path and method fall inside the admitted routes."""
    return not any(route_within(path, e) for e in assessment.exclusions) and any(
        route_within(path, r.path) and method in r.methods for r in assessment.routes
    )


# -- evidence ---------------------------------------------------------------

#: Headers whose values are credentials or session state in either direction.
SENSITIVE_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie"}
)
EXCERPT_BYTES = 1024
INTERPRETATION = (
    "ZAP findings are scanner observations about the routes and identity "
    "listed in coverage. They do not establish exploitability, and routes "
    "outside coverage were not examined."
)
#: Findings go to these Skills for review; review grants no target authority.
REVIEW_SKILLS = ("security-investigate", "security-remediate")
_RISK = {"0": "informational", "1": "low", "2": "medium", "3": "high"}


class WebEvidenceError(ValueError):
    """A bounded diagnostic code; never carries report text."""


class WebFinding(Contract):
    finding_id: Text
    rule_id: Text
    name: Text
    risk: Literal["informational", "low", "medium", "high"]
    confidence: Text
    phase: Literal["passive", "active"]
    target: Text
    route: Text
    method: Text
    identity: Text
    parameter: Text | None = None
    evidence_excerpt: Text | None = None
    request_excerpt: Text | None = None
    response_excerpt: Text | None = None
    reproduction: Text


class WebAuthentication(Contract):
    identity: Text
    method: Literal["form", "bearer"]
    status: Literal["verified", "failed", "unsupported", "not_attempted"]
    anonymous_compared: bool
    reason: Text | None = None


class WebCoverage(Contract):
    discovered_routes: Items = Field(default_factory=list)
    tested_routes: Items = Field(default_factory=list)
    untested_routes: Items = Field(default_factory=list)
    unreachable_routes: Items = Field(default_factory=list)
    inaccessible_routes: Items = Field(default_factory=list)
    denied_requests: Items = Field(default_factory=list)
    passive_checks: bool = False
    active_checks: bool = False
    authentication: WebAuthentication | None = None


class WebExecution(Contract):
    state: Literal["succeeded", "failed", "timed_out", "canceled"]
    exit_code: int | None = None
    #: ZAP's own plan outcome, separate from container or transport failure.
    plan: Literal["completed", "warnings", "errors", "not_completed"]
    version: Text | None = None


class WebAssessmentSummary(Contract):
    schema_version: Literal["web-assessment/v1"] = "web-assessment/v1"
    tool: Literal["zap"] = "zap"
    admission: WebAssessment
    action_class: Text
    execution: WebExecution
    result: NativeResult
    findings: list[WebFinding] = Field(default_factory=list, max_length=4096)
    coverage: WebCoverage
    status: Literal["complete", "partial"]
    limitations: Items
    plan_ref: Text | None = None
    native_ref: Text | None = None
    messages_ref: Text | None = None
    review_skills: Items = Field(default_factory=lambda: list(REVIEW_SKILLS))
    #: Alerts are evidence only: nothing mutates, fixes or publishes on them.
    target_actions: Literal["none"] = "none"
    interpretation: Text = INTERPRETATION
    verification: Literal["not_performed"] = "not_performed"


def interpret_exit(exit_code: int | None, terminal_state: str) -> WebExecution:
    """ZAP's ``-autorun`` exit codes: 0 ran, 1 plan errors, 2 plan warnings.

    Anything else, and every timeout or cancellation, is a container or
    transport outcome; findings never come from the exit code.
    """
    state = (
        terminal_state
        if terminal_state in {"succeeded", "timed_out", "canceled"}
        else "failed"
    )
    plan = "not_completed"
    if state not in {"timed_out", "canceled"}:
        plan = {0: "completed", 1: "errors", 2: "warnings"}.get(
            exit_code, "not_completed"
        )
        # A non-zero plan code is a finished plan, not a failed container.
        state = "succeeded" if plan != "not_completed" else "failed"
    return WebExecution(state=state, exitCode=exit_code, plan=plan)


class _Scrubber:
    def __init__(self, secrets: Sequence[str]):
        self.secrets = [s for s in secrets if s]

    def text(self, value, limit: int | None = None) -> str:
        if not isinstance(value, str):
            value = "" if value is None else str(value)
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = redact_sensitive_text(value)
        if limit is not None and len(value.encode()) > limit:
            value = value.encode()[:limit].decode(errors="ignore") + "…"
        return value

    def header_block(self, value, limit: int) -> str:
        lines = []
        for line in self.text(value).splitlines():
            name, sep, _ = line.partition(":")
            if sep and name.strip().lower() in SENSITIVE_HEADERS:
                line = f"{name}: [REDACTED]"
            lines.append(line)
        return self.text("\n".join(lines), limit)


def _load(body: bytes | None, kind: str):
    if body is None:
        return None
    try:
        value = json.loads(body)
    except (ValueError, RecursionError, UnicodeDecodeError) as exc:
        raise WebEvidenceError(f"{kind}_malformed") from exc
    if not isinstance(value, dict):
        raise WebEvidenceError(f"{kind}_malformed")
    return value


def _alerts(report: dict | None, origin: str) -> list[tuple[dict, dict]]:
    if report is None:
        return []
    sites = report.get("site")
    if not isinstance(sites, list):
        raise WebEvidenceError("report_malformed")
    rows = []
    for site in sites:
        if not isinstance(site, dict) or not isinstance(site.get("alerts"), list):
            raise WebEvidenceError("report_malformed")
        for alert in site["alerts"]:
            if not isinstance(alert, dict) or not isinstance(
                alert.get("instances", []), list
            ):
                raise WebEvidenceError("report_malformed")
            for instance in alert.get("instances", []):
                if isinstance(instance, dict):
                    rows.append((alert, instance))
    return rows


def _split(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    return origin, parts.path or "/"


def sanitize_report(body: bytes, secrets: Sequence[str], limit: int) -> bytes:
    """Native ZAP JSON with credentials removed and bodies bounded."""
    scrub = _Scrubber(secrets)
    report = _load(body, "report")
    for site in report.get("site") or []:
        for alert in site.get("alerts") or []:
            for instance in alert.get("instances") or []:
                for key in ("request-header", "response-header"):
                    if key in instance:
                        instance[key] = scrub.header_block(instance[key], 4096)
                for key in ("request-body", "response-body"):
                    if key in instance:
                        instance[key] = scrub.text(instance[key], EXCERPT_BYTES)
    safe = _scrub_tree(report, scrub)
    payload = json.dumps(safe, sort_keys=True, separators=(",", ":")).encode()
    if len(payload) > limit:
        raise WebEvidenceError("report_exceeds_bound")
    return payload


def sanitize_messages(body: bytes, secrets: Sequence[str], limit: int) -> bytes:
    """HAR without credentials, session values, login bodies or large content."""
    scrub = _Scrubber(secrets)
    har = _load(body, "messages")
    entries = (har.get("log") or {}).get("entries")
    if not isinstance(entries, list):
        raise WebEvidenceError("messages_malformed")
    for entry in entries:
        for side in ("request", "response"):
            message = entry.get(side) or {}
            for header in message.get("headers") or []:
                if str(header.get("name", "")).lower() in SENSITIVE_HEADERS:
                    header["value"] = "[REDACTED]"
            for cookie in message.get("cookies") or []:
                cookie["value"] = "[REDACTED]"
        post = (entry.get("request") or {}).get("postData")
        if isinstance(post, dict):
            # Form posts carry credentials; only their shape is retained.
            post["text"] = "[REDACTED]" if post.get("text") else ""
            for param in post.get("params") or []:
                param["value"] = "[REDACTED]"
        content = (entry.get("response") or {}).get("content")
        if isinstance(content, dict) and "text" in content:
            content["text"] = scrub.text(content["text"], EXCERPT_BYTES)
    safe = _scrub_tree(har, scrub)
    payload = json.dumps(safe, sort_keys=True, separators=(",", ":")).encode()
    if len(payload) > limit:
        raise WebEvidenceError("messages_exceed_bound")
    return payload


def _scrub_tree(value, scrub: _Scrubber):
    if isinstance(value, str):
        return scrub.text(value)
    if isinstance(value, list):
        return [_scrub_tree(item, scrub) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_tree(item, scrub) for key, item in value.items()}
    return value


def _entries(har: dict | None) -> list[dict]:
    if har is None:
        return []
    entries = (har.get("log") or {}).get("entries")
    if not isinstance(entries, list):
        raise WebEvidenceError("messages_malformed")
    return [e for e in entries if isinstance(e, dict)]


def _header(message: dict, name: str) -> str | None:
    for header in message.get("headers") or []:
        if str(header.get("name", "")).lower() == name:
            return str(header.get("value", ""))
    return None


def summarize_web_assessment(
    assessment: WebAssessment,
    *,
    report: bytes | None,
    messages: bytes | None,
    passive_report: bytes | None,
    passive_messages: bytes | None,
    exit_code: int | None,
    terminal_state: str,
    native_ref: str | None = None,
    messages_ref: str | None = None,
    plan_ref: str | None = None,
    limitations: Sequence[str] = (),
) -> WebAssessmentSummary:
    """What was tested, as which identity, and what ZAP observed there.

    Native files are the already-sanitized retained copies. Anything missing,
    malformed, unreachable, denied or unauthenticated makes the result
    partial; it never becomes a clean result.
    """
    a = assessment
    limits = list(limitations)
    execution = interpret_exit(exit_code, terminal_state)
    if execution.state != "succeeded":
        limits.append(f"container_job_{execution.state}")
    if execution.plan != "completed":
        limits.append(f"zap_plan_{execution.plan}")

    def parsed(body, kind):
        try:
            return _load(body, kind)
        except WebEvidenceError as exc:
            limits.append(str(exc))
            return None

    final_report = parsed(report, "report")
    final_messages = parsed(messages, "messages")
    early_report = parsed(passive_report, "passive_report")
    early_messages = parsed(passive_messages, "passive_messages")
    if final_report is None:
        limits.append("final_report_unavailable")
    if final_messages is None:
        limits.append("messages_unavailable")
    try:
        final_alerts = _alerts(final_report, a.origin)
        early_alerts = _alerts(early_report, a.origin)
    except WebEvidenceError as exc:
        limits.append(str(exc))
        final_alerts, early_alerts = [], []
    if a.mode == "active" and early_report is None:
        limits.append("passive_report_unavailable")
    stopped_early = False
    for native in (early_report, final_report):
        if native is None:
            continue
        if native.get("afPlanErrors"):
            limits.append("zap_plan_errors")
        if native.get("afPlanWarns"):
            limits.append("zap_plan_warnings")
        stopped = native.get("stoppingInsight")
        if isinstance(stopped, dict):
            stopped_early = True
            # ZAP ended the plan early, for example on authentication failures.
            limits.append(f"zap_stopped:{_bounded(stopped.get('key'), 64) or 'unknown'}")
    try:
        # The passive export holds every exchanged message, including the
        # anonymous and identity verification requests, in order.
        entries = _entries(early_messages) + _entries(final_messages)
    except WebEvidenceError as exc:
        limits.append(str(exc))
        entries = []
    identity = a.identity
    actor = identity.name if identity is not None else "anonymous"

    # Coverage from the messages ZAP actually exchanged.
    discovered, tested, unreachable, inaccessible = set(), set(), set(), set()
    denied, challenges = set(), set()
    for entry in entries:
        request = entry.get("request") or {}
        response = entry.get("response") or {}
        method = str(request.get("method", "GET")).upper()
        origin, path = _split(str(request.get("url", "")))
        status = response.get("status") or 0
        route = f"{method} {path}"
        if origin != a.origin:
            denied.add(f"off-origin {method} {origin}")
            continue
        if not path_admitted(a, path, method):
            denied.add(f"unadmitted {route}")
            continue
        discovered.add(route)
        if not isinstance(status, int) or status == 0 or status in {502, 503, 504}:
            unreachable.add(route)
            continue
        tested.add(route)
        if status in {401, 403}:
            inaccessible.add(route)
            scheme = (_header(response, "www-authenticate") or "").split(" ")[0]
            if scheme:
                challenges.add(scheme.lower())
        location = _header(response, "location")
        if 300 <= status < 400 and location:
            target_origin, target_path = _split(urljoin(a.origin + path, location))
            if target_origin != a.origin or not path_admitted(a, target_path):
                denied.add(f"redirect {route} -> {target_origin}{target_path}")
            else:
                discovered.add(f"GET {target_path}")
    if denied:
        limits.append("out_of_scope_requests_denied")
    if unreachable:
        limits.append("routes_unreachable")
    if inaccessible:
        limits.append("routes_inaccessible")
    if not tested:
        limits.append("no_routes_tested")
    untested = [
        r.path
        for r in a.routes
        if not any(route_within(t.split(" ", 1)[1], r.path) for t in tested)
    ]
    if untested:
        limits.append("admitted_routes_untested")

    authentication = None
    if identity is not None:
        try:
            # The first export holds the verification requests before any
            # attack traffic, in the order they were sent.
            exchanged = _entries(early_messages if a.mode == "active" else final_messages)
        except WebEvidenceError:
            exchanged = []
        verify_responses = [
            e.get("response") or {}
            for e in exchanged
            if _split(str((e.get("request") or {}).get("url", "")))
            == (a.origin, identity.verification_path)
            and str((e.get("request") or {}).get("method", "GET")).upper() == "GET"
        ]
        authentication = _authentication(identity, verify_responses, challenges)
        if authentication.status != "verified":
            limits.append(f"authentication_{authentication.status}")

    # Findings, attributed to target, route, method, identity and rule.
    passive_keys = {
        (alert.get("pluginid"), inst.get("uri"), inst.get("method"), inst.get("param"))
        for alert, inst in early_alerts
    }
    findings: dict[str, WebFinding] = {}
    native: dict[str, Finding] = {}
    reproduce = native_ref or "unavailable"
    alerts = early_alerts + [
        (alert, inst)
        for alert, inst in final_alerts
        if (alert.get("pluginid"), inst.get("uri"), inst.get("method"), inst.get("param"))
        not in passive_keys
    ]
    for index, (alert, inst) in enumerate(alerts):
        origin, path = _split(str(inst.get("uri", "")))
        method = str(inst.get("method", "GET")).upper()
        if origin != a.origin or not path_admitted(a, path, method):
            denied.add(f"finding outside scope {method} {origin}{path}")
            continue
        rule = str(alert.get("pluginid") or alert.get("alertRef") or "unknown")
        key = (alert.get("pluginid"), inst.get("uri"), inst.get("method"), inst.get("param"))
        phase = "active" if a.mode == "active" and key not in passive_keys else "passive"
        row = Finding(
            source="web",
            tool="zap",
            ruleId=rule,
            asset=a.origin,
            nativeId=content_key([rule, method, path, inst.get("param") or "", actor]),
            evidenceRefs=[reproduce],
        )
        native[row.finding_id] = row
        findings[row.finding_id] = WebFinding(
            findingId=row.finding_id,
            ruleId=rule,
            name=_bounded(alert.get("alert") or alert.get("name") or rule, 256),
            risk=_RISK.get(str(alert.get("riskcode")), "informational"),
            confidence=_bounded(alert.get("confidence") or "unknown", 64),
            phase=phase,
            target=a.origin,
            route=path,
            method=method,
            identity=actor,
            parameter=_bounded(inst.get("param"), 256),
            evidenceExcerpt=_bounded(inst.get("evidence"), 256),
            requestExcerpt=_bounded(inst.get("request-header"), EXCERPT_BYTES),
            responseExcerpt=_bounded(inst.get("response-body"), EXCERPT_BYTES),
            reproduction=f"{reproduce}#alert/{rule}/{index}",
        )
    coverage = WebCoverage(
        discoveredRoutes=sorted(discovered),
        testedRoutes=sorted(tested),
        untestedRoutes=untested,
        unreachableRoutes=sorted(unreachable),
        inaccessibleRoutes=sorted(inaccessible),
        deniedRequests=sorted(denied)[:4096],
        passiveChecks=early_report is not None or final_report is not None,
        activeChecks=(
            a.mode == "active"
            and final_report is not None
            and execution.plan in {"completed", "warnings"}
            and not stopped_early
        ),
        authentication=authentication,
    )
    limits = sorted(set(limits))
    status = "partial" if limits else "complete"
    result = NativeResult(
        execution=execution.state,
        coverage=status,
        findings=list(native.values()),
        examinedAssets=[a.origin] if tested else [],
        unexaminedAssets=[] if tested else [a.origin],
        limitations=limits,
    )
    return WebAssessmentSummary(
        admission=a,
        actionClass=a.action_class,
        execution=execution,
        result=result,
        findings=list(findings.values()),
        coverage=coverage,
        status=status,
        limitations=limits,
        planRef=plan_ref,
        nativeRef=native_ref,
        messagesRef=messages_ref,
    )


def _bounded(value, limit: int) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text.encode()) > limit:
        text = text.encode()[:limit].decode(errors="ignore")
    return text[:1024]


def _authentication(identity, responses: list[dict], challenges: set[str]):
    """The anonymous request comes first; the identity's must then answer.

    ZAP polls the same route to keep the session, so any later failure after
    a successful answer means the session ended during the assessment.
    """
    marker = identity.logged_in_marker

    def answered(response) -> bool:
        status = response.get("status") or 0
        text = str((response.get("content") or {}).get("text") or "")
        return 200 <= status < 300 and (marker is None or marker in text)

    def result(status, reason=None, compared=False):
        return WebAuthentication(
            identity=identity.name,
            method=identity.method,
            status=status,
            anonymousCompared=compared,
            reason=reason,
        )

    unsupported = challenges - {"bearer"} if identity.method == "bearer" else challenges
    if unsupported:
        return result(
            "unsupported", f"unsupported login challenge: {sorted(unsupported)[0]}"
        )
    if len(responses) < 2:
        return result(
            "not_attempted",
            "verification route was not requested anonymously and as the identity",
        )
    anonymous, rest = responses[0], responses[1:]
    # Bearer headers are attached to every request, so its anonymous request
    # is authenticated too and cannot serve as the comparison.
    compared = identity.method == "form"
    first = next((i for i, r in enumerate(rest) if answered(r)), None)
    if first is None:
        return result(
            "failed", "verification route did not answer for the identity", compared
        )
    if compared and answered(anonymous):
        return result("failed", "verification route also answers anonymously", True)
    if not all(answered(r) for r in rest[first + 1 :]):
        return result("failed", "session expired during the assessment", compared)
    return result("verified", None, compared)
