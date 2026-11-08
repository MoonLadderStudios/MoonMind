"""Curated request plans and truthful per-template observations."""

import json

import pytest

from moonmind.schemas.repository_scanner_models import WebAssessment
from moonmind.security.web_assessments import (
    build_web_assessment_plan,
    summarize_web_assessment,
)

TEMPLATE = "nextjs-middleware-bypass"
ORIGIN = "https://app.example.test"


def admission(**changes):
    return WebAssessment.model_validate(
        {
            "mode": "known-issues",
            "origin": ORIGIN,
            "routes": [{"path": "/admin", "methods": ["GET"]}],
            "startPaths": ["/admin"],
            "discovery": [],
            "templateIds": [TEMPLATE],
            "egressPolicyRef": "web-target",
            **changes,
        }
    )


def test_curated_mode_emits_only_fixed_request_pairs():
    a = admission()
    assert a.action_class == "web-known-issue"
    plan = build_web_assessment_plan(a)
    assert not {"spider", "spiderAjax", "activeScan", "openapi"} & {
        j["type"] for j in plan["jobs"]
    }
    requests = [
        r for j in plan["jobs"] if j["type"] == "requestor" for r in j["requests"]
    ]
    assert len(requests) == 2
    assert {(r["url"], r["method"]) for r in requests} == {(ORIGIN + "/admin", "GET")}
    assert all(not r.get("data") for r in requests)
    assert "X-Middleware-Subrequest:" in "\n".join(requests[1]["headers"])


@pytest.mark.parametrize(
    "changes",
    [
        {"templateIds": []},
        {"templateIds": ["https://evil.example/template"]},
        {"templateIds": [TEMPLATE, TEMPLATE]},
        {"discovery": ["spider"]},
        {"startPaths": ["/outside"]},
        {"activeStrength": "low"},
        {"mode": "baseline"},
        {"templates": [{"code": "anything"}]},
    ],
)
def test_curated_mode_refuses_unbounded_choices(changes):
    with pytest.raises(ValueError):
        admission(**changes)


def native_entries(probe_status=200):
    requests = [
        r
        for j in build_web_assessment_plan(admission())["jobs"]
        if j["type"] == "requestor"
        for r in j["requests"]
    ]
    rows = []
    for index, request in enumerate(requests):
        status = 307 if index == 0 else probe_status
        headers = (
            [{"name": "x-nextjs-redirect", "value": "/login"}] if status == 307 else []
        )
        rows.append(
            {
                "request": {
                    "url": request["url"],
                    "method": request["method"],
                    "headers": [
                        {"name": h.split(":", 1)[0], "value": h.split(":", 1)[1]}
                        for h in request["headers"]
                    ],
                },
                "response": {
                    "status": status,
                    "headers": headers,
                    "content": {"text": "login" if status == 307 else "protected page"},
                },
            }
        )
    return rows


def summarize(entries, **kwargs):
    return summarize_web_assessment(
        admission(),
        report=b'{"@version":"2.17.0","site":[]}',
        messages=json.dumps({"log": {"entries": entries}}).encode(),
        passive_report=None,
        passive_messages=None,
        exit_code=0,
        terminal_state="succeeded",
        native_ref="art_report",
        messages_ref="art_messages",
        **kwargs
    )


@pytest.mark.parametrize(
    "status,outcome",
    [(200, "match"), (307, "negative"), (403, "incomplete"), (500, "incomplete")],
)
def test_known_issue_outcomes_require_the_request_pair(status, outcome):
    result = summarize(native_entries(status))
    assert result.coverage.templates[0].outcome == outcome
    assert result.status == ("partial" if outcome == "incomplete" else "complete")
    assert bool(result.result.findings) == (outcome == "match")
    assert result.verification == "not_performed"
    assert result.template_provenance[0].sha256
    if result.findings:
        assert result.result.findings[0].evidence_refs == ["art_messages"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "reversed",
        "duplicate",
        "bad_method",
        "wrong_origin",
        "query",
        "cookie",
        "no_baseline",
        "malformed_status",
    ],
)
def test_incomplete_or_confounded_native_evidence_cannot_be_negative(mutation):
    rows = native_entries(307)
    if mutation == "missing":
        rows.pop()
    if mutation == "reversed":
        rows.reverse()
    if mutation == "duplicate":
        rows.append(rows[-1])
    if mutation == "bad_method":
        rows[1]["request"]["method"] = "POST"
    if mutation == "wrong_origin":
        rows[1]["request"]["url"] = "https://outside.example/admin"
    if mutation == "query":
        rows[1]["request"]["url"] += "?redirect=evil"
    if mutation == "cookie":
        rows[1]["request"]["headers"].append({"name": "Cookie", "value": "[REDACTED]"})
    if mutation == "no_baseline":
        rows[0]["response"]["status"] = 200
    if mutation == "malformed_status":
        rows[1]["response"]["status"] = "307"
    result = summarize(rows)
    assert result.coverage.templates[0].outcome == "incomplete"
    assert result.status == "partial"


def test_known_issue_action_and_credentials_remain_independently_admitted():
    from moonmind.security.workload_profiles import resolved_job_within_scope
    from tests.unit.security.test_web_assessment import FORM, scope, scoped

    job = scoped(mode="known-issues", templateIds=[TEMPLATE], discovery=[])
    with pytest.raises(PermissionError, match="action class"):
        resolved_job_within_scope(job, scope(), ())
    resolved_job_within_scope(job, scope(actionClasses=["web-known-issue"]), ())
    with pytest.raises(ValueError, match="anonymous"):
        admission(identity=FORM, routes=[{"path": "/", "methods": ["GET", "POST"]}])


@pytest.mark.parametrize(
    "state,code",
    [("timed_out", None), ("canceled", None), ("failed", 1), ("succeeded", 2)],
)
def test_interrupted_template_cannot_claim_complete_negative(state, code):
    result = summarize_web_assessment(
        admission(),
        report=b'{"@version":"2.17.0","site":[]}',
        messages=json.dumps({"log": {"entries": native_entries(307)}}).encode(),
        passive_report=None,
        passive_messages=None,
        exit_code=code,
        terminal_state=state,
        native_ref="art_report",
        messages_ref="art_messages",
    )
    assert result.status == "partial"
    assert result.coverage.templates[0].outcome == "incomplete"


def test_template_updates_change_comparison_identity_without_engine_version_pinning():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from moonmind.security.assessments import AssessmentScope
    from moonmind.workflows.skills.security_assessment import _web_observation

    summary = summarize(native_entries(307))
    request = SimpleNamespace(scope=AssessmentScope(assets=[ORIGIN]))

    def config(s):
        return (
            _web_observation(request, s, "art_summary", datetime.now(UTC))
            .coverage[0]
            .config_ref
        )

    original = config(summary)
    updated = summary.model_copy(deep=True)
    updated.template_provenance[0].sha256 = "f" * 64
    assert config(updated) != original
    updated = summary.model_copy(deep=True)
    updated.execution.version = "99.0.0"
    assert config(updated) == original


@pytest.mark.parametrize(
    "value", ["http://[broken/login", "https://outside.example/login"]
)
def test_malformed_or_external_middleware_location_is_incomplete(value):
    rows = native_entries(307)
    rows[0]["response"]["headers"][0]["value"] = value
    assert summarize(rows).coverage.templates[0].outcome == "incomplete"


def test_native_null_post_data_is_not_a_request_body():
    rows = native_entries(307)
    for row in rows:
        row["request"]["postData"] = None
    assert summarize(rows).coverage.templates[0].outcome == "negative"


def test_encoded_content_does_not_establish_a_match():
    rows = native_entries(200)
    rows[1]["response"]["content"]["encoding"] = "base64"
    assert summarize(rows).coverage.templates[0].outcome == "incomplete"


@pytest.mark.parametrize(
    "name",
    ["x-middleware-rewrite", "x-nextjs-redirect", "x-nextjs-rewrite", "location"],
)
def test_probe_with_preserved_enforcement_header_is_not_a_match(name):
    rows = native_entries(200)
    rows[1]["response"]["headers"] = [{"name": name, "value": "/login"}]
    rows[1]["response"]["content"][
        "text"
    ] = '<html><form action="/login">Sign in</form></html>'
    result = summarize(rows)
    assert result.coverage.templates[0].outcome == "incomplete"
    assert not result.result.findings


def test_sender_guard_enforces_recipe_headers_and_request_count():
    import shutil
    import subprocess

    from moonmind.security.known_issue_templates import SUBREQUEST

    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable; real script is qualified by upstream-image CI")
    guard = build_web_assessment_plan(admission())["jobs"][0]["parameters"]["inline"]
    program = (
        """
class FakeURI {
  constructor(value) { this.value = value; this.url = new URL(value); }
  toString() { return this.value; }
  getHost() { return this.url.hostname; }
  getEscapedPath() { return this.url.pathname; }
}
var Java = {type: () => FakeURI};
"""
        + guard
        + """
const rows = JSON.parse(process.argv[1]);
const result = [];
for (const row of rows) {
  const headers = row.headers;
  let uri = new URI(row.url);
  const header = {
    getURI: () => uri, setURI: (value) => {uri = value;},
    getMethod: () => row.method,
    getHeader: (name) => headers[name.toLowerCase()] ?? null,
    setHeader: (name, value) => { if (value === null) delete headers[name.toLowerCase()]; else headers[name.toLowerCase()] = value; },
  };
  sendingRequest({getRequestHeader: () => header, getRequestBody: () => ({length: () => 0})}, 0, {});
  result.push({url: uri.toString(), headers});
}
process.stdout.write(JSON.stringify(result));
"""
    )

    def request(**headers):
        return {
            "url": ORIGIN + "/admin",
            "method": "GET",
            "headers": {"x-nextjs-data": "1", **headers},
        }

    rows = [
        request(**{"x-middleware-subrequest": "unadmitted"}),
        request(cookie="secret", authorization="secret"),
        request(**{"x-middleware-subrequest": SUBREQUEST}),
        request(),
    ]
    result = subprocess.run(
        [node, "-e", program, json.dumps(rows)],
        capture_output=True,
        text=True,
        check=True,
    )
    guarded = json.loads(result.stdout)
    assert "request-denied.moonmind.invalid" in guarded[0]["url"]
    assert guarded[1] == {"url": ORIGIN + "/admin", "headers": {"x-nextjs-data": "1"}}
    assert guarded[2]["url"] == ORIGIN + "/admin"
    assert "request-denied.moonmind.invalid" in guarded[3]["url"]


@pytest.mark.parametrize("status,outcome", [(200, "match"), (307, "negative")])
def test_repeated_nonmatcher_response_headers_are_valid(status, outcome):
    rows = native_entries(status)
    for row in rows:
        row["response"]["headers"].extend(
            [
                {"name": "Set-Cookie", "value": "[REDACTED]"},
                {"name": "Set-Cookie", "value": "[REDACTED]"},
                {"name": "Vary", "value": "Accept"},
                {"name": "Vary", "value": "Accept-Encoding"},
            ]
        )
    assert summarize(rows).coverage.templates[0].outcome == outcome


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("bad_status", [200, 403])
def test_per_route_negative_is_independent_of_other_route_failure(reverse, bad_status):
    paths = ["/public", "/fixed"]
    if reverse:
        paths.reverse()
    entries = []
    for path in paths:
        rows = native_entries(307)
        for row in rows:
            row["request"]["url"] = ORIGIN + path
        if path == "/public":
            for row in rows:
                row["response"]["status"] = bad_status
        entries.extend(rows)
    result = summarize_web_assessment(
        admission(routes=[{"path": "/", "methods": ["GET"]}], startPaths=paths),
        report=b'{"@version":"2.17.0","site":[]}',
        messages=json.dumps({"log": {"entries": entries}}).encode(),
        passive_report=None,
        passive_messages=None,
        exit_code=0,
        terminal_state="succeeded",
        native_ref="art_report",
        messages_ref="art_messages",
    )
    assert result.status == "partial"
    assert {row.route: row.outcome for row in result.coverage.templates} == {
        "/public": "incomplete",
        "/fixed": "negative",
    }


@pytest.mark.parametrize(
    "name", ["x-nextjs-redirect", "x-middleware-rewrite", "location"]
)
def test_ambiguous_repeated_matcher_headers_still_refuse_coverage(name):
    rows = native_entries(200)
    rows[0]["response"]["headers"].extend(
        [{"name": name, "value": "/login"}, {"name": name, "value": "/different"}]
    )
    assert summarize(rows).coverage.templates[0].outcome == "incomplete"
