"""ZAP web and API assessment admission, plans and evidence (issue #5044)."""

from __future__ import annotations

import json

import pytest

from moonmind.config.container_backend_settings import (
    EGRESS_POLICIES_ENV_KEY,
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    ContainerJobSpec,
    ContainerJobSubmitRequest,
)
from moonmind.schemas.repository_scanner_models import WebAssessment
from moonmind.security.egress_policy import parse_egress_policies
from moonmind.security.web_assessments import (
    PLAN_JOB_TYPES,
    SECRET_ENV,
    build_web_assessment_job,
    build_web_assessment_plan,
    render_web_assessment_plan,
    web_policy_admits,
)

WORKSPACE = {"kind": "sandbox", "workspaceId": "web"}
ORIGIN = "http://app.example.test:8080"
SECRET = "synthetic-web-secret-5044"


def web(**overrides) -> WebAssessment:
    return WebAssessment.model_validate(
        {
            "mode": "baseline",
            "origin": ORIGIN,
            "routes": [
                {"path": "/", "methods": ["GET"]},
                {"path": "/login", "methods": ["GET", "POST"]},
                {"path": "/api", "methods": ["GET", "POST"]},
            ],
            "exclusions": ["/logout"],
            "egressPolicyRef": "web-target",
            **overrides,
        }
    )


FORM = {
    "name": "alice",
    "method": "form",
    "credentialRef": "secret://web/alice",
    "verificationPath": "/account",
    "loggedInMarker": "Signed in as alice",
    "loginPath": "/login",
    "username": "alice",
}
OPENAPI = {"format": "openapi", "inputArtifactRef": "art_openapi"}


def jobs(plan: dict, kind: str) -> list[dict]:
    return [job for job in plan["jobs"] if job["type"] == kind]


def spec(assessment: WebAssessment, **overrides) -> dict:
    return {
        **build_web_assessment_job(assessment),
        "workspaceRef": WORKSPACE,
        **overrides,
    }


# -- schema ---------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"mode": "active"},  # no strength
        {"activeStrength": "low"},  # strength outside active mode
        {"mode": "api"},  # no definition
        {"apiDefinition": OPENAPI},  # baseline with a definition
        {"origin": "http://10.0.0.1"},
        {"origin": "http://app.example.test/path"},
        {"origin": "ftp://app.example.test"},
        {"startPaths": ["/logout"]},
        {"routes": [{"path": "/api"}]},  # start path "/" is not admitted
        {"exclusions": ["/"]},
        {"routes": [{"path": "/../etc"}]},
        {"identity": {**FORM, "credentialRef": "plain-secret-value"}},
        {"identity": {**FORM, "loggedInMarker": None}},
        {"identity": {**FORM, "verificationPath": "/logout"}},
        {"identity": {**FORM, "loginPath": "/"}},  # GET-only route
        {"discovery": []},
    ],
)
def test_schema_rejects_unscoped_or_ambiguous_assessments(overrides):
    with pytest.raises(ValueError):
        web(**overrides)


def test_each_mode_requires_its_own_action_class():
    assert web().action_class == "web-passive"
    assert web(mode="api", apiDefinition=OPENAPI).action_class == "web-api"
    assert web(mode="active", activeStrength="low").action_class == "web-active"


# -- plan -----------------------------------------------------------------


def test_baseline_and_api_plans_never_run_the_active_scanner():
    for assessment in (web(), web(mode="api", apiDefinition=OPENAPI)):
        plan = build_web_assessment_plan(assessment)
        assert not jobs(plan, "activeScan")
        assert all(not job["parameters"]["postForm"] for job in jobs(plan, "spider"))
    api = build_web_assessment_plan(web(mode="api", apiDefinition=OPENAPI))
    (imported,) = jobs(api, "openapi")
    assert imported["parameters"]["apiFile"] == "/inputs/api-definition/definition.json"
    assert imported["parameters"]["targetUrl"] == ORIGIN


@pytest.mark.parametrize("strength", ["low", "medium", "high"])
def test_active_plan_uses_exactly_the_admitted_strength(strength):
    plan = build_web_assessment_plan(web(mode="active", activeStrength=strength))
    (active,) = jobs(plan, "activeScan")
    assert active["policyDefinition"]["defaultStrength"] == strength.capitalize()
    assert active["parameters"]["threadPerHost"] == 1
    assert active["parameters"]["delayInMs"] == 100  # 10 requests per second
    # Passive evidence is written before any attack payload is sent.
    kinds = [job["type"] for job in plan["jobs"]]
    first_report = kinds.index("report")
    assert first_report < kinds.index("activeScan")
    assert plan["jobs"][first_report]["parameters"]["reportFile"] == (
        "passive-report.json"
    )


def test_plan_runs_only_admitted_zap_jobs_without_templates():
    for assessment in (
        web(identity=FORM, discovery=["spider", "ajax-spider"]),
        web(mode="api", apiDefinition=OPENAPI),
        web(
            mode="active",
            activeStrength="high",
            apiDefinition={
                "format": "graphql",
                "inputArtifactRef": "art_graph",
                "endpointPath": "/api/graphql",
            },
        ),
    ):
        plan = build_web_assessment_plan(assessment)
        assert {job["type"] for job in plan["jobs"]} <= PLAN_JOB_TYPES
        text = render_web_assessment_plan(assessment).decode().lower()
        for forbidden in ("nuclei", "template-catalog", "script", "quickstart"):
            assert forbidden not in text


def test_plan_context_carries_scope_and_is_deterministic():
    assessment = web(identity=FORM, startPaths=["/", "/api"])
    plan = build_web_assessment_plan(assessment)
    (context,) = plan["env"]["contexts"]
    assert context["urls"] == [f"{ORIGIN}/", f"{ORIGIN}/api"]
    assert context["includePaths"] == [
        rf"\Q{ORIGIN}\E(?:[/?#].*)?",
        rf"\Q{ORIGIN}/login\E(?:[/?#].*)?",
        rf"\Q{ORIGIN}/api\E(?:[/?#].*)?",
    ]
    assert context["excludePaths"] == [rf"\Q{ORIGIN}/logout\E(?:[/?#].*)?"]
    assert render_web_assessment_plan(assessment) == render_web_assessment_plan(
        web(identity=FORM, startPaths=["/", "/api"])
    )
    json.loads(render_web_assessment_plan(assessment))


def test_identity_secret_is_a_placeholder_and_authentication_is_verified():
    plan = build_web_assessment_plan(web(identity=FORM))
    (context,) = plan["env"]["contexts"]
    (user,) = context["users"]
    assert user["credentials"]["password"] == "${" + SECRET_ENV + "}"
    assert context["authentication"]["verification"]["loggedInRegex"] == (
        r"\QSigned in as alice\E"
    )
    anonymous, as_identity = jobs(plan, "requestor")
    assert anonymous["requests"] == as_identity["requests"]
    assert anonymous["requests"][0]["url"] == f"{ORIGIN}/account"
    assert "user" not in anonymous.get("parameters", {})
    assert as_identity["parameters"]["user"] == "alice"
    assert all(j["parameters"]["user"] == "alice" for j in jobs(plan, "spider"))


def test_plan_durations_fit_inside_the_job_timeout():
    assessment = web(mode="active", activeStrength="low", timeoutSeconds=300)
    plan = build_web_assessment_plan(assessment)
    minutes = sum(
        job["parameters"].get("maxDuration", 0)
        + job["parameters"].get("maxScanDurationInMins", 0)
        for job in plan["jobs"]
    )
    assert minutes * 60 < build_web_assessment_job(assessment)["timeoutSeconds"]


# -- job --------------------------------------------------------------------


def test_job_runs_the_deployment_zap_image_through_scoped_egress():
    assessment = web(mode="api", apiDefinition=OPENAPI)
    job = ContainerJobSpec.model_validate(spec(assessment))
    assert job.image_source_ref == "web-assessment"
    assert job.network_mode == "bridge" and job.egress_policy_ref == "web-target"
    assert job.environment == [] and job.caches == []
    assert job.command[-2:] == ["/outputs/web-assessment", "-"]
    assert "zap.sh -cmd -autorun" in job.command[1]
    (attachment,) = job.input_attachments
    assert (attachment.name, attachment.artifact_ref) == (
        "api-definition",
        "art_openapi",
    )
    assert [o.relative_path for o in job.outputs] == [
        "web-assessment/report.json",
        "web-assessment/messages.har",
    ]
    # Only active mode has a separate passive phase to keep.
    active = build_web_assessment_job(web(mode="active", activeStrength="low"))
    assert [o["name"] for o in active["outputs"]] == [
        "report",
        "messages",
        "passive-report",
        "passive-messages",
    ]
    # Secrets never travel in argv: the bearer argument names only the site.
    bearer = web(
        identity={
            "name": "svc",
            "method": "bearer",
            "credentialRef": "secret://web/svc",
            "verificationPath": "/api",
        }
    )
    command = build_web_assessment_job(bearer)["command"]
    assert command[-1] == "app.example.test:8080"
    assert SECRET not in json.dumps(command)


@pytest.mark.parametrize(
    "override",
    [
        {"command": ["-c", "zap-full-scan.py -t http://app.example.test:8080"]},
        {"entrypoint": ["zap.sh"]},
        {"environment": [{"name": "MOONMIND_WEB_SECRET", "value": SECRET}]},
        {"networkMode": "none", "egressPolicyRef": None},
        {"caches": [{"cacheRef": "zap", "target": "/home/zap/.ZAP"}]},
        {"timeoutSeconds": 86400},
        {"inputAttachments": None},
        {"inputAttachments": [{"name": "x", "artifactRef": "art_other"}]},
        {"outputs": []},
        {"imageSourceRef": None, "image": "ghcr.io/zaproxy/zaproxy:stable"},
    ],
)
def test_job_rejects_content_its_admission_did_not_render(override):
    with pytest.raises(ValueError):
        ContainerJobSpec.model_validate(spec(web(), **override))


def test_web_assessment_is_its_own_workload():
    with pytest.raises(ValueError):
        ContainerJobSpec.model_validate(
            spec(
                web(),
                workloadProfile={"profileRef": "zap", "mode": "baseline"},
                imageSourceRef=None,
            )
        )
    with pytest.raises(ValueError):
        ContainerJobSpec.model_validate(
            spec(
                web(),
                imageAssessment={
                    "operation": "assess",
                    "target": "source",
                    "databaseCacheRef": "db",
                    "databaseDigest": "a" * 64,
                },
            )
        )


def _policy(destinations):
    return parse_egress_policies(
        json.dumps(
            [
                {
                    "policyRef": "web-target",
                    "allowedWorkflowIds": ["assessment"],
                    "allowedImageSourceRefs": ["web-assessment"],
                    "destinations": destinations,
                }
            ]
        )
    )


TARGET = {
    "host": "app.example.test",
    "purpose": "live-target",
    "protocol": "http",
    "port": 8080,
}


def test_web_egress_reaches_only_admitted_live_targets():
    assessment = web()
    (policy,) = _policy([TARGET])
    assert web_policy_admits(policy, assessment)
    for destinations in (
        [{**TARGET, "port": 443}],
        [{**TARGET, "protocol": "https"}],
        [{**TARGET, "host": "other.example.test"}],
        [TARGET, {**TARGET, "host": "api.github.com", "purpose": "provider-api"}],
    ):
        (policy,) = _policy(destinations)
        assert not web_policy_admits(policy, assessment)


def test_settings_admit_web_egress_only_for_its_target():
    def settings(destinations):
        return resolve_container_backend_settings(
            {
                IMAGE_SOURCES_ENV_KEY: json.dumps(
                    [
                        {
                            "sourceRef": "web-assessment",
                            "image": "ghcr.io/zaproxy/zaproxy:stable",
                        }
                    ]
                ),
                EGRESS_POLICIES_ENV_KEY: json.dumps(
                    [
                        {
                            "policyRef": "web-target",
                            "allowedWorkflowIds": ["assessment"],
                            "allowedImageSourceRefs": ["web-assessment"],
                            "destinations": destinations,
                        }
                    ]
                ),
            }
        )

    request = ContainerJobSubmitRequest(
        idempotencyKey="web",
        source={"source": "workflow", "workflowId": "assessment"},
        spec=spec(web()),
    )
    assert settings([TARGET]).authorize_egress(request).policy_ref == "web-target"
    with pytest.raises(ValueError, match="admitted targets"):
        settings([{**TARGET, "host": "other.example.test"}]).authorize_egress(request)


# -- scope and identity -----------------------------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402

from moonmind.schemas.container_job_models import (  # noqa: E402
    ContainerJobActivityRequest,
    OwnerIdentity,
    WorkloadCredentialAuthorization,
)
from moonmind.security.web_assessments import (  # noqa: E402
    WebEvidenceError,
    check_api_definition,
)
from moonmind.security.workload_credentials import (  # noqa: E402
    authorize_workload_credentials,
    launch_authorizations,
    web_identity_authorization,
)
from moonmind.security.workload_profiles import (  # noqa: E402
    AdmittedAssessmentScope,
    AssessmentTarget,
    resolved_job_within_scope,
    selection_within_scope,
)

NOW = datetime(2026, 10, 28, tzinfo=UTC)
GRANT = {
    "ref": "secret://web/alice",
    "purpose": "application_auth",
    "account": "alice",
    "profileRefs": ["web-assessment"],
    "expiresAt": (NOW + timedelta(hours=2)).isoformat(),
}


def scope(**overrides) -> AdmittedAssessmentScope:
    return AdmittedAssessmentScope.model_validate(
        {
            "assessmentRef": "web-1",
            "profileRefs": ["web-assessment"],
            "actionClasses": ["web-passive", "web-api"],
            "targets": [
                {
                    "webOrigin": ORIGIN,
                    "webRoutes": [
                        {"path": "/", "methods": ["GET"]},
                        {"path": "/login", "methods": ["GET", "POST"]},
                        {"path": "/api", "methods": ["GET", "POST"]},
                    ],
                    "webExclusions": ["/logout"],
                },
                {"artifactRef": "art_openapi"},
            ],
            "networkRefs": ["web-target"],
            "maxJobs": 4,
            "maxJobSeconds": 3600,
            "evidenceIntent": "web assessment",
            "grants": [GRANT],
            **overrides,
        }
    )


def scoped(**overrides) -> ContainerJobSpec:
    return ContainerJobSpec.model_validate(
        spec(web(assessmentRef="web-1", **overrides))
    )


def test_scope_targets_name_exactly_one_kind_with_routes_for_origins():
    with pytest.raises(ValueError):
        AssessmentTarget.model_validate({"webOrigin": ORIGIN})
    with pytest.raises(ValueError):
        AssessmentTarget.model_validate(
            {"workspacePath": ".", "webRoutes": [{"path": "/"}]}
        )
    with pytest.raises(ValueError):
        AssessmentTarget.model_validate(
            {"webOrigin": "http://localhost", "webRoutes": [{"path": "/"}]}
        )


def test_web_assessment_within_admitted_scope_is_accepted():
    job = scoped(mode="api", apiDefinition=OPENAPI)
    resolved_job_within_scope(job, scope(), ())
    assert selection_within_scope(job, scope())


@pytest.mark.parametrize(
    "overrides, scope_overrides, reason",
    [
        ({"mode": "active", "activeStrength": "low"}, {}, "action class"),
        ({"egressPolicyRef": "other-net"}, {}, "network"),
        ({"origin": "http://other.example.test:8080"}, {}, "origin"),
        (
            {"routes": [{"path": "/", "methods": ["GET", "DELETE"]}]},
            {},
            "route or method",
        ),
        ({"exclusions": []}, {}, "exclusion"),
        ({"timeoutSeconds": 3600}, {"maxJobSeconds": 900}, "timeout"),
        ({}, {"profileRefs": ["other"]}, "not admitted in this scope"),
        (
            {"mode": "api", "apiDefinition": {**OPENAPI, "inputArtifactRef": "art_x"}},
            {},
            "API definition",
        ),
    ],
)
def test_web_assessment_outside_scope_is_refused(overrides, scope_overrides, reason):
    job = scoped(**overrides)
    with pytest.raises(PermissionError, match=reason):
        resolved_job_within_scope(job, scope(**scope_overrides), ())
    assert not selection_within_scope(job, scope(**scope_overrides))


def test_web_assessment_for_another_assessment_is_refused():
    job = ContainerJobSpec.model_validate(spec(web(assessmentRef="web-2")))
    with pytest.raises(PermissionError, match="another assessment"):
        resolved_job_within_scope(job, scope(), ())


def test_identity_secret_needs_an_application_auth_grant_for_that_identity():
    job = scoped(identity=FORM)
    (decision,) = authorize_workload_credentials(
        job, scope(), (), principal="user:operator", now=NOW
    )
    assert decision["slot"] == "identity"
    assert decision["profileRef"] == "web-assessment"
    assert decision["account"] == "alice"
    assert SECRET not in json.dumps(decision, default=str)
    for grant, reason in (
        ({**GRANT, "account": "root"}, "identity"),
        ({**GRANT, "profileRefs": ["prowler"]}, "web assessment"),
        ({**GRANT, "expiresAt": (NOW + timedelta(minutes=5)).isoformat()}, "budget"),
        ({**GRANT, "ref": "secret://web/other"}, "not admitted"),
    ):
        with pytest.raises(PermissionError, match=reason):
            authorize_workload_credentials(
                job, scope(grants=[grant]), (), principal="user:operator", now=NOW
            )
    with pytest.raises(PermissionError, match="admitted assessment"):
        authorize_workload_credentials(
            job, None, (), principal="user:operator", now=NOW
        )
    assert (
        authorize_workload_credentials(
            scoped(), scope(), (), principal="user:operator", now=NOW
        )
        == []
    )


def _activity(job: ContainerJobSpec, decisions) -> ContainerJobActivityRequest:
    job_id = "container-job:" + "a" * 32
    return ContainerJobActivityRequest(
        jobId=job_id,
        owner=OwnerIdentity(principalType="user", principalId="operator"),
        ownershipToken=f"{job_id}:v1",
        request=ContainerJobSubmitRequest(
            idempotencyKey="web",
            source={"source": "workflow", "workflowId": "assessment"},
            spec=job.model_dump(by_alias=True, exclude_none=True),
        ),
        workloadCredentials=tuple(
            WorkloadCredentialAuthorization.model_validate({**item, "jobId": job_id})
            for item in decisions
        )
        or None,
    )


def test_worker_rechecks_the_identity_decision_before_delivery():
    job = scoped(identity=FORM)
    decisions = authorize_workload_credentials(
        job, scope(), (), principal="user:operator", now=NOW
    )
    request = _activity(job, decisions)
    assert web_identity_authorization(request, now=NOW).credential_ref == (
        "secret://web/alice"
    )
    # Profile slot delivery never handles the web identity.
    assert launch_authorizations(request, (), now=NOW) == []
    with pytest.raises(TimeoutError):
        web_identity_authorization(request, now=NOW + timedelta(hours=3))
    other = [{**decisions[0], "account": "root"}]
    with pytest.raises(PermissionError):
        web_identity_authorization(_activity(job, other), now=NOW)
    with pytest.raises(PermissionError):
        web_identity_authorization(_activity(job, []), now=NOW)
    with pytest.raises(PermissionError):
        web_identity_authorization(_activity(scoped(), decisions), now=NOW)


# -- API definitions --------------------------------------------------------


def _openapi(servers, paths):
    return json.dumps({"openapi": "3.0.3", "servers": servers, "paths": paths}).encode()


def test_api_definition_must_describe_only_admitted_routes():
    api = web(mode="api", apiDefinition=OPENAPI)
    get = {"get": {"responses": {}}}
    check_api_definition(api, _openapi([{"url": "/"}], {"/api/items": get}))
    check_api_definition(
        api, _openapi([{"url": ORIGIN + "/api"}], {"/items/{id}": get})
    )
    for body, code in (
        (
            _openapi([{"url": "http://other.example.test"}], {"/api": get}),
            "out_of_scope",
        ),
        (_openapi([{"url": "/"}], {"/search": {"post": {}}}), "out_of_scope"),
        (_openapi([{"url": "/"}], {"/api": {"delete": {}}}), "out_of_scope"),
        (_openapi([{"url": "/"}], {"/logout": get}), "out_of_scope"),
        (_openapi([{"url": "{scheme}://evil"}], {"/api": get}), "out_of_scope"),
        (b"openapi: 3.0.3", "malformed"),
        (_openapi([{"url": "/"}], {}), "malformed"),
    ):
        with pytest.raises(WebEvidenceError, match=code):
            check_api_definition(api, body)
    swagger = json.dumps(
        {"swagger": "2.0", "host": "evil.example.test", "paths": {"/api": get}}
    ).encode()
    with pytest.raises(WebEvidenceError, match="out_of_scope"):
        check_api_definition(api, swagger)


# -- evidence ---------------------------------------------------------------

from moonmind.security.web_assessments import (  # noqa: E402
    interpret_exit,
    sanitize_messages,
    sanitize_report,
    summarize_web_assessment,
)

LIMIT = 8 * 1024 * 1024


def alert(plugin, uri, method="GET", *, risk="1", param=""):
    # The traditional-json-plus shape ZAP 2.17 writes.
    return {
        "pluginid": plugin,
        "alert": f"rule {plugin}",
        "riskcode": risk,
        "confidence": "2",
        "instances": [
            {
                "uri": uri,
                "method": method,
                "param": param,
                "evidence": "<p>",
                "request-header": (
                    f"{method} {uri} HTTP/1.1\r\nAuthorization: Bearer {SECRET}\r\n"
                    f"Cookie: sid={SECRET}\r\n"
                ),
                "response-header": f"HTTP/1.1 200 OK\r\nSet-Cookie: sid={SECRET}\r\n",
                "response-body": f"echo {SECRET} " + "x" * 5000,
            }
        ],
    }


def report(*alerts, **extra) -> bytes:
    return json.dumps(
        {
            "@programName": "ZAP",
            "@version": "2.17.0",
            "site": [{"@name": ORIGIN, "alerts": list(alerts)}],
            "afPlanErrors": [],
            "afPlanWarns": [],
            **extra,
        }
    ).encode()


def entry(path, status=200, *, method="GET", origin=ORIGIN, text="", headers=()):
    return {
        "request": {
            "method": method,
            "url": origin + path,
            "headers": [
                {"name": "host", "value": "app.example.test"},
                {"name": "Cookie", "value": f"sid={SECRET}"},
            ],
            "cookies": [{"name": "sid", "value": SECRET}],
            "postData": (
                {
                    "mimeType": "application/x-www-form-urlencoded",
                    "text": f"username=alice&password={SECRET}",
                    "params": [{"name": "password", "value": SECRET}],
                }
                if method == "POST"
                else {"mimeType": "", "text": ""}
            ),
        },
        "response": {
            "status": status,
            "headers": [{"name": k, "value": v} for k, v in headers],
            "content": {"text": text},
        },
    }


def har(*entries) -> bytes:
    return json.dumps({"log": {"version": "1.2", "entries": list(entries)}}).encode()


EVERY_ROUTE = (entry("/"), entry("/login"), entry("/api/items"))


def summarize(assessment, *, exit_code=0, terminal="succeeded", **files):
    files = {
        key: (
            (sanitize_report if "report" in key else sanitize_messages)(
                value, [SECRET], LIMIT
            )
            if value is not None
            else None
        )
        for key, value in files.items()
    }
    return summarize_web_assessment(
        assessment,
        report=files.get("report"),
        messages=files.get("messages"),
        passive_report=files.get("passive_report"),
        passive_messages=files.get("passive_messages"),
        exit_code=exit_code,
        terminal_state=terminal,
        native_ref="art_report",
        messages_ref="art_messages",
        plan_ref="art_plan",
    )


def test_exit_codes_separate_plan_outcomes_from_job_failure():
    assert interpret_exit(0, "succeeded").plan == "completed"
    # ZAP reports plan errors and warnings with exit codes 1 and 2; the
    # container itself finished.
    for code, plan in ((1, "errors"), (2, "warnings")):
        execution = interpret_exit(code, "failed")
        assert (execution.state, execution.plan) == ("succeeded", plan)
    for code in (126, 137, None):
        execution = interpret_exit(code, "failed")
        assert (execution.state, execution.plan) == ("failed", "not_completed")
    for terminal in ("timed_out", "canceled"):
        execution = interpret_exit(0, terminal)
        assert (execution.state, execution.plan) == (terminal, "not_completed")


def test_complete_baseline_attributes_findings_and_redacts_secrets():
    summary = summarize(
        web(),
        report=report(alert("10021", f"{ORIGIN}/"), alert("10038", f"{ORIGIN}/login")),
        messages=har(*EVERY_ROUTE),
    )
    assert summary.status == "complete" and summary.limitations == []
    assert summary.execution.version == "2.17.0"
    assert summary.coverage.tested_routes == ["GET /", "GET /api/items", "GET /login"]
    assert summary.coverage.passive_checks and not summary.coverage.active_checks
    first = summary.findings[0]
    assert (first.rule_id, first.route, first.method, first.identity, first.phase) == (
        "10021",
        "/",
        "GET",
        "anonymous",
        "passive",
    )
    assert first.target == ORIGIN
    assert first.reproduction.startswith("art_report#alert/10021/")
    assert {f.finding_id for f in summary.result.findings} == {
        f.finding_id for f in summary.findings
    }
    # Findings are evidence only and go to review Skills.
    assert summary.target_actions == "none"
    assert summary.review_skills == ["security-investigate", "security-remediate"]
    text = summary.model_dump_json(by_alias=True)
    assert SECRET not in text
    assert len(first.response_excerpt.encode()) <= 1024


def test_sanitizers_remove_credentials_sessions_and_login_bodies():
    raw = har(entry("/login", 302, method="POST"), entry("/account", text=SECRET))
    safe = sanitize_messages(raw, [SECRET], LIMIT)
    assert SECRET.encode() not in safe
    login = json.loads(safe)["log"]["entries"][0]["request"]
    assert login["postData"]["text"] == "[REDACTED]"
    assert {h["value"] for h in login["headers"] if h["name"] == "Cookie"} == {
        "[REDACTED]"
    }
    native = sanitize_report(report(alert("1", f"{ORIGIN}/")), [SECRET], LIMIT)
    assert SECRET.encode() not in native
    instance = json.loads(native)["site"][0]["alerts"][0]["instances"][0]
    assert "Authorization: [REDACTED]" in instance["request-header"]
    assert "Set-Cookie: [REDACTED]" in instance["response-header"]


def test_denied_redirects_and_unreachable_routes_cannot_report_clean():
    summary = summarize(
        web(),
        report=report(),
        messages=har(
            *EVERY_ROUTE,
            entry("/away", 302, headers=[("Location", "http://outside.example.test/")]),
            entry("/gone", 502),
            entry("/", origin="http://outside.example.test", status=403),
        ),
    )
    assert summary.status == "partial"
    assert "out_of_scope_requests_denied" in summary.limitations
    assert "routes_unreachable" in summary.limitations
    assert "redirect GET /away -> http://outside.example.test/" in (
        summary.coverage.denied_requests
    )
    assert (
        "off-origin GET http://outside.example.test" in summary.coverage.denied_requests
    )
    assert summary.coverage.unreachable_routes == ["GET /gone"]


def test_incomplete_crawl_is_partial_with_the_untested_routes():
    summary = summarize(web(), report=report(), messages=har(entry("/")))
    assert summary.status == "partial"
    assert summary.coverage.untested_routes == ["/login", "/api"]
    assert "admitted_routes_untested" in summary.limitations
    nothing = summarize(web(), report=report(), messages=har())
    assert {"no_routes_tested", "admitted_routes_untested"} <= set(nothing.limitations)


@pytest.mark.parametrize(
    "files, limitation",
    [
        ({"report": b"{not json", "messages": har(*EVERY_ROUTE)}, "report_malformed"),
        ({"report": report(), "messages": None}, "messages_unavailable"),
        ({"report": None, "messages": har(*EVERY_ROUTE)}, "final_report_unavailable"),
    ],
)
def test_missing_or_malformed_output_is_partial(files, limitation):
    if files["report"] == b"{not json":
        summary = summarize_web_assessment(
            web(),
            report=files["report"],
            messages=sanitize_messages(files["messages"], [], LIMIT),
            passive_report=None,
            passive_messages=None,
            exit_code=0,
            terminal_state="succeeded",
        )
    else:
        summary = summarize(web(), **files)
    assert summary.status == "partial" and limitation in summary.limitations


def test_plan_errors_warnings_and_early_stops_are_partial():
    for extra, limitation in (
        ({"afPlanErrors": ["spider failed"]}, "zap_plan_errors"),
        ({"afPlanWarns": ["no urls"]}, "zap_plan_warnings"),
        (
            {"stoppingInsight": {"key": "insight.auth.failure"}},
            "zap_stopped:insight.auth.failure",
        ),
    ):
        summary = summarize(web(), report=report(**extra), messages=har(*EVERY_ROUTE))
        assert limitation in summary.limitations and summary.status == "partial"
    warned = summarize(web(), exit_code=2, report=report(), messages=har(*EVERY_ROUTE))
    assert warned.execution.plan == "warnings" and warned.status == "partial"


def test_active_findings_are_separated_from_the_passive_phase():
    assessment = web(mode="active", activeStrength="low")
    passive = report(alert("10021", f"{ORIGIN}/"))
    summary = summarize(
        assessment,
        passive_report=passive,
        passive_messages=har(*EVERY_ROUTE),
        report=report(
            alert("10021", f"{ORIGIN}/"),
            alert("40012", f"{ORIGIN}/api/items", risk="3", param="name"),
        ),
        messages=har(*EVERY_ROUTE),
    )
    assert summary.status == "complete" and summary.coverage.active_checks
    phases = {(f.rule_id, f.phase) for f in summary.findings}
    assert phases == {("10021", "passive"), ("40012", "active")}


def test_timeout_during_active_testing_keeps_the_passive_phase():
    summary = summarize(
        web(mode="active", activeStrength="medium"),
        terminal="timed_out",
        exit_code=None,
        passive_report=report(alert("10021", f"{ORIGIN}/")),
        passive_messages=har(*EVERY_ROUTE),
    )
    assert summary.status == "partial"
    assert summary.execution.state == "timed_out"
    assert {"container_job_timed_out", "final_report_unavailable"} <= set(
        summary.limitations
    )
    assert not summary.coverage.active_checks and summary.coverage.passive_checks
    assert [f.phase for f in summary.findings] == ["passive"]
    assert summary.coverage.tested_routes


def _identity_run(*responses, identity=FORM, challenge=None):
    verify = [
        entry("/account", status, text=text, headers=headers)
        for status, text, headers in responses
    ]
    if challenge:
        verify.append(
            entry("/api/items", 401, headers=[("WWW-Authenticate", challenge)])
        )
    return summarize(
        web(identity=identity),
        report=report(),
        messages=har(*verify, *EVERY_ROUTE),
    )


ANON = (302, "", [("Location", "/login")])
SIGNED_IN = (200, "<p>Signed in as alice</p>", [])


def test_authentication_is_verified_against_anonymous_access():
    summary = _identity_run(ANON, (302, "", []), SIGNED_IN, SIGNED_IN)
    auth = summary.coverage.authentication
    assert (auth.status, auth.anonymous_compared) == ("verified", True)
    assert summary.status == "complete"
    assert all(f.identity == "alice" for f in summary.findings)


@pytest.mark.parametrize(
    "responses, reason",
    [
        ((ANON, ANON), "did not answer for the identity"),
        ((SIGNED_IN, SIGNED_IN), "also answers anonymously"),
        ((ANON, SIGNED_IN, ANON), "session expired"),
        ((ANON,), "not requested"),
    ],
)
def test_failed_or_unproven_authentication_is_partial_with_a_reason(responses, reason):
    summary = _identity_run(*responses)
    auth = summary.coverage.authentication
    assert auth.status in {"failed", "not_attempted"} and reason in auth.reason
    assert summary.status == "partial"


def test_unsupported_login_challenges_and_inaccessible_routes_are_explicit():
    summary = _identity_run(ANON, SIGNED_IN, challenge="Negotiate")
    auth = summary.coverage.authentication
    assert auth.status == "unsupported" and "negotiate" in auth.reason
    assert summary.coverage.inaccessible_routes == ["GET /api/items"]
    assert {"authentication_unsupported", "routes_inaccessible"} <= set(
        summary.limitations
    )


def test_unsent_site_tree_nodes_are_not_unreachable_or_failed_logins():
    # ZAP exports folder nodes and unsent placeholders with status 0.
    summary = _identity_run(ANON, SIGNED_IN, SIGNED_IN, (0, "", []))
    assert summary.coverage.authentication.status == "verified"
    folder = summarize(
        web(),
        report=report(),
        messages=har(*EVERY_ROUTE, entry("/api", 0), entry("/api/items/7", 200)),
    )
    assert folder.coverage.unreachable_routes == []
    assert folder.status == "complete"
