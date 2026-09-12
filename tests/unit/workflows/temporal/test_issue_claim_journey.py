"""Escaped own-claim regression through HTTP, production tools and durable SQL."""

import asyncio
import json
import threading
from contextlib import AsyncExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import GitHubIssueClaim
from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal import story_output_tools as tools
from moonmind.workflows.temporal.issue_claim_store import IssueClaimStore


@pytest_asyncio.fixture
async def journey(tmp_path, monkeypatch, request):
    stack = AsyncExitStack()
    engine = None
    if getattr(request, "param", None) == "postgres":
        from tests.support.isolated_postgres import isolated_postgres

        sessions = await stack.enter_async_context(
            isolated_postgres([GitHubIssueClaim.__table__])
        )
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claims.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(GitHubIssueClaim.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tools, "IssueClaimStore", lambda: IssueClaimStore(sessions))
    monkeypatch.setenv("MOONMIND_INSTALLATION_ID", "inst-test-claims")
    state = {"comments": [], "labels": [], "lost_ack": False, "posts": 0}
    repository = "example/repo"

    class Handler(BaseHTTPRequestHandler):
        def issue(self):
            return {
                "number": 3970,
                "title": "Implement bounded work",
                "body": "Acceptance: automated fixture passes.",
                "html_url": f"https://github.com/{repository}/issues/3970",
                "state": state.get("state", "open"),
                "labels": [{"name": label} for label in state["labels"]],
            }

        def log_message(self, *_args):
            pass

        def respond(self, payload, status=200):
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/user":
                self.respond({"id": 123, "login": "fixture-owner"})
            elif path.endswith("/comments"):
                self.respond(state["comments"])
            elif path.endswith("/issues/3970"):
                self.respond(self.issue())
            elif path.endswith("/issues"):
                self.respond(
                    [self.issue()] if state.get("state", "open") == "open" else []
                )
            else:
                self.respond({"message": "unknown fixture route"}, 404)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/comments"):
                state["posts"] += 1
                comment = {
                    "id": len(state["comments"]) + 1,
                    "body": payload["body"],
                    "user": {"id": 123, "login": "fixture-owner"},
                }
                state["comments"].append(comment)
                if state["lost_ack"]:
                    state["lost_ack"] = False
                    self.close_connection = True
                    return
                self.respond(comment, 201)
            elif self.path.endswith("/labels"):
                state["labels"] = list(set(state["labels"] + payload["labels"]))
                self.respond([{"name": label} for label in state["labels"]])
            else:
                self.respond({}, 404)

        def do_PATCH(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "/issues/comments/" not in self.path:
                return self.respond({}, 404)
            comment = next(
                item
                for item in state["comments"]
                if item["id"] == int(self.path.rsplit("/", 1)[1])
            )
            released = '"activity":"released"' in payload["body"]
            if released and state.get("reject_release"):
                return self.respond({"message": "fixture temporary failure"}, 503)
            comment["body"] = payload["body"]
            if released and state.get("lose_release_ack"):
                state["lose_release_ack"] = False
                self.close_connection = True
                return
            self.respond(comment)

        def do_DELETE(self):
            label = unquote(self.path.rsplit("/", 1)[1])
            if label in state["labels"]:
                state["labels"].remove(label)
            self.respond([{"name": label} for label in state["labels"]])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_client = httpx.AsyncClient

    class Client(original_client):
        def build_request(self, method, url, *args, **kwargs):
            url = str(url).replace(
                "https://api.github.com", f"http://127.0.0.1:{server.server_port}"
            )
            return super().build_request(method, url, *args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    service = GitHubService()

    async def token(*_args, **_kwargs):
        return "fixture-only", None

    async def readiness(**_kwargs):
        return {"ready": True}

    monkeypatch.setattr(service, "resolve_github_token", token)
    monkeypatch.setattr(service, "check_issue_label_readiness", readiness)
    try:
        yield state, service, sessions
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        if engine is not None:
            await engine.dispose()
        await stack.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_ack", [False, True])
async def test_default_claim_restart_then_start_preserves_owner(journey, lost_ack):
    state, service, sessions = journey
    state["lost_ack"] = lost_ack
    inputs = {"repository": "example/repo", "issueNumber": 3970}
    context = {"execution_owner": "default/workflow-a"}
    brief = await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    assert brief.status == "COMPLETED", brief.outputs
    attempt_id = brief.outputs["attemptId"]
    # A new store/tool invocation has no previous outputs: persisted selection
    # and the authenticated remote receipt are sufficient to recover lost ack.
    receipt = await IssueClaimStore(sessions).get(context["execution_owner"])
    assert receipt.confirmed
    repeated = await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    assert repeated.status == "COMPLETED", repeated.outputs
    started = await tools.update_github_issue_status(
        {**inputs, "mode": "start"}, context, github_service_factory=lambda: service
    )
    assert started.status == "COMPLETED", started.outputs
    assert started.outputs["attemptId"] == attempt_id
    assert state["posts"] == 1
    assert state["labels"] == ["status: in-progress"]
    # Simulate a worker loss between announcement and label write. The durable
    # owner resumes its pending mutation rather than selecting a new issue.
    async with IssueClaimStore(sessions).locked(context["execution_owner"]) as row:
        row.confirmed = False
    state["labels"] = []
    recovered = await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    assert recovered.status == "COMPLETED", recovered.outputs
    assert state["posts"] == 1
    # Supplying that public identifier from another workflow proves nothing.
    stolen = await tools.update_github_issue_status(
        {**inputs, "mode": "start", "attemptId": attempt_id},
        {"execution_owner": "default/workflow-b"},
        github_service_factory=lambda: service,
    )
    assert stolen.status == "FAILED"
    assert state["posts"] == 1


@pytest.mark.asyncio
async def test_changed_claim_provenance_and_candidate_are_rejected(journey):
    state, service, _sessions = journey
    inputs = {"repository": "example/repo", "issueNumber": 3970}
    context = {"execution_owner": "default/workflow-a"}
    first = await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    assert first.status == "COMPLETED", first.outputs
    changed = await tools.load_github_issue_preset_brief(
        {**inputs, "issueNumber": 4000}, context, github_service_factory=lambda: service
    )
    assert changed.status == "FAILED"
    assert changed.outputs["reasonCode"] == "substitution_denied"
    state["comments"][0]["user"]["id"] = 999
    forged = await tools.update_github_issue_status(
        {**inputs, "mode": "start"}, context, github_service_factory=lambda: service
    )
    assert forged.status == "FAILED"
    assert forged.outputs["reasonCode"] == "claim_evidence_conflict"


@pytest.mark.asyncio
async def test_duplicate_effect_observation_distinguishes_pagination_from_remote_effects(
    journey, monkeypatch
):
    from moonmind.observability import metrics
    from moonmind.workflows.temporal.issue_claim_store import inspect_claim_comments

    state, service, sessions = journey
    owner = "default/duplicate-observation"
    result = await tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueNumber": 3970},
        {"execution_owner": owner},
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED"
    receipt = await IssueClaimStore(sessions).get(owner)
    observations = []
    monkeypatch.setattr(
        metrics,
        "increment_counter",
        lambda name, **kwargs: observations.append((name, kwargs)),
    )
    comment = state["comments"][0]
    assert inspect_claim_comments(receipt, [comment, comment]) == str(comment["id"])
    assert observations == []
    with pytest.raises(ValueError, match="duplicated"):
        inspect_claim_comments(receipt, [comment, {**comment, "id": 999}])
    assert observations == [
        ("moonmind_duplicate_effect_observations", {"labels": {"component": "worker"}})
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["lose_release_ack", "reject_release"])
async def test_failed_finalization_releases_durable_claim_after_remote_confirmation(
    journey, fault
):
    from moonmind.workflows.temporal.github_issue_attempts import parse_attempt_comment

    state, service, sessions = journey
    inputs = {"repository": "example/repo", "issueNumber": 3970}
    context = {"execution_owner": "default/workflow-a"}
    brief = await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    assert brief.status == "COMPLETED", brief.outputs
    finalization = {
        **inputs,
        "executionEvent": "failed",
        "writerEvidence": {
            "writers_stopped": True,
            "stop_method": "runtime_quiescence",
            "stop_evidence": "fixture writer exited",
        },
        "mutationEvidence": {
            "push_outcome_absent": True,
            "pr_outcome_absent": True,
            "merge_outcome_absent": True,
        },
        "preservationEvidence": {
            "save_method": "explicit_no_work",
            "trustworthy_no_work": True,
        },
        "dispositionEvidence": {"no_work": True, "fresh_retry_safe": True},
        "nextAction": "fresh_retry",
    }
    state[fault] = True
    first = await tools.finalize_github_issue_failed_attempt(
        finalization, context, github_service_factory=lambda: service
    )
    if fault == "reject_release":
        assert first.status == "FAILED", first.outputs
        assert not (
            await IssueClaimStore(sessions).get(context["execution_owner"])
        ).released
        state[fault] = False
    resumed = await tools.finalize_github_issue_failed_attempt(
        finalization, context, github_service_factory=lambda: service
    )
    assert resumed.status == "COMPLETED", resumed.outputs
    receipt = await IssueClaimStore(sessions).get(context["execution_owner"])
    assert receipt.released
    assert receipt.pending_comment_body is None
    assert receipt.finalization_json["result"]["released"]
    assert (
        parse_attempt_comment(state["comments"][0]["body"]).handoff.activity
        == "released"
    )
    assert state["posts"] == 1
    # The partial unique index now admits a different workflow. Selection
    # policy still independently decides whether/when that issue is eligible.
    successor = await IssueClaimStore(sessions).prepare(
        owner="default/workflow-b",
        repository="example/repo",
        issue_number=3970,
        attempt_id="successor",
        actor_id="123",
        comment_body="successor intent",
    )
    assert successor.owner != receipt.owner
    assert (
        await tools.finalize_github_issue_failed_attempt(
            finalization, context, github_service_factory=lambda: service
        )
    ).status == "COMPLETED"


@pytest.mark.asyncio
@pytest.mark.parametrize("post_authorized", [False, True])
async def test_reselection_requires_proof_no_announcement_was_authorized(
    journey, post_authorized
):
    state, service, sessions = journey
    owner = "default/reservation-crash"
    context = {"execution_owner": owner}
    claim = await tools._prepare_github_issue_claim(
        inputs={"repository": "example/repo", "issueNumber": 3970},
        context=context,
        repository="example/repo",
        issue_number=3970,
        service=service,
    )
    if post_authorized:
        await IssueClaimStore(sessions).start_announcement(owner, claim.attempt_id)
    # Crash after reservation, then an independent writer closes the issue.
    state["state"] = "closed"
    result = await tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueSearch": ""},
        context,
        github_service_factory=lambda: service,
    )
    retained = await IssueClaimStore(sessions).get(owner)
    assert state["posts"] == 0
    if post_authorized:
        assert result.status == "FAILED"
        assert retained and retained.announcement_started
    else:
        assert result.status == "COMPLETED", result.outputs
        assert result.completion_disposition == "idle"
        assert retained is None
