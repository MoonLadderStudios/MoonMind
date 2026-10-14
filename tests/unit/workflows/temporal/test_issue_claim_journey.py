"""Escaped own-claim regression through HTTP, production tools and durable SQL."""

import asyncio
import hashlib
import json
import threading
from contextlib import AsyncExitStack, asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

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
        def issue(self, number=3970):
            issue_state = state if number == 3970 else state["extra_state"]
            return {
                "number": number,
                "title": "Implement bounded work",
                "user": {"id": 123, "login": "fixture-owner"},
                "body": state.get("body", "Acceptance: automated fixture passes."),
                "html_url": f"https://github.com/{repository}/issues/{number}",
                "state": issue_state.get("state", "open"),
                "labels": [{"name": label} for label in issue_state["labels"]],
            }

        def log_message(self, *_args):
            pass

        def respond(self, payload, status=200, headers=None):
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = self.path.split("?")[0]
            state.setdefault("reads", []).append(path)
            if path == state.get("failed_read_path"):
                self.respond({"message": "fixture read outage"}, 503)
                return
            if path == "/user":
                self.respond(state.get("actor", {"id": 123, "login": "fixture-owner"}))
            elif path.endswith("/issues/3971/comments") and "extra_state" in state:
                self.respond(state["extra_state"]["comments"])
            elif path.endswith("/comments"):
                self.respond(state["comments"])
            elif path.endswith("/issues/3971") and "extra_state" in state:
                self.respond(self.issue(3971))
            elif path.endswith("/issues/3970"):
                self.respond(self.issue())
            elif state.get("scan_entries") and path.rsplit("/", 1)[-1].isdigit():
                self.respond(next(item for item in state["scan_entries"] if item["number"] == int(path.rsplit("/", 1)[-1])))
            elif path.endswith("/issues/42"):
                self.respond(
                    {
                        **self.issue(),
                        "number": 42,
                        "state": "closed",
                        "html_url": f"https://github.com/{repository}/issues/42",
                    }
                )
            elif "/compare/" in path and "comparison_provider" in state:
                if state.get("compare_failures_remaining", 0):
                    state["compare_failures_remaining"] -= 1
                    self.respond({"message": "temporary comparison outage"}, 503)
                else:
                    payload, status = state["comparison_provider"](unquote(path.rsplit("/compare/", 1)[1]))
                    # GitHub-style validator: an unchanged comparison answers
                    # a conditional request with an uncounted 304.
                    etag = '"%s"' % hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                    if status == 200 and self.headers.get("If-None-Match") == etag:
                        state["compare_not_modified"] = state.get("compare_not_modified", 0) + 1
                        self.send_response(304)
                        self.send_header("ETag", etag)
                        self.end_headers()
                    else:
                        self.respond(payload, status, {"ETag": etag})
            elif path.endswith("/issues"):
                if state.get("scan_entries"):
                    query = parse_qs(urlsplit(self.path).query)
                    per_page = int(query.get("per_page", [100])[0])
                    page = int(query.get("page", [1])[0])
                    # A listing carries the same live labels as the issue read.
                    self.respond(
                        [
                            self.issue() if item["number"] == 3970 else item
                            for item in state["scan_entries"][(page - 1) * per_page : page * per_page]
                        ]
                    )
                    return
                if int(parse_qs(urlsplit(self.path).query).get("page", [1])[0]) > 1:
                    self.respond([])
                    return
                self.respond(
                    ([self.issue()] if state.get("state", "open") == "open" else [])
                    + ([self.issue(3971)] if "extra_state" in state else [])
                )
            else:
                self.respond({"message": "unknown fixture route"}, 404)
            return

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            issue_state = state["extra_state"] if "/issues/3971/" in self.path else state
            if self.path.endswith("/comments"):
                state["posts"] += 1
                comment = {
                    "id": len(issue_state["comments"]) + 1,
                    "body": payload["body"],
                    "user": state.get("actor", {"id": 123, "login": "fixture-owner"}),
                    "author_association": "COLLABORATOR",
                    # GitHub always timestamps a comment; reservation policy
                    # reads announcement time from this field, never locally.
                    "created_at": state.get("created_at", "2026-09-01T00:00:00Z"),
                }
                issue_state["comments"].append(comment)
                if state["lost_ack"]:
                    state["lost_ack"] = False
                    self.close_connection = True
                    return
                self.respond(comment, 201)
            elif self.path.endswith("/labels"):
                issue_state["labels"] = list(set(issue_state["labels"] + payload["labels"]))
                self.respond([{"name": label} for label in issue_state["labels"]])
            else:
                self.respond({}, 404)

        def do_PATCH(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "/issues/comments/" not in self.path:
                self.respond({}, 404)
                return
            comment = next(
                item
                for item in state["comments"]
                if item["id"] == int(self.path.rsplit("/", 1)[1])
            )
            released = '"activity":"released"' in payload["body"]
            if released and state.get("reject_release"):
                self.respond({"message": "fixture temporary failure"}, 503)
                return
            comment["body"] = payload["body"]
            if state.get("lose_update_ack"):
                state["lose_update_ack"] = False
                self.close_connection = True
                return
            if released and state.get("lose_release_ack"):
                state["lose_release_ack"] = False
                self.close_connection = True
                return
            self.respond(comment)
            return

        def do_DELETE(self):
            issue_state = state["extra_state"] if "/issues/3971/" in self.path else state
            label = unquote(self.path.rsplit("/", 1)[1])
            if label in issue_state["labels"]:
                issue_state["labels"].remove(label)
            self.respond([{"name": label} for label in issue_state["labels"]])

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
@pytest.mark.parametrize(
    "contender_order,contender_activity,proceeds",
    [
        ("later", "preparing", True),
        ("earlier", "preparing", False),
        ("later", "active", False),
    ],
)
async def test_simultaneous_announcements_admit_only_the_earliest(
    journey, monkeypatch, contender_order, contender_activity, proceeds
):
    """Two deployments that announce in the same instant must not both yield.

    GitHub's comment IDs totally order the announcements, so the earliest
    preparing claim continues and the later one quiesces. A contender that has
    already started work still stops this attempt.
    """
    state, service, sessions = journey
    other_owner = "default/other-deployment-attempt"
    other = await tools._prepare_github_issue_claim(
        inputs={"repository": "example/repo", "issueNumber": 3970},
        context={"execution_owner": other_owner},
        repository="example/repo", issue_number=3970, service=service,
    )
    # The other deployment's reservation lives only on GitHub.
    assert await IssueClaimStore(sessions).abandon_unannounced(
        other_owner, other.attempt_id
    )
    contender_body = other.comment_body.replace(
        '"activity":"preparing"', f'"activity":"{contender_activity}"'
    )
    assert f'"activity":"{contender_activity}"' in contender_body
    original_create = service.create_issue_comment

    def announce_contender():
        state["comments"].append(
            {
                "id": len(state["comments"]) + 1,
                "body": contender_body,
                "user": {"id": 123, "login": "fixture-owner"},
                "author_association": "COLLABORATOR",
                "created_at": "2026-09-01T00:00:00Z",
            }
        )

    async def racing_create(**kwargs):
        if contender_order == "earlier":
            announce_contender()
        created = await original_create(**kwargs)
        if contender_order == "later":
            announce_contender()
        return created

    monkeypatch.setattr(service, "create_issue_comment", racing_create)
    inputs = {"repository": "example/repo", "issueNumber": 3970}
    context = {"execution_owner": "default/this-deployment-attempt"}
    brief = await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    assert state["posts"] == 1
    if not proceeds:
        assert brief.status == "FAILED", brief.outputs
        assert brief.outputs["reasonCode"] == "active_attempt_conflict"
        assert state["labels"] == []
        return
    assert brief.status == "COMPLETED", brief.outputs
    assert state["labels"] == ["status: in-progress"]
    started = await tools.update_github_issue_status(
        {**inputs, "mode": "start"}, context, github_service_factory=lambda: service
    )
    assert started.status == "COMPLETED", started.outputs
    assert started.outputs["attemptId"] == brief.outputs["attemptId"]


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
async def test_blocked_assessment_records_intent_without_unverified_release(journey):
    state, service, sessions = journey
    context = {"execution_owner": "default/blocked-assessment-run"}
    brief = await tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueSearch": ""},
        context,
        github_service_factory=lambda: service,
    )
    assert brief.status == "COMPLETED"
    inputs = {
        "repository": "example/repo",
        "issueNumber": 3970,
        "attemptId": brief.outputs["attemptId"],
        "executionEvent": "blocked",
        "terminalIntent": True,
        "assessmentArtifactRef": "art_assessment_3970",
        "remainingRequirements": ["Run packaged GPU proof"],
        "reason": "Managed render image pull was denied before proof ran.",
    }

    result = await tools.finalize_github_issue_failed_attempt(
        inputs, context, github_service_factory=lambda: service
    )
    receipt = await IssueClaimStore(sessions).get(context["execution_owner"])
    assert result.status == "FAILED"
    assert result.outputs["released"] is False
    assert result.outputs["ownershipEnded"] is False
    assert receipt.released is False and receipt.ownership_ended is False
    assert receipt.finalization_json["intent"] == {
        "executionEvent": "blocked",
        "assessmentArtifactRef": "art_assessment_3970",
        "remainingRequirements": ["Run packaged GPU proof"],
        "reason": "Managed render image pull was denied before proof ran.",
    }
    assert state["labels"] == ["status: in-progress"]
    assert (await tools.finalize_github_issue_failed_attempt(
        inputs, context, github_service_factory=lambda: service
    )).status == "FAILED"


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
        store = IssueClaimStore(sessions)
        async with store.locked(owner) as row:
            await store.start_announcement(row)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before_selection", "after_reservation", "before_locked_check"])
@pytest.mark.parametrize("eligible_successor", [False, True])
async def test_search_skips_remote_contender_before_authorizing_announcement(
    journey, monkeypatch, when, eligible_successor
):
    """A prior deployment's preparing comment must not poison the next tick."""
    state, service, sessions = journey
    old_owner = "default/old-deployment-attempt"
    existing = await tools._prepare_github_issue_claim(
        inputs={"repository": "example/repo", "issueNumber": 3970},
        context={"execution_owner": old_owner},
        repository="example/repo", issue_number=3970, service=service,
    )
    # Its external attempt survives while this deployment has no local claim.
    assert await IssueClaimStore(sessions).abandon_unannounced(
        old_owner, existing.attempt_id
    )
    contender = {"id": 91, "body": existing.comment_body, "user": {"id": 123}}
    if eligible_successor:
        state["extra_state"] = {"comments": [], "labels": []}
    if when == "before_selection":
        state["comments"] = [contender]
    elif when == "after_reservation":
        prepare = tools._prepare_github_issue_claim

        async def contender_after_reservation(**kwargs):
            receipt = await prepare(**kwargs)
            state["comments"] = [contender]
            return receipt

        monkeypatch.setattr(tools, "_prepare_github_issue_claim", contender_after_reservation)
    else:
        locked = IssueClaimStore.locked

        @asynccontextmanager
        async def contender_at_locked_check(store, locked_owner):
            async with locked(store, locked_owner) as row:
                state["comments"] = [contender]
                yield row

        monkeypatch.setattr(IssueClaimStore, "locked", contender_at_locked_check)
    owner = "default/next-recurring-occurrence"
    result = await tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueSearch": "", "includeAllAuthors": False},
        {"execution_owner": owner}, github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED", result.outputs
    assert state["labels"] == []
    assert state["comments"] == [contender]
    retained = await IssueClaimStore(sessions).get(owner)
    if eligible_successor:
        assert result.completion_disposition != "idle"
        assert retained.issue_number == 3971 and retained.confirmed
        assert state["posts"] == 1
        assert state["extra_state"]["labels"] == ["status: in-progress"]
    else:
        assert result.completion_disposition == "idle"
        assert state["posts"] == 0
        assert retained is None
    if when == "before_selection":
        evidence = result.outputs["searchEvidence"]
        assert evidence["rejectionCounts"]["live_reservation"] == 1
        rejected = evidence["rejectedCandidates"][0]
        assert rejected["issueNumber"] == 3970
        assert rejected["claimEvidence"]["attempts"][0]["workflowId"] == old_owner
        assert (
            rejected["claimEvidence"]["attempts"][0]["reservationStatus"]
            == "live_reservation"
        )
        if not eligible_successor:
            assert "reserved by another attempt" in result.outputs["summary"]
            assert "selectedIssueAuthor" not in evidence


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage,read,post_authorized",
    [
        ("selection", "comments", False),
        ("selection", "prerequisite", False),
        ("resume", "comments", False),
        ("resume", "issue", False),
        ("resume", "comments", True),
        ("resume", "issue", True),
    ],
)
async def test_failed_brief_reads_release_only_unannounced_reservations(
    journey, monkeypatch, stage, read, post_authorized
):
    state, service, sessions = journey
    owner = "default/read-failure"
    store = IssueClaimStore(sessions)
    context = {"execution_owner": owner}
    inputs = {
        "repository": "example/repo",
        "issueSearch": "",
        "includeAllAuthors": False,
    }
    paths = {
        "comments": "/repos/example/repo/issues/3970/comments",
        "issue": "/repos/example/repo/issues/3970",
        "prerequisite": "/repos/example/repo/issues/42",
    }
    if read == "prerequisite":
        state["body"] = "Depends on #42.\nAcceptance: automated fixture passes."
    prepare = tools._prepare_github_issue_claim
    reservations = []

    async def reserve_then_fail_read(**kwargs):
        receipt = await prepare(**kwargs)
        reservations.append(receipt)
        # Fault begins only after the real SQL commit. Subsequent evidence
        # reads fail through the real GitHub HTTP adapter, before any POST.
        state["failed_read_path"] = paths[read]
        return receipt

    if stage == "resume":
        receipt = await prepare(
            inputs=inputs,
            context=context,
            repository="example/repo",
            issue_number=3970,
            service=service,
        )
        reservations.append(receipt)
        if post_authorized:
            async with store.locked(owner) as row:
                await store.start_announcement(row)
        state["failed_read_path"] = paths[read]
    else:
        monkeypatch.setattr(tools, "_prepare_github_issue_claim", reserve_then_fail_read)

    result = await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    assert result.status == "FAILED", result.outputs
    assert len(reservations) == 1  # Read failure does not trigger candidate reselection.
    assert paths[read] in state["reads"]
    assert state["posts"] == 0 and state["labels"] == []
    retained = await IssueClaimStore(sessions).get(owner)
    if post_authorized:
        assert retained and retained.announcement_started
        assert not retained.released
        assert retained.attempt_id == reservations[0].attempt_id
    else:
        assert retained is None
        # A different durable workflow can select this issue after the outage;
        # the partial unique index must not retain the failed reader's claim.
        state.pop("failed_read_path")
        monkeypatch.setattr(tools, "_prepare_github_issue_claim", prepare)
        successor = await tools.load_github_issue_preset_brief(
            inputs,
            {"execution_owner": "default/next-scheduled-run"},
            github_service_factory=lambda: service,
        )
        assert successor.status == "COMPLETED", successor.outputs
        assert state["posts"] == 1
        assert successor.outputs["attemptId"] != reservations[0].attempt_id


@pytest.mark.asyncio
async def test_unexpected_pre_mutation_exception_does_not_strand_reservation(
    journey, monkeypatch
):
    state, service, sessions = journey
    owner = "default/unexpected-read-failure"
    prepare = tools._prepare_github_issue_claim

    async def interrupted_after_reservation(**kwargs):
        await prepare(**kwargs)
        raise RuntimeError("fixture failed before mutation")

    monkeypatch.setattr(
        tools, "_prepare_github_issue_claim", interrupted_after_reservation
    )
    with pytest.raises(RuntimeError, match="fixture failed before mutation"):
        await tools.load_github_issue_preset_brief(
            {"repository": "example/repo", "issueSearch": ""},
            {"execution_owner": owner},
            github_service_factory=lambda: service,
        )
    retained = await IssueClaimStore(sessions).get(owner)
    assert retained is None
    assert state["posts"] == 0


@pytest.mark.asyncio
async def test_failed_child_brief_read_keeps_parent_reservation(journey, monkeypatch):
    state, service, sessions = journey
    parent = "default/claim-parent"
    child = parent + "/child"
    receipt = await tools._prepare_github_issue_claim(
        inputs={"repository": "example/repo", "issueNumber": 3970},
        context={"execution_owner": parent},
        repository="example/repo",
        issue_number=3970,
        service=service,
    )
    original_for_execution = IssueClaimStore.for_execution

    async def inherited(self, owner):
        if owner == child:
            return receipt
        return await original_for_execution(self, owner)

    monkeypatch.setattr(IssueClaimStore, "for_execution", inherited)
    state["failed_read_path"] = "/repos/example/repo/issues/3970/comments"
    result = await tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueSearch": ""},
        {"execution_owner": child},
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED"
    retained = await IssueClaimStore(sessions).get(parent)
    assert retained == receipt
    assert state["posts"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["lose_release_ack", "reject_release"])
@pytest.mark.parametrize("explicit_gap", [True, False])
@pytest.mark.parametrize("legacy_plan", [True, False])
@pytest.mark.parametrize("revision_alias", [True, False])
async def test_terminal_handoff_keeps_portable_candidate_and_gap_after_restart(
    journey, fault, explicit_gap, legacy_plan, revision_alias
):
    from moonmind.workflows.temporal.github_issue_attempts import parse_attempt_comment

    state, service, sessions = journey
    inputs = {"repository": "example/repo", "issueNumber": 3970}
    context = {"execution_owner": "default/workflow-portable"}
    brief = await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    assert brief.status == "COMPLETED", brief.outputs
    evidence = {
        **inputs,
        "executionEvent": "failed",
        "writerEvidence": {
            "writers_stopped": True,
            "stop_method": "runtime_quiescence",
            "stop_evidence": "writer exited",
        },
        "mutationEvidence": {
            "push_outcome_absent": True,
            "pr_outcome_absent": True,
            "merge_outcome_absent": True,
        },
        "preservationEvidence": {
            "save_method": "pr_head_verified",
            "preservation_verified": True,
            "pr_url": "https://github.com/example/repo/pull/42",
            "pr_head_sha": "candidate-a",
            "pr_base": "main",
            "saved_branch": "saved-work",
            "saved_sha": "candidate-a",
        },
        "dispositionEvidence": {
            "portable_work_safe": True,
            "preservation_verified": True,
            "handoff_published": True,
        },
        "remainingRequirements": ["Run the missing gameplay acceptance scenario"],
        "nextAction": "verify",
        "reason": "Acceptance evidence is incomplete",
    }
    if not explicit_gap or revision_alias:
        from dataclasses import replace

        from moonmind.workflows.temporal.github_issue_attempts import (
            render_attempt_comment,
        )
        from moonmind.workflows.temporal.issue_claim_store import publish_claim_comment

        store = IssueClaimStore(sessions)
        receipt = await store.get(context["execution_owner"])
        prior = parse_attempt_comment(receipt.comment_body).handoff
        await publish_claim_comment(
            store,
            receipt,
            service,
            render_attempt_comment(
                replace(
                    prior,
                    remaining_requirements=(
                        tuple(evidence.pop("remainingRequirements"))
                        if not explicit_gap
                        else ()
                    ),
                    pr_url="https://github.com/example/repo/pull/42",
                    pr_head="candidate-old",
                    saved_sha="candidate-old",
                )
            ),
        )
    if revision_alias:
        # The generic PR revision proves only the PR; the saved branch has
        # its own explicitly supplied SHA.
        evidence["preservationEvidence"].pop("pr_head_sha")
        evidence["preservationEvidence"]["revision"] = "candidate-a"
    state[fault] = True
    await tools.finalize_github_issue_failed_attempt(
        evidence, context, github_service_factory=lambda: service
    )
    state[fault] = False
    if legacy_plan:
        # Retained deployments saved this shape before portable snapshots were
        # added. A lost acknowledgement can leave only a plan even if the
        # released comment is already confirmed remotely.
        async with IssueClaimStore(sessions).locked(context["execution_owner"]) as row:
            retained_plan = dict(row.finalization_json["plan"])
            retained_plan.pop("attempt_handoff")
            row.finalization_json = {"plan": retained_plan}
    # Restart with changed caller data: the committed plan, not a new candidate,
    # must supply both the readable and machine-readable released handoff.
    resumed = await tools.finalize_github_issue_failed_attempt(
        {
            **evidence,
            "remainingRequirements": ["Wrong later gap"],
            "preservationEvidence": {
                **evidence["preservationEvidence"],
                "saved_sha": "candidate-b",
                "pr_head_sha": "candidate-b",
            },
        },
        context,
        github_service_factory=lambda: service,
    )
    assert resumed.status == "COMPLETED", resumed.outputs
    handoff = parse_attempt_comment(state["comments"][0]["body"]).handoff
    assert handoff.activity == "released"
    assert handoff.pr_url == "https://github.com/example/repo/pull/42"
    assert handoff.pr_head == "candidate-a"
    assert handoff.pr_base == "main"
    assert handoff.saved_branch == "saved-work"
    assert handoff.saved_sha == "candidate-a"
    assert handoff.remaining_requirements == (
        "Run the missing gameplay acceptance scenario",
    )
    assert "Remaining requirements: none recorded." not in state["comments"][0]["body"]
    assert "Preserved work: none recorded." not in state["comments"][0]["body"]
    assert "candidate-old" not in state["comments"][0]["body"]
    assert handoff.last_report == "Acceptance evidence is incomplete"
    assert handoff.next_action == "verify"
    assert state["posts"] == 1
    if legacy_plan:
        receipt = await IssueClaimStore(sessions).get(context["execution_owner"])
        assert receipt.finalization_json["plan"] == retained_plan


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "new_ref",
    [
        {"pr_url": "https://github.com/example/repo/pull/43"},
        {"saved_branch": "different-work"},
    ],
)
@pytest.mark.parametrize("new_revision", [None, "new-sha"])
async def test_finalization_does_not_verify_new_reference_with_retained_sha(
    journey, new_ref, new_revision
):
    from dataclasses import replace

    from moonmind.workflows.temporal.github_issue_attempts import (
        parse_attempt_comment,
        render_attempt_comment,
    )
    from moonmind.workflows.temporal.issue_claim_store import publish_claim_comment

    state, service, sessions = journey
    inputs = {"repository": "example/repo", "issueNumber": 3970}
    context = {"execution_owner": "default/workflow-changed-candidate"}
    brief = await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    assert brief.status == "COMPLETED", brief.outputs
    store = IssueClaimStore(sessions)
    receipt = await store.get(context["execution_owner"])
    prior = parse_attempt_comment(receipt.comment_body).handoff
    await publish_claim_comment(
        store,
        receipt,
        service,
        render_attempt_comment(
            replace(
                prior,
                pr_url="https://github.com/example/repo/pull/42",
                pr_head="old-sha",
                saved_branch="old-work",
                saved_sha="old-sha",
            )
        ),
    )
    before = state["comments"][0]["body"]
    result = await tools.finalize_github_issue_failed_attempt(
        {
            **inputs,
            "executionEvent": "failed",
            "writerEvidence": {
                "writers_stopped": True,
                "stop_method": "runtime_quiescence",
                "stop_evidence": "writer exited",
            },
            "mutationEvidence": {
                "push_outcome_absent": True,
                "pr_outcome_absent": True,
                "merge_outcome_absent": True,
            },
            "preservationEvidence": {
                "save_method": (
                    "saved_branch_verified" if "saved_branch" in new_ref else "pr_head_verified"
                ),
                "preservation_verified": True,
                **new_ref,
                **({"revision": new_revision} if new_revision else {}),
            },
            "dispositionEvidence": {
                "portable_work_safe": True,
                "preservation_verified": True,
                "handoff_published": True,
            },
            "remainingRequirements": ["Verify the changed candidate"],
            "nextAction": "verify",
        },
        context,
        github_service_factory=lambda: service,
    )
    if new_revision:
        assert result.status == "COMPLETED", result.outputs
        body = state["comments"][0]["body"]
        handoff = parse_attempt_comment(body).handoff
        assert handoff.pr_url == new_ref.get("pr_url", "")
        assert handoff.saved_branch == new_ref.get("saved_branch", "")
        assert "old-sha" not in body
        assert "old-work" not in body
        assert new_revision in body
    else:
        assert result.status == "FAILED", result.outputs
        assert result.outputs["reasonCode"] == "preservation_incomplete"
        assert state["comments"][0]["body"] == before
        assert not (await store.get(context["execution_owner"])).released


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", ["pr_url", "saved_branch"])
async def test_finalization_generic_revision_only_populates_matching_reference(
    journey, reference
):
    from moonmind.workflows.temporal.github_issue_attempts import parse_attempt_comment

    state, service, sessions = journey
    inputs = {"repository": "example/repo", "issueNumber": 3970}
    context = {"execution_owner": "default/workflow-single-reference"}
    brief = await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    assert brief.status == "COMPLETED", brief.outputs
    result = await tools.finalize_github_issue_failed_attempt(
        {
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
                "save_method": (
                    "pr_head_verified"
                    if reference == "pr_url"
                    else "saved_branch_verified"
                ),
                "preservation_verified": True,
                reference: (
                    "https://github.com/example/repo/pull/42"
                    if reference == "pr_url"
                    else "saved-work"
                ),
                "revision": "candidate-a",
            },
            "dispositionEvidence": {
                "portable_work_safe": True,
                "preservation_verified": True,
                "handoff_published": True,
            },
            "nextAction": "verify",
        },
        context,
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED", result.outputs
    body = state["comments"][0]["body"]
    handoff = parse_attempt_comment(body).handoff
    assert handoff.pr_head == ("candidate-a" if reference == "pr_url" else "")
    assert handoff.saved_sha == ("candidate-a" if reference == "saved_branch" else "")
    assert "Saved: ?@" not in body
    if reference == "saved_branch":
        assert "Head/base:" not in body
    receipt = await IssueClaimStore(sessions).get(context["execution_owner"])
    planned = receipt.finalization_json["plan"]["attempt_handoff"]
    assert planned["prHead"] == handoff.pr_head
    assert planned["savedSha"] == handoff.saved_sha


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "secret_field", ["reason", "remainingRequirements", "retryHistory", "overflow_retry_history", "scanner_unavailable"]
)
async def test_finalization_scans_raw_handoff_before_record_or_mutation(
    journey, monkeypatch, secret_field
):
    from unittest.mock import AsyncMock

    state, service, sessions = journey
    inputs = {"repository": "example/repo", "issueNumber": 3970}
    context = {"execution_owner": "default/workflow-blocked-handoff"}
    brief = await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    assert brief.status == "COMPLETED", brief.outputs
    state["labels"] = ["status: in-progress"]
    before_body = state["comments"][0]["body"]
    before = await IssueClaimStore(sessions).get(context["execution_owner"])
    calls = []
    original_record = IssueClaimStore.record_finalization

    async def record(self, *args, **kwargs):
        calls.append((args, kwargs))
        return await original_record(self, *args, **kwargs)

    monkeypatch.setattr(IssueClaimStore, "record_finalization", record)
    mutations = []
    for name in (
        "create_issue_comment",
        "update_issue_comment",
        "add_issue_labels",
        "remove_issue_label",
        "close_issue",
    ):
        spy = AsyncMock(wraps=getattr(service, name))
        monkeypatch.setattr(service, name, spy)
        mutations.append(spy)
    if secret_field == "scanner_unavailable":
        import moonmind.security

        def unavailable(*args, **kwargs):
            raise RuntimeError("fixture scanner outage")

        monkeypatch.setattr(moonmind.security, "scan_outbound_bundle", unavailable)
    # A synthetic fixture credential, never a real token.
    unsafe = "Synthetic fixture credential ghp_" + "A" * 36
    secret_input = (
        {"retryHistory": [f"prior-{index}" for index in range(20)] + [unsafe]}
        if secret_field == "overflow_retry_history"
        else {secret_field: [unsafe] if secret_field == "remainingRequirements" else unsafe}
    )
    result = await tools.finalize_github_issue_failed_attempt(
        {
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
            **(
                secret_input if secret_field != "scanner_unavailable"
                else {"reason": "Fixture safe reason"}
            ),
        },
        context,
        github_service_factory=lambda: service,
    )
    assert result.status == "FAILED", result.outputs
    assert result.outputs["reasonCode"] == "comment_blocked_by_scan"
    assert result.outputs["released"] is False
    assert result.outputs["workspaceRetained"] is True
    assert calls == []
    for mutation in mutations:
        mutation.assert_not_awaited()
    assert state["comments"][0]["body"] == before_body
    assert state["posts"] == 1
    assert state["labels"] == ["status: in-progress"]
    assert state.get("state", "open") == "open"
    after = await IssueClaimStore(sessions).get(context["execution_owner"])
    assert after == before
    assert unsafe not in json.dumps(result.outputs)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_plan", [False, True])
@pytest.mark.parametrize("release_confirmed", [False, True])
async def test_finalization_recovers_confirmed_release_without_rescanning(
    journey, monkeypatch, legacy_plan, release_confirmed
):
    from unittest.mock import AsyncMock, Mock

    import moonmind.security

    state, service, sessions = journey
    inputs = {"repository": "example/repo", "issueNumber": 3970}
    context = {"execution_owner": "default/workflow-release-result-crash"}
    brief = await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    assert brief.status == "COMPLETED", brief.outputs
    evidence = {
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
    original_record = IssueClaimStore.record_finalization

    async def crash_before_result(self, owner, phase, payload):
        if phase == "result":
            raise RuntimeError("fixture crash before final result persistence")
        return await original_record(self, owner, phase, payload)

    state["lose_release_ack" if release_confirmed else "reject_release"] = True
    with monkeypatch.context() as patch:
        patch.setattr(IssueClaimStore, "record_finalization", crash_before_result)
        if release_confirmed:
            with pytest.raises(RuntimeError, match="fixture crash before final result"):
                await tools.finalize_github_issue_failed_attempt(
                    evidence, context, github_service_factory=lambda: service
                )
        else:
            first = await tools.finalize_github_issue_failed_attempt(
                evidence, context, github_service_factory=lambda: service
            )
            assert first.status == "FAILED", first.outputs
    store = IssueClaimStore(sessions)
    receipt = await store.get(context["execution_owner"])
    assert receipt.released is release_confirmed
    assert "result" not in receipt.finalization_json
    if legacy_plan:
        async with store.locked(context["execution_owner"]) as row:
            plan = dict(row.finalization_json["plan"])
            plan.pop("attempt_handoff")
            row.finalization_json = {"plan": plan}
        receipt = await store.get(context["execution_owner"])
    before_plan = receipt.finalization_json["plan"]
    before_body = state["comments"][0]["body"]
    before_labels = list(state["labels"])
    writes = []
    for name in (
        "create_issue_comment",
        "update_issue_comment",
        "add_issue_labels",
        "remove_issue_label",
        "close_issue",
    ):
        spy = AsyncMock(wraps=getattr(service, name))
        monkeypatch.setattr(service, name, spy)
        writes.append(spy)
    scanner = Mock(side_effect=RuntimeError("fixture scanner outage"))
    monkeypatch.setattr(moonmind.security, "scan_outbound_bundle", scanner)
    resumed = await tools.finalize_github_issue_failed_attempt(
        evidence, context, github_service_factory=lambda: service
    )
    after = await store.get(context["execution_owner"])
    if release_confirmed:
        assert resumed.status == "COMPLETED", resumed.outputs
        assert resumed.outputs["released"] is True
        assert resumed.outputs["reasonCode"] == "released"
        assert after.finalization_json["result"]["released"] is True
        scanner.assert_not_called()
    else:
        assert resumed.status == "FAILED", resumed.outputs
        assert resumed.outputs["reasonCode"] == "comment_blocked_by_scan"
        assert after == receipt
        scanner.assert_called_once()
    assert after.finalization_json["plan"] == before_plan
    assert state["comments"][0]["body"] == before_body
    assert state["labels"] == before_labels
    assert state["posts"] == 1
    for write in writes:
        write.assert_not_awaited()


def _safe_terminal_inputs(**overrides):
    return {
        "repository": "example/repo",
        "issueNumber": 3970,
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
        "dispositionEvidence": {
            "trustworthy_no_work": True,
            "fresh_retry_allowed": True,
        },
        "nextAction": "fresh_retry",
        **overrides,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["pr_head_verified", "saved_branch_verified"])
@pytest.mark.parametrize("explicit_sha", [False, True])
async def test_reconciler_revision_does_not_rewrite_other_retained_candidate(
    journey, method, explicit_sha
):
    from dataclasses import replace

    from moonmind.workflows.temporal.activities.github_issue_reconciliation_activities import (
        _preservation_evidence_from_handoff,
    )
    from moonmind.workflows.temporal.github_issue_attempts import (
        parse_attempt_comment,
        render_attempt_comment,
    )
    from moonmind.workflows.temporal.issue_claim_store import publish_claim_comment

    state, service, sessions = journey
    inputs = _safe_terminal_inputs()
    context = {"execution_owner": "default/workflow-reconcile-revision"}
    await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    store = IssueClaimStore(sessions)
    receipt = await store.get(context["execution_owner"])
    prior = replace(
        parse_attempt_comment(receipt.comment_body).handoff,
        pr_url="https://github.com/example/repo/pull/42",
        pr_head="prior-pr-sha",
        saved_branch="saved-work",
        saved_sha="prior-branch-sha",
    )
    await publish_claim_comment(store, receipt, service, render_attempt_comment(prior))
    observed = "observed-revision"
    preservation = _preservation_evidence_from_handoff(
        newest={},
        preserved={
            "prHeadSha": observed,
            "savedBranch": prior.saved_branch,
            "savedSha": observed,
        },
        pr_url=prior.pr_url,
        pr_state={"known": method == "pr_head_verified", "headSha": observed},
    )
    assert preservation["save_method"] == method
    if not explicit_sha:
        preservation.pop("pr_head_sha" if method == "pr_head_verified" else "saved_sha")
    result = await tools.finalize_github_issue_failed_attempt(
        {
            **inputs,
            "preservationEvidence": preservation,
            "dispositionEvidence": {
                "portable_work_safe": True,
                "preservation_verified": True,
                "handoff_published": True,
            },
        },
        context,
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED", result.outputs
    handoff = parse_attempt_comment(state["comments"][0]["body"]).handoff
    assert handoff.pr_head == (
        observed if method == "pr_head_verified" else prior.pr_head
    )
    assert handoff.saved_sha == (
        observed if method == "saved_branch_verified" else prior.saved_sha
    )
    assert handoff.pr_url == prior.pr_url
    assert handoff.saved_branch == prior.saved_branch
    receipt = await store.get(context["execution_owner"])
    assert (
        receipt.finalization_json["plan"]["attempt_handoff"]["savedSha"]
        == handoff.saved_sha
    )
    assert (
        receipt.finalization_json["plan"]["attempt_handoff"]["prHead"]
        == handoff.pr_head
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["retryHistory", "retry_history", "nullable_camel"])
@pytest.mark.parametrize("history_size", [2, 20])
@pytest.mark.parametrize(
    "incoming",
    [
        "New verification retry",
        ["admitted-attempt", "New verification retry", "New verification retry", ""],
        "",
        [],
    ],
)
async def test_terminal_retry_history_extends_lineage_and_is_pinned(
    journey, alias, incoming, history_size
):
    from dataclasses import replace

    from moonmind.workflows.temporal.github_issue_attempts import (
        parse_attempt_comment,
        render_attempt_comment,
    )
    from moonmind.workflows.temporal.issue_claim_store import publish_claim_comment

    state, service, sessions = journey
    inputs = _safe_terminal_inputs()
    context = {"execution_owner": "default/workflow-retry-history"}
    await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    store = IssueClaimStore(sessions)
    receipt = await store.get(context["execution_owner"])
    prior = replace(
        parse_attempt_comment(receipt.comment_body).handoff,
        retry_history=(
            "admitted-attempt",
            "Prior retry evidence",
            *(f"prior-attempt-{index}" for index in range(history_size - 2)),
        ),
        retry_allowance=3,
        retry_remaining=1,
    )
    await publish_claim_comment(store, receipt, service, render_attempt_comment(prior))
    state["reject_release"] = True
    first = await tools.finalize_github_issue_failed_attempt(
        {
            **inputs,
            **(
                {"retryHistory": None, "retry_history": incoming}
                if alias == "nullable_camel"
                else {alias: incoming}
            ),
        },
        context,
        github_service_factory=lambda: service,
    )
    assert first.status == "FAILED", first.outputs
    expected = (
        list(prior.retry_history) + (["New verification retry"] if incoming else [])
    )[:20]
    receipt = await store.get(context["execution_owner"])
    pinned = receipt.finalization_json["plan"]
    assert pinned["attempt_handoff"]["retryHistory"] == expected
    assert f"Retry history: {'; '.join(expected)}." in pinned["terminal_comment"]
    if history_size == 20 and incoming:
        assert "1 additional retry history entry omitted" in pinned["terminal_comment"]
        assert "New verification retry" not in pinned["terminal_comment"]
    state["reject_release"] = False
    resumed = await tools.finalize_github_issue_failed_attempt(
        {**inputs, alias: ["Wrong later retry"]},
        context,
        github_service_factory=lambda: service,
    )
    assert resumed.status == "COMPLETED", resumed.outputs
    handoff = parse_attempt_comment(state["comments"][0]["body"]).handoff
    assert handoff.retry_history == tuple(expected)
    assert handoff.retry_allowance == prior.retry_allowance
    assert handoff.retry_remaining == prior.retry_remaining
    assert "Wrong later retry" not in state["comments"][0]["body"]
    assert (await store.get(context["execution_owner"])).finalization_json[
        "plan"
    ] == pinned


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt_at", ["plan", "release_pending", "result"])
@pytest.mark.parametrize("original_mode", ["parent_pr_and_merge", "pr_only_handoff"])
async def test_terminal_recovery_preserves_original_completion_and_observation(
    journey, monkeypatch, interrupt_at, original_mode
):
    from unittest.mock import AsyncMock

    state, service, sessions = journey
    inputs = _safe_terminal_inputs(completionMode=original_mode)
    context = {"execution_owner": "default/workflow-completion-receipt"}
    await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    original_record = IssueClaimStore.record_finalization
    attempted_result = {}

    async def interrupt(self, owner, phase, payload):
        if phase == "result":
            attempted_result.update(payload)
        if phase == interrupt_at:
            if phase == "plan":
                await original_record(self, owner, phase, payload)
            raise RuntimeError("fixture interrupted finalization")
        return await original_record(self, owner, phase, payload)

    state["reject_release"] = interrupt_at == "release_pending"
    state["lose_release_ack"] = interrupt_at == "result"
    with monkeypatch.context() as patch:
        patch.setattr(IssueClaimStore, "record_finalization", interrupt)
        if interrupt_at == "release_pending":
            first = await tools.finalize_github_issue_failed_attempt(
                inputs, context, github_service_factory=lambda: service
            )
            assert first.status == "FAILED", first.outputs
        else:
            with pytest.raises(RuntimeError, match="fixture interrupted finalization"):
                await tools.finalize_github_issue_failed_attempt(
                    inputs, context, github_service_factory=lambda: service
                )
    state["reject_release"] = False
    store = IssueClaimStore(sessions)
    observed_before = (
        await store.get(context["execution_owner"])
    ).finalization_json.get("observation")
    if interrupt_at == "release_pending":
        from dataclasses import replace

        from moonmind.workflows.temporal import github_issue_lifecycle

        assert observed_before is not None
        original_classify = github_issue_lifecycle.classify_mutation_outcome

        def reobserve(**kwargs):
            return replace(
                original_classify(**kwargs), detail="Equivalent later observation"
            )

        monkeypatch.setattr(
            github_issue_lifecycle, "classify_mutation_outcome", reobserve
        )
    writes = []
    if interrupt_at == "result":
        for name in (
            "create_issue_comment",
            "update_issue_comment",
            "add_issue_labels",
            "remove_issue_label",
            "close_issue",
        ):
            spy = AsyncMock(wraps=getattr(service, name))
            monkeypatch.setattr(service, name, spy)
            writes.append(spy)
    resumed = await tools.finalize_github_issue_failed_attempt(
        {
            **inputs,
            "completionMode": "pr_only_handoff"
            if original_mode == "parent_pr_and_merge"
            else "parent_pr_and_merge",
        },
        context,
        github_service_factory=lambda: service,
    )
    assert resumed.status == "COMPLETED", resumed.outputs
    assert resumed.outputs["completionRoute"] == original_mode
    assert resumed.outputs["mergeAuthorized"] is (
        original_mode == "parent_pr_and_merge"
    )
    assert resumed.outputs["mutationOutcome"] is not None
    if attempted_result:
        for field in (
            "mutationOutcome",
            "completionRoute",
            "mergeAuthorized",
            "summary",
            "commentId",
            "workspaceRetained",
        ):
            assert resumed.outputs[field] == attempted_result[field]
    if observed_before is not None:
        assert resumed.outputs["mutationOutcome"] == observed_before["mutationOutcome"]
        assert (await store.get(context["execution_owner"])).finalization_json[
            "observation"
        ] == observed_before
    for write in writes:
        write.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_unavailable", [False, True])
async def test_terminal_default_next_action_matches_canonical_and_prose(
    journey, runtime_unavailable
):
    from dataclasses import replace

    from moonmind.workflows.temporal.github_issue_attempts import (
        parse_attempt_comment,
        render_attempt_comment,
    )
    from moonmind.workflows.temporal.issue_claim_store import publish_claim_comment

    state, service, sessions = journey
    inputs = _safe_terminal_inputs()
    inputs.pop("nextAction")
    inputs["dispositionEvidence"] = {
        "trustworthy_no_work": True,
        "fresh_retry_allowed": True,
        "runtime_unavailable": runtime_unavailable,
    }
    context = {"execution_owner": "default/workflow-terminal-next-action"}
    await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    store = IssueClaimStore(sessions)
    receipt = await store.get(context["execution_owner"])
    prior = replace(
        parse_attempt_comment(receipt.comment_body).handoff, next_action="verify"
    )
    await publish_claim_comment(store, receipt, service, render_attempt_comment(prior))
    result = await tools.finalize_github_issue_failed_attempt(
        inputs, context, github_service_factory=lambda: service
    )
    assert result.status == "COMPLETED", result.outputs
    receipt = await store.get(context["execution_owner"])
    plan = receipt.finalization_json["plan"]
    assert plan["attempt_handoff"]["nextAction"] == "fresh_retry"
    assert "Next action: **fresh_retry**." in plan["terminal_comment"]
    assert (
        parse_attempt_comment(state["comments"][0]["body"]).handoff.next_action
        == "fresh_retry"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed_release", [False, True])
async def test_legacy_finalization_does_not_invent_completion_authority(
    journey, monkeypatch, confirmed_release
):
    _state, service, sessions = journey
    inputs = _safe_terminal_inputs(completionMode="pr_only_handoff")
    context = {"execution_owner": "default/workflow-legacy-completion"}
    await tools.load_github_issue_preset_brief(
        inputs, context, github_service_factory=lambda: service
    )
    original_record = IssueClaimStore.record_finalization
    crash_phase = "result" if confirmed_release else "plan"

    async def interrupt(self, owner, phase, payload):
        if phase == crash_phase:
            if phase == "plan":
                await original_record(self, owner, phase, payload)
            raise RuntimeError("fixture legacy interruption")
        return await original_record(self, owner, phase, payload)

    with monkeypatch.context() as patch:
        patch.setattr(IssueClaimStore, "record_finalization", interrupt)
        with pytest.raises(RuntimeError, match="fixture legacy interruption"):
            await tools.finalize_github_issue_failed_attempt(
                inputs, context, github_service_factory=lambda: service
            )
    store = IssueClaimStore(sessions)
    async with store.locked(context["execution_owner"]) as row:
        legacy = dict(row.finalization_json["plan"])
        legacy.pop("completion")
        # Real old receipts have neither completion provenance nor a separately
        # persisted label observation. Keep that absence explicit.
        row.finalization_json = {"plan": legacy}
    result = await tools.finalize_github_issue_failed_attempt(
        {**inputs, "completionMode": "parent_pr_and_merge"},
        context,
        github_service_factory=lambda: service,
    )
    assert result.status == "COMPLETED", result.outputs
    assert result.outputs["completionRoute"] == "needs_decision"
    assert result.outputs["mergeAuthorized"] is False
    if confirmed_release:
        assert result.outputs["mutationOutcome"] is None
    else:
        assert result.outputs["mutationOutcome"] is not None
    assert (await store.get(context["execution_owner"])).finalization_json[
        "plan"
    ] == legacy


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["plan", "observation", "result"])
async def test_finalization_receipt_phases_are_immutable_and_explicit(journey, phase):
    _state, service, sessions = journey
    context = {"execution_owner": "default/workflow-receipt-phases"}
    await tools.load_github_issue_preset_brief(
        _safe_terminal_inputs(), context, github_service_factory=lambda: service
    )
    store = IssueClaimStore(sessions)
    value = {"fixture": "confirmed evidence"}
    await store.record_finalization(context["execution_owner"], phase, value)
    await store.record_finalization(context["execution_owner"], phase, value)
    with pytest.raises(ValueError, match="claim_finalization_conflict"):
        await store.record_finalization(
            context["execution_owner"], phase, {"fixture": "changed evidence"}
        )
    with pytest.raises(ValueError, match="Unknown claim finalization phase"):
        await store.record_finalization(context["execution_owner"], "unknown", value)
    assert (await store.get(context["execution_owner"])).finalization_json == {phase: value}
