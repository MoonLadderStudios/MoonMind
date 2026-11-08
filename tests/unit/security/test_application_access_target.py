"""Real HTTP proxy fixtures: policy is implemented independently of assertions."""

import asyncio
import itertools
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from test_application_access import spec

from moonmind.security.application_access import (
    ApplicationAccessSpec,
    assess_application_access,
)
from moonmind.security.application_access_target import (
    ApplicationAccessCapture,
    observe_application_access,
)
from moonmind.security.target_assessments import ScopedTargetClient, TargetScope


def scope(origin, requests=40):
    return TargetScope.model_validate(
        {
            "web": {
                "origin": "http://fixture.test",
                "routes": [
                    {"path": p, "methods": ["GET", "POST"]}
                    for p in ["/identity", "/record", "/admin"]
                ],
                "start_paths": ["/identity"],
                "mode": "active",
                "active_strength": "low",
                "egress_policy_ref": "fixture",
                "max_requests_per_second": 100,
                "timeout_seconds": 300,
            },
            "max_requests": requests,
            "credential_slots": {"slot://" + i: i for i in ["owner", "other", "admin"]},
        }
    )


@pytest.fixture
def target():
    state = {
        "vulnerable": False,
        "leak": False,
        "login": False,
        "malformed": False,
        "wrong": False,
        "expired": False,
        "drop": False,
        "redirect": False,
        "missing": False,
        "version": 1,
        "mutation": None,
        "calls": [],
        "effects": 0,
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            path = urlsplit(self.path).path
            identity = (
                self.headers.get("Authorization", "")
                .removeprefix("Bearer ")
                .removeprefix("token-")
                or "anon"
            )
            state["calls"].append((self.command, path, identity, time.monotonic()))
            status, payload = 200, {}
            if path == "/identity":
                payload = {
                    "anonymous": identity == "anon",
                    "identity": "wrong" if state["wrong"] else identity,
                }
                if state["expired"]:
                    status, payload = 401, {"error": "expired"}
                if (
                    state.get("post_identity_failed")
                    and sum(c[1] == "/identity" for c in state["calls"]) > 1
                ):
                    status = 401  # Matching JSON is not authenticated success.
            elif path == "/record/state":
                if identity != "owner":
                    status, payload = 403, {"error": "forbidden"}
                else:
                    payload = {"mutation": state["mutation"]}
                    if not state["missing"]:
                        payload["version"] = state["version"]
            else:
                # This fixture's access policy has no access to the assessment spec:
                # record owner/admin may read; only admin may use privileged route.
                allowed = identity == "admin" or (
                    identity == "owner" and path == "/record"
                )
                if state["vulnerable"]:
                    allowed = True
                if self.command == "POST":
                    body = json.loads(
                        self.rfile.read(int(self.headers["Content-Length"]))
                    )
                    if allowed:
                        state["mutation"] = body["mutation"]
                        state["version"] += 1
                        state["effects"] += 1
                    if state["drop"] is True or state["drop"] == body["mutation"]:
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                status, payload = (
                    (200, {"record": {"id": "record", "secret": "PRIVATE_MARKER"}})
                    if allowed
                    else (403, {"error": "forbidden"})
                )
                if state["leak"]:
                    payload["record"] = {"secret": "PRIVATE_MARKER"}
                if state["login"]:
                    status, payload = 200, {"error": "login"}
            if state["redirect"] and path == "/record":
                self.send_response(302)
                self.send_header("Location", "/outside")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = (
                b"not-json"
                if state["malformed"] and path == "/record"
                else json.dumps(payload).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["origin"] = "http://127.0.0.1:" + str(server.server_port)
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


def selected(identity="anon", *, mutate=False, privileged=False):
    data = spec(mutating=mutate).model_dump(mode="json")
    for name in ["other", "admin"]:
        data["authorization_matrix"]["identities"].append(
            {
                "id": name,
                "account_ref": name,
                "session_ref": name,
                "integration_ref": "app",
                "credential_ref": "slot://" + name,
            }
        )
    case, access = data["cases"][0], data["authorization_matrix"]["cases"][0]
    access["identity_ref"] = identity
    access["expected"] = (
        "allow"
        if identity == "admin" or (identity == "owner" and not privileged)
        else "deny"
    )
    for request in [case["request"], case["identity_check"]]:
        request["credential_ref"] = None if identity == "anon" else "slot://" + identity
    case["identity_assertions"] = [
        {"id": "identity", "path": "identity", "value": identity}
    ]
    if privileged:
        case["request"]["path"] = "/admin"
    if mutate and access["expected"] == "allow":
        case["state_assertions"][0]["operator"] = "changed"
    return ApplicationAccessSpec.model_validate(data)


def client(target, tmp_path, **kwargs):
    for name in ["owner", "other", "admin"]:
        (tmp_path / name).write_text("token-" + name)
    admitted = scope(target["origin"], kwargs.pop("requests", 40))
    return ScopedTargetClient(
        admitted,
        proxy=target["origin"],
        credential_root=tmp_path,
        max_seconds=20,
        **kwargs,
    )


def observe(s, c, path):
    return asyncio.run(observe_application_access(s, c, checkpoint_path=path))


@pytest.mark.parametrize(
    "identity,privileged",
    [
        ("anon", False),
        ("owner", False),
        ("other", False),
        ("admin", True),
        ("owner", True),
    ],
)
@pytest.mark.parametrize("vulnerable", [False, True])
def test_independent_policy(target, tmp_path, identity, privileged, vulnerable):
    target["vulnerable"] = vulnerable
    s = selected(identity, privileged=privileged)
    capture = observe(s, client(target, tmp_path), tmp_path / "capture")
    result = assess_application_access(s, capture.bundle).cases[0]
    denied = s.authorization_matrix.cases[0].expected == "deny"
    assert result.outcome == ("finding" if vulnerable and denied else "pass")
    assert capture.requests_spent == 3
    assert "PRIVATE_MARKER" not in (tmp_path / "capture").read_text()
    assert len(target["calls"]) == 3


@pytest.mark.parametrize(
    "flag,outcome",
    [
        ("login", "inconclusive"),
        ("leak", "finding"),
        ("wrong", "inconclusive"),
        ("expired", "inconclusive"),
        ("malformed", "inconclusive"),
        ("redirect", "inconclusive"),
    ],
)
def test_untrusted_responses(target, tmp_path, flag, outcome):
    target[flag] = True
    s = selected()
    capture = observe(s, client(target, tmp_path), tmp_path / "capture")
    assert assess_application_access(s, capture.bundle).cases[0].outcome == outcome
    assert all(path != "/outside" for _, path, _, _ in target["calls"])
    if flag in {"wrong", "expired"}:
        assert len(target["calls"]) == 1
    assert "PRIVATE_MARKER" not in capture.model_dump_json()


@pytest.mark.parametrize("vulnerable", [False, True])
def test_denied_mutation_state(target, tmp_path, vulnerable):
    target["vulnerable"] = vulnerable
    s = selected(mutate=True)
    capture = observe(s, client(target, tmp_path), tmp_path / "capture")
    assert assess_application_access(s, capture.bundle).cases[0].outcome == (
        "finding" if vulnerable else "pass"
    )
    assert capture.requests_spent == 5
    assert capture.bundle.observations[0].receipt_ref == (
        "mutation-1" if vulnerable else None
    )


def test_drop_and_new_client_resume_never_replays(target, tmp_path):
    target.update(vulnerable=True, drop=True)
    s = selected(mutate=True)
    path = tmp_path / "capture"
    first = observe(s, client(target, tmp_path), path)
    assert target["effects"] == 1
    assert first.progress[0].phase == "attempted"
    target["drop"] = False
    recovered = observe(s, client(target, tmp_path), path)
    assert target["effects"] == 1
    assert len([c for c in target["calls"] if c[0] == "POST"]) == 1
    assert recovered.bundle.observations[0].receipt_ref == "mutation-1"
    assert (
        recovered.bundle.observations[0].attempt_ref
        == first.bundle.observations[0].attempt_ref
    )
    assert recovered.started_at == first.started_at
    assert recovered.requests_spent == 6
    report = assess_application_access(s, recovered.bundle)
    assert (report.status, report.outcome) == ("partial", "violations")
    assert {"response_incomplete", "effect_uncertain"} <= set(report.cases[0].reasons)
    assert ApplicationAccessCapture.model_validate_json(path.read_bytes()) == recovered


def test_receipt_without_capture_blocks_replay(target, tmp_path):
    target["mutation"] = "mutation-1"
    s = selected(mutate=True)
    capture = observe(s, client(target, tmp_path), tmp_path / "capture")
    assert not any(c[0] == "POST" for c in target["calls"])
    assert assess_application_access(s, capture.bundle).outcome == "incomplete"


def test_missing_state_prevents_effect(target, tmp_path):
    target["missing"] = True
    s = selected(mutate=True)
    capture = observe(s, client(target, tmp_path), tmp_path / "capture")
    assert not any(c[0] == "POST" for c in target["calls"])
    assert assess_application_access(s, capture.bundle).outcome == "incomplete"


def test_whole_plan_rejected_before_first_request(target, tmp_path):
    s = selected(mutate=True)
    s.cases[0].state_check.path = "/outside"
    with pytest.raises(PermissionError):
        observe(s, client(target, tmp_path), tmp_path / "capture")
    assert target["calls"] == []


def test_budget_and_cancellation_do_not_renew(target, tmp_path):
    s = selected()
    path = tmp_path / "capture"
    capture = observe(s, client(target, tmp_path, requests=1), path)
    assert capture.stop_reason == "budget_exhausted"
    assert len(target["calls"]) == 1
    observe(s, client(target, tmp_path, requests=1), path)
    assert len(target["calls"]) == 1
    path = tmp_path / "cancelled"
    capture = observe(s, client(target, tmp_path, should_stop=lambda: True), path)
    assert capture.stop_reason == "cancelled"
    observe(s, client(target, tmp_path), path)
    assert len(target["calls"]) == 1


def test_original_deadline_and_scope_binding(target, tmp_path):
    s = selected(mutate=True)
    target.update(vulnerable=True, drop=True)
    path = tmp_path / "capture"
    capture = observe(s, client(target, tmp_path), path)
    capture.started_at -= 25
    path.write_text(capture.model_dump_json(by_alias=True))
    count = len(target["calls"])
    resumed = observe(s, client(target, tmp_path), path)
    assert resumed.stop_reason == "timeout"
    assert len(target["calls"]) == count
    changed = client(target, tmp_path)
    changed.scope.web.origin = "http://localhost:9999"
    with pytest.raises(ValueError, match="capture differs"):
        observe(s, changed, path)


def test_new_process_cli_resume_keeps_mutation_and_input_hashes(target, tmp_path):
    import hashlib
    import os
    import subprocess
    import sys

    from moonmind.security.application_access import ApplicationAccessReport
    from moonmind.security.workload_profiles import TARGET_SCOPE_ENV

    target.update(vulnerable=True, drop=True)
    s = selected(mutate=True)
    capture_path = tmp_path / "capture"
    initial_client = client(target, tmp_path)
    original = observe(s, initial_client, capture_path)
    target["drop"] = False
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(s.model_dump_json(by_alias=True))
    output = tmp_path / "report.json"
    # The predecessor is gone: a fresh process reads only persisted files.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; from moonmind.security.application_access_target import main; "
                f"raise SystemExit(main(credential_root=Path({str(tmp_path)!r})))"
            ),
            "--spec",
            str(spec_path),
            "--capture",
            str(capture_path),
            "--output",
            str(output),
            "--max-seconds",
            "20",
        ],
        env={
            **os.environ,
            TARGET_SCOPE_ENV: initial_client.scope.model_dump_json(by_alias=True),
            "MOONMIND_EGRESS_PROXY": target["origin"],
            "MOONMIND_EGRESS_POLICY_REF": "fixture",
        },
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert target["effects"] == 1
    restored = ApplicationAccessCapture.model_validate_json(capture_path.read_bytes())
    assert (
        restored.bundle.observations[0].attempt_ref
        == original.bundle.observations[0].attempt_ref
    )
    report = ApplicationAccessReport.model_validate_json(output.read_bytes())
    assert (report.status, report.outcome) == ("partial", "violations")
    assert {"response_incomplete", "effect_uncertain"} <= set(report.cases[0].reasons)
    assert report.input_digests == {
        "spec": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        "capture": hashlib.sha256(capture_path.read_bytes()).hexdigest(),
    }
    assert "PRIVATE_MARKER" not in output.read_text()


def test_cancel_after_effect_retains_response_and_stops(target, tmp_path):
    s = selected(mutate=True)
    target["vulnerable"] = True
    capture = observe(
        s,
        client(target, tmp_path, should_stop=lambda: target["effects"] == 1),
        tmp_path / "capture",
    )
    observation = capture.bundle.observations[0]
    assert capture.stop_reason == "cancelled"
    assert observation.response
    assert observation.attempt_ref
    assert len(target["calls"]) == 3
    observe(s, client(target, tmp_path), tmp_path / "capture")
    assert len(target["calls"]) == 3


def test_rate_and_exclusion_enforced(target, tmp_path):
    s = selected()
    c = client(target, tmp_path)
    c.scope.web.max_requests_per_second = 10
    observe(s, c, tmp_path / "rate")
    times = [row[3] for row in target["calls"]]
    assert all(b - a >= 0.085 for a, b in itertools.pairwise(times))
    target["calls"].clear()
    c = client(target, tmp_path)
    c.scope.web.exclusions = ["/record"]
    with pytest.raises(PermissionError):
        observe(s, c, tmp_path / "excluded")
    assert not target["calls"]


def test_checkpoint_reserved_before_every_send_and_attempt(target, tmp_path):
    s = selected(mutate=True)
    c = client(target, tmp_path)
    path = tmp_path / "capture"
    original = c.exchange

    async def checked(request):
        saved = ApplicationAccessCapture.model_validate_json(path.read_bytes())
        assert saved.requests_spent == c.requests_sent + 1
        if request.method == "POST":
            assert saved.progress[0].phase == "attempted"
            assert saved.bundle.observations[0].attempt_ref
        return await original(request)

    c.exchange = checked
    observe(s, c, path)


def test_capture_consistency_validation(target, tmp_path):
    from pydantic import ValidationError

    capture = observe(selected(), client(target, tmp_path), tmp_path / "capture")
    data = capture.model_dump(mode="json")
    data["scope_digest"] = "0" * 64
    with pytest.raises(ValidationError):
        ApplicationAccessCapture.model_validate(data)
    data = capture.model_dump(mode="json")
    data["progress"][0]["phase"] = "attempted"
    with pytest.raises(ValidationError):
        ApplicationAccessCapture.model_validate(data)


def test_completed_cases_survive_interruption_and_resume(target, tmp_path):
    import copy

    data = selected(mutate=True).model_dump(mode="json")
    later = copy.deepcopy(data["cases"][0])
    later["case_ref"] = "later"
    later["request"]["id"] = "later-request"
    later["request"]["payload"]["mutation"] = "mutation-2"
    later["mutation_ref"] = "mutation-2"
    later["receipt_assertion"]["value"] = "mutation-2"
    data["cases"].append(later)
    access = copy.deepcopy(data["authorization_matrix"]["cases"][0])
    access["id"] = "later"
    access["action"] = "update-later"
    data["authorization_matrix"]["cases"].append(access)
    s = ApplicationAccessSpec.model_validate(data)
    target.update(vulnerable=True, drop="mutation-2")
    path = tmp_path / "capture"
    first = observe(s, client(target, tmp_path), path)
    assert first.progress[0].phase == "complete"
    assert first.progress[1].phase == "attempted"
    assert target["effects"] == 2
    resumed = observe(s, client(target, tmp_path), path)
    assert resumed.bundle.observations[0] == first.bundle.observations[0]
    assert target["effects"] == 2
    assert len([c for c in target["calls"] if c[0] == "POST"]) == 2
    assert assess_application_access(s, resumed.bundle).cases[0].outcome == "finding"


def test_capture_spec_guard_rejects_changed_payload_and_admission(target, tmp_path):
    from pydantic import ValidationError

    from moonmind.security.application_access_target import validate_capture_spec
    from moonmind.security.assessments import content_key

    s = selected(mutate=True)
    capture = observe(s, client(target, tmp_path), tmp_path / "capture")
    validate_capture_spec(s, capture)
    s.cases[0].request.payload["mutation"] = "different"
    with pytest.raises(ValidationError):
        validate_capture_spec(s, capture)
    s = selected(mutate=True)
    capture.scope.web.exclusions = ["/record"]
    capture.scope_digest = content_key(capture.scope.model_dump(mode="json"))
    with pytest.raises(PermissionError):
        validate_capture_spec(s, capture)


def test_post_identity_failure_with_matching_json_is_inconclusive(target, tmp_path):
    target.update(vulnerable=True, post_identity_failed=True)
    selected_spec = selected(mutate=True)
    capture = observe(selected_spec, client(target, tmp_path), tmp_path / "capture")
    report = assess_application_access(selected_spec, capture.bundle)
    assert report.cases[0].authentication_state == "unverified"
    assert report.cases[0].outcome == "inconclusive"
