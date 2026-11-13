"""Automatic Codex completion evidence at the portable Skill boundary."""

from __future__ import annotations

import copy
import json
import runpy
from datetime import datetime
from pathlib import Path

import pytest

from pr_resolver_core import (
    ResolverAction,
    classify_snapshot,
    normalize_portable_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests/fixtures/pr_resolver/codex_automatic_review_4773.json"


@pytest.fixture
def captured():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def snapshot_module():
    return runpy.run_path(
        str(REPO_ROOT / ".agents/skills/pr-resolver/bin/pr_resolve_snapshot.py")
    )


def _evidence(snapshot_module, monkeypatch, captured, **overrides):
    build = snapshot_module["build_automated_review_evidence"]

    # Isolate the evidence decision from GitHub IO; this is the full SHA
    # returned by the repository's commit endpoint for the captured abbreviation.
    def resolve_commit(**kwargs):
        matches = {
            commit["sha"]
            for commit in captured["commits"]
            if commit["sha"].startswith(kwargs["commit_ref"])
        }
        return captured["head_sha"] if matches == {captured["head_sha"]} else None

    monkeypatch.setitem(
        build.__globals__, "_resolve_automatic_review_commit", resolve_commit
    )

    def read_actor(command, *_args, **_kwargs):
        assert command == ["gh", "api", "users/chatgpt-codex-connector%5Bbot%5D"]
        return captured["provider_identity"]

    monkeypatch.setitem(build.__globals__, "run_command", read_actor)
    params = {
        "provider": "codex",
        "require_fresh_review": True,
        "pr_repo": captured["repository"],
        "pr_number": captured["pr_number"],
        "head_sha": captured["head_sha"],
        "head_committed_at": datetime.fromisoformat(
            captured["head_committed_at"].replace("Z", "+00:00")
        ),
        "comments": copy.deepcopy(captured["comments"]),
        "reviews": [],
        "reactions_for_request": [],
        "reactions_for_pr": copy.deepcopy(captured["reactions"]),
    }
    params.update(overrides)
    return build(**params)


def test_captured_pr4773_automatic_review_opens_the_current_head_gate(
    snapshot_module, monkeypatch, captured
):
    evidence = _evidence(snapshot_module, monkeypatch, captured)

    assert evidence["freshReviewForHead"] is True
    assert evidence["requestPending"] is False
    assert evidence["requestCommentId"] is None
    assert evidence["completionKind"] == "automatic_summary"
    assert evidence["completionId"] == 6082098611
    assert evidence["completedAt"] == "2026-10-09T13:45:42.886890+00:00"
    payload = {
        "repository": captured["repository"],
        "pr": {
            "number": captured["pr_number"],
            "state": "OPEN",
            "headRefOid": captured["head_sha"],
            "mergeStateStatus": "CLEAN",
            "mergeable": True,
        },
        "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
        "commentsFetch": {"succeeded": True},
        "commentsSummary": {
            "includeBotReviewComments": True,
            "hasActionableComments": False,
        },
        "automatedReview": evidence,
    }
    assert (
        classify_snapshot(normalize_portable_snapshot(payload)).action
        is ResolverAction.ATTEMPT_MERGE
    )


@pytest.fixture(params=["markdown", "details"])
def summary_format(request):
    return request.param


def _current_format_capture(summary_format="markdown"):
    captured = json.loads(
        FIXTURE.with_name("codex_automatic_review_with_state.json").read_text()
    )
    if summary_format == "details":
        comment = captured["comments"][0]
        comment["body"] = (
            comment["body"].replace(
                "## Codex Review Summary",
                "<details>\n<summary><strong>Codex Review Summary</strong>: ✅ Completed</summary>",
            )
            + "\n\n</details>"
        )
    return captured


@pytest.mark.parametrize("full_table_sha", [False, True])
def test_captured_machine_state_summary_qualifies_the_exact_reviewed_head(
    snapshot_module, monkeypatch, full_table_sha, summary_format
):
    captured = _current_format_capture(summary_format)
    if full_table_sha:
        captured["comments"][0]["body"] = captured["comments"][0]["body"].replace(
            "`17799e0`", "`" + captured["head_sha"] + "`"
        )
    evidence = _evidence(snapshot_module, monkeypatch, captured)
    assert evidence["freshReviewForHead"] is True
    assert evidence["completionKind"] == "automatic_summary"
    assert evidence["completionId"] == 6639737323
    assert evidence["automaticSummary"]["reviewedCommitSha"] == captured["head_sha"]
    assert evidence["automaticSummary"]["reactionId"] == 625970988
    assert evidence["requestPending"] is False
    assert evidence["requestFailed"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("commit_sha", "a" * 40),
        ("commit_sha", "17799e0" + "a" * 33),
        ("commit_sha", "17799e0"),
        ("commit_sha", None),
        ("status", "running"),
        ("status", "failed"),
        ("status", None),
        ("has_any_findings", True),
        ("has_any_findings", None),
        ("has_any_findings", "false"),
        ("has_any_findings", 0),
        ("has_unresolved_findings", True),
        ("has_unresolved_findings", None),
        ("has_unresolved_findings", "false"),
        ("has_unresolved_findings", 0),
    ],
)
def test_machine_state_cannot_disagree_with_clean_current_head_completion(
    snapshot_module, monkeypatch, field, value, summary_format
):
    captured = _current_format_capture(summary_format)
    summary = captured["comments"][0]
    lines = summary["body"].splitlines()
    prefix, suffix = "<!-- codex-review-state:v1 ", " -->"
    state = json.loads(lines[1][len(prefix) : -len(suffix)])
    state["code_review"][field] = value
    lines[1] = prefix + json.dumps(state) + suffix
    summary["body"] = "\n".join(lines)
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


@pytest.mark.parametrize(
    "fault",
    [
        "malformed",
        "unknown-version",
        "duplicate",
        "array",
        "missing-code-review",
        "nonobject-code-review",
        "duplicate-json-key",
    ],
)
def test_malformed_or_ambiguous_machine_state_cannot_complete(
    snapshot_module, monkeypatch, fault, summary_format
):
    captured = _current_format_capture(summary_format)
    lines = captured["comments"][0]["body"].splitlines()
    if fault == "malformed":
        lines[1] = "<!-- codex-review-state:v1 {invalid} -->"
    elif fault == "unknown-version":
        lines[1] = lines[1].replace(":v1 ", ":v2 ")
    elif fault == "duplicate":
        lines.insert(1, lines[1])
    elif fault == "array":
        lines[1] = "<!-- codex-review-state:v1 [] -->"
    elif fault == "missing-code-review":
        lines[1] = "<!-- codex-review-state:v1 {} -->"
    elif fault == "nonobject-code-review":
        lines[1] = '<!-- codex-review-state:v1 {"code_review":"completed"} -->'
    else:
        lines[1] = lines[1].replace(
            '"has_any_findings":false',
            '"has_any_findings":true,"has_any_findings":false',
        )
    captured["comments"][0]["body"] = "\n".join(lines)
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


@pytest.mark.parametrize(
    "blocker",
    ["reaction-absent", "human", "wrong-repo", "later-refusal", "later-request"],
)
def test_machine_state_does_not_replace_independent_review_authority(
    snapshot_module, monkeypatch, blocker, summary_format
):
    captured = _current_format_capture(summary_format)
    if blocker == "reaction-absent":
        captured["reactions"] = []
    elif blocker == "human":
        captured["comments"][0]["user"] = "operator"
    elif blocker == "wrong-repo":
        captured["comments"][0]["url"] = captured["comments"][0]["url"].replace(
            "MoonLadderStudios/MoonMind", "other/repo"
        )
    else:
        refusal = blocker == "later-refusal"
        captured["comments"].append(
            _feedback(
                captured,
                body=(
                    "You have reached your Codex usage limits for code reviews."
                    if refusal
                    else "@codex review"
                ),
                at="2026-11-04T19:16:20Z",
                user="chatgpt-codex-connector[bot]" if refusal else "operator",
            )
        )
    evidence = _evidence(snapshot_module, monkeypatch, captured)
    assert evidence["freshReviewForHead"] is False
    if blocker == "later-refusal":
        assert evidence["requestFailed"] is True
    elif blocker == "later-request":
        assert evidence["requestPending"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("user", "operator"),
        ("user", "gemini-code-assist[bot]"),
        ("user", {"login": "chatgpt-codex-connector", "type": "User"}),
        ("user", {"login": "chatgpt-codex-connector[bot]", "type": "User"}),
        ("url", "https://github.com/other/repo/pull/4773#issuecomment-6082098611"),
        (
            "url",
            "https://github.com/MoonLadderStudios/MoonMind/pull/4772#issuecomment-6082098611",
        ),
        (
            "url",
            "https://github.com.evil.test/MoonLadderStudios/MoonMind/pull/4773#issuecomment-6082098611",
        ),
        ("url", None),
        ("id", 6082098612),
        ("created_at", "2026-10-09T13:45:50Z"),
        ("updated_at", None),
        ("updated_at", "2026-10-09T13:45:41Z"),
        ("updated_at", "2026-10-09T13:45:47Z"),
    ],
)
def test_unqualified_summary_metadata_cannot_complete(
    snapshot_module, monkeypatch, captured, field, value
):
    captured["comments"][0][field] = value
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("**Completed**", "**In Progress**"),
        ("**Completed**", "**Failed**"),
        ("7f81b48", "aaaaaaaa"),
        ("7f81b48", "7f81"),
        ("7f81b48", "7f81b48notasha"),
        ("13:45:42.886890Z</relative-time>", "13:45:43.886890Z</relative-time>"),
        ("13:45:42.886890Z", "13:45:42.886890"),
        ("<!-- codex-pull-request-review-summary -->", "Quoted example:"),
        ("📝 **Code Review**", "📝 **Task Execution**"),
    ],
)
def test_noncompletion_or_wrong_commit_is_rejected(
    snapshot_module, monkeypatch, captured, old, new
):
    summary = captured["comments"][0]
    summary["body"] = summary["body"].replace(old, new)
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


@pytest.mark.parametrize("form", ["quote", "indent", "fence", "P1", "P2", "unmarked"])
def test_embedded_or_finding_bearing_summary_is_not_clean(
    snapshot_module, monkeypatch, captured, form
):
    summary = captured["comments"][0]
    if form == "quote":
        summary["body"] = "\n".join(
            "> " + line for line in summary["body"].splitlines()
        )
    elif form == "indent":
        summary["body"] = "\n".join(
            "    " + line for line in summary["body"].splitlines()
        )
    elif form == "fence":
        fence = chr(96) * 3
        summary["body"] = fence + "html\n" + summary["body"] + "\n" + fence
    else:
        summary["body"] += "\n" + (
            f"[{form}] Fix the guard" if form != "unmarked" else "Fix the guard"
        )
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("user", {"login": "operator", "type": "User"}),
        ("user", {"login": "gemini-code-assist[bot]"}),
        ("user", {"login": "chatgpt-codex-connector", "type": "User"}),
        ("content", "eyes"),
        ("content", "heart"),
        ("created_at", "2026-10-09T13:45:41Z"),
        ("created_at", "2026-10-09T13:45:43Z"),
        ("created_at", "2026-10-09T13:45:44Z"),
        ("created_at", None),
        ("id", None),
    ],
)
def test_unqualified_or_stale_reaction_cannot_complete(
    snapshot_module, monkeypatch, captured, field, value
):
    captured["reactions"][0][field] = value
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


def test_summary_without_no_findings_reaction_cannot_complete(
    snapshot_module, monkeypatch, captured
):
    assert (
        _evidence(snapshot_module, monkeypatch, captured, reactions_for_pr=[])[
            "freshReviewForHead"
        ]
        is False
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("login", "operator"),
        ("id", 1),
        ("node_id", "human-node"),
        ("type", "User"),
    ],
)
def test_reaction_user_type_requires_matching_authoritative_bot_identity(
    snapshot_module, monkeypatch, captured, field, value
):
    captured["provider_identity"][field] = value
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


def test_reaction_bot_identity_lookup_failure_stays_observable(
    snapshot_module, monkeypatch, captured
):
    verify = snapshot_module["_verify_automatic_reaction_actors"]

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("GitHub bot account lookup unavailable")

    monkeypatch.setitem(verify.__globals__, "run_command", unavailable)
    with pytest.raises(RuntimeError, match="GitHub bot account lookup unavailable"):
        verify(provider="codex", reactions=captured["reactions"])


def test_reaction_identity_is_verified_once_per_collection_without_mutating_raw_evidence(
    snapshot_module, monkeypatch, captured
):
    verify = snapshot_module["_verify_automatic_reaction_actors"]
    reads = []

    def read_actor(command, *_args, **_kwargs):
        reads.append(command)
        return captured["provider_identity"]

    monkeypatch.setitem(verify.__globals__, "run_command", read_actor)
    raw = captured["reactions"] * 2
    verified = verify(provider="codex", reactions=raw)
    assert len(reads) == 1
    assert all(reaction["user"]["type"] == "Bot" for reaction in verified)
    assert all(reaction["user"]["type"] == "User" for reaction in raw)


@pytest.mark.parametrize("when", ["2026-10-09T13:45:46Z", "2026-10-09T13:45:47Z"])
def test_newer_review_start_supersedes_old_thumb(
    snapshot_module, monkeypatch, captured, when
):
    captured["reactions"].append(
        {
            "id": 556689013,
            "content": "eyes",
            "created_at": when,
            "user": {"login": "chatgpt-codex-connector[bot]"},
        }
    )
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


def _feedback(captured, *, body, at, user="chatgpt-codex-connector[bot]"):
    return {
        "id": 6082098612,
        "type": "issue_comment",
        "user": user,
        "body": body,
        "created_at": at,
        "updated_at": at,
        "url": f"https://github.com/{captured['repository']}/pull/{captured['pr_number']}#issuecomment-6082098612",
    }


@pytest.mark.parametrize("change", ["started", "wrong_head", "malformed"])
def test_latest_summary_is_selected_before_qualification(
    snapshot_module, monkeypatch, captured, change
):
    newer = copy.deepcopy(captured["comments"][0])
    newer.update(_feedback(captured, body=newer["body"], at="2026-10-09T13:46:00Z"))
    if change == "started":
        newer["body"] = newer["body"].replace("**Completed**", "**In Progress**")
    elif change == "wrong_head":
        newer["body"] = newer["body"].replace("7f81b48", "aaaaaaaa")
    else:
        newer["body"] = newer["body"].replace("| Review |", "| Unknown |")
    captured["comments"].append(newer)
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )


@pytest.mark.parametrize("edited", [False, True])
def test_later_provider_refusal_supersedes_automatic_completion(
    snapshot_module, monkeypatch, captured, edited
):
    refusal = _feedback(
        captured,
        body="You have reached your Codex usage limits for code reviews.",
        at="2026-10-09T13:45:47Z",
    )
    if edited:
        refusal["created_at"] = "2026-10-09T13:45:41Z"
    captured["comments"].append(refusal)
    evidence = _evidence(snapshot_module, monkeypatch, captured)
    assert evidence["freshReviewForHead"] is False
    assert evidence["requestFailed"] is True
    assert evidence["requestFailure"]["providerErrorClass"] == "rate_limit"
    assert evidence["requestFailure"]["failedAt"] == "2026-10-09T13:45:47+00:00"


def test_newer_completed_summary_can_supersede_earlier_refusal(
    snapshot_module, monkeypatch, captured
):
    captured["comments"].append(
        _feedback(
            captured,
            body="Codex usage limits have been reached for code reviews.",
            at="2026-10-09T13:41:00Z",
        )
    )
    evidence = _evidence(snapshot_module, monkeypatch, captured)
    assert evidence["freshReviewForHead"] is True
    assert evidence["requestFailed"] is False


def test_new_explicit_request_supersedes_automatic_completion(
    snapshot_module, monkeypatch, captured
):
    captured["comments"].append(
        _feedback(
            captured,
            body="@codex review",
            at="2026-10-09T13:45:47Z",
            user="operator",
        )
    )
    evidence = _evidence(snapshot_module, monkeypatch, captured)
    assert evidence["freshReviewForHead"] is False
    assert evidence["requestPending"] is True
    assert evidence["requestCommentId"] == 6082098612


@pytest.mark.parametrize("created_at", [None, "invalid", "2026-10-09T13:45:47"])
def test_request_with_unknown_chronology_cannot_reuse_automatic_completion(
    snapshot_module, monkeypatch, captured, created_at
):
    request = _feedback(
        captured, body="@codex review", at="2026-10-09T13:45:47Z", user="operator"
    )
    request["created_at"] = created_at
    captured["comments"].append(request)
    with pytest.raises(RuntimeError, match="explicit review request chronology"):
        _evidence(snapshot_module, monkeypatch, captured)


def test_later_request_cannot_be_hidden_by_future_git_commit_date(
    snapshot_module, monkeypatch, captured
):
    captured["comments"].append(
        _feedback(
            captured, body="@codex review", at="2026-10-09T13:45:47Z", user="operator"
        )
    )
    evidence = _evidence(
        snapshot_module,
        monkeypatch,
        captured,
        head_committed_at=datetime.fromisoformat("2027-01-01T00:00:00+00:00"),
    )
    assert evidence["freshReviewForHead"] is False
    assert evidence["requestPending"] is True
    assert evidence["requestCommentId"] == 6082098612


@pytest.mark.parametrize(
    "variant", ["full_sha", "reused_comment", "same_second_update", "precise_update"]
)
def test_qualified_summary_variants_preserve_exact_head_completion(
    snapshot_module, monkeypatch, captured, variant
):
    if variant == "full_sha":
        captured["comments"][0]["body"] = captured["comments"][0]["body"].replace(
            "7f81b48", captured["head_sha"]
        )
    elif variant == "reused_comment":
        captured["comments"][0]["created_at"] = "2026-10-08T13:40:47Z"
    elif variant == "same_second_update":
        captured["comments"][0]["updated_at"] = "2026-10-09T13:45:42Z"
    else:
        captured["comments"][0]["updated_at"] = "2026-10-09T13:45:42.886890Z"
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is True
    )


@pytest.mark.parametrize("edited", [False, True])
def test_newer_unclassified_provider_feedback_cannot_reuse_old_completion(
    snapshot_module, monkeypatch, captured, edited
):
    feedback = _feedback(
        captured, body="The review is still running.", at="2026-10-09T13:45:47Z"
    )
    if edited:
        feedback["created_at"] = "2026-10-09T13:45:41Z"
    captured["comments"].append(feedback)
    evidence = _evidence(snapshot_module, monkeypatch, captured)
    assert evidence["freshReviewForHead"] is False
    assert evidence["requestFailed"] is False


@pytest.mark.parametrize("head_date", [None, "2027-01-01T00:00:00+00:00"])
def test_exact_automatic_completion_does_not_trust_author_controlled_commit_dates(
    snapshot_module, monkeypatch, captured, head_date
):
    build = snapshot_module["build_automated_review_evidence"]
    monkeypatch.setitem(
        build.__globals__, "_fetch_head_commit_timestamp", lambda **kwargs: None
    )
    at = datetime.fromisoformat(head_date) if head_date else None
    assert (
        _evidence(snapshot_module, monkeypatch, captured, head_committed_at=at)[
            "freshReviewForHead"
        ]
        is True
    )


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "ambiguous",
        "wrong_resolution",
        "wrong_repo",
        "wrong_pr",
        "changed_head",
        "incomplete",
        "malformed",
        "unavailable",
    ],
)
def test_abbreviated_commit_requires_authoritative_disambiguation(
    snapshot_module, monkeypatch, captured, change
):
    resolve = snapshot_module["_resolve_automatic_review_commit"]
    scope = resolve.__globals__
    pull = {
        "number": captured["pr_number"],
        "head": {"sha": captured["head_sha"]},
        "base": {"repo": {"full_name": captured["repository"]}},
        "commits": len(captured["commits"]),
    }
    commits = copy.deepcopy(captured["commits"])
    resolved = {"sha": captured["head_sha"]}
    if change == "ambiguous":
        commits.append({"sha": captured["head_sha"][:7] + "c" * 33})
        pull["commits"] += 1
    elif change == "wrong_resolution":
        resolved["sha"] = "b" * 40
    elif change == "wrong_repo":
        pull["base"]["repo"]["full_name"] = "other/repo"
    elif change == "wrong_pr":
        pull["number"] += 1
    elif change == "changed_head":
        pull["head"]["sha"] = "b" * 40
    elif change == "incomplete":
        pull["commits"] += 1
    elif change == "malformed":
        commits[0]["sha"] = "not-a-sha"

    def read(command, *_args, **_kwargs):
        if change == "unavailable":
            raise RuntimeError("GitHub evidence unavailable")
        return resolved if "/commits/" in command[-1] else pull

    monkeypatch.setitem(scope, "run_command", read)
    monkeypatch.setitem(scope, "_fetch_review_collection", lambda endpoint: commits)
    params = {
        "pr_repo": captured["repository"],
        "pr_number": captured["pr_number"],
        "commit_ref": captured["head_sha"][:7],
        "head_sha": captured["head_sha"],
    }
    if change in {"incomplete", "malformed", "unavailable"}:
        with pytest.raises(RuntimeError):
            resolve(**params)
    else:
        assert resolve(**params) == (captured["head_sha"] if change == "none" else None)


@pytest.mark.parametrize("change", ["complete", "incomplete", "ambiguous"])
def test_large_pr_commit_inventory_uses_complete_exact_sha_comparison(
    snapshot_module, monkeypatch, captured, change
):
    resolve = snapshot_module["_resolve_automatic_review_commit"]
    head = captured["head_sha"]
    base = "b" * 40
    commits = [{"sha": f"{i:040x}"} for i in range(300)] + [{"sha": head}]
    pull = {
        "number": captured["pr_number"],
        "head": {"sha": head},
        "base": {"sha": base, "repo": {"full_name": captured["repository"]}},
        "commits": len(commits),
    }
    if change == "incomplete":
        commits = commits[:-1]
    elif change == "ambiguous":
        commits.append({"sha": head[:7] + "c" * 33})
        pull["commits"] += 1
    reads = []

    def read(command, *_args, **kwargs):
        reads.append(command[-1])
        if "/compare/" in command[-1]:
            assert command == [
                "gh",
                "api",
                "--paginate",
                f"repos/{captured['repository']}/compare/{base}...{head}?per_page=100",
            ]
            assert kwargs["paginated"] is True
            assert kwargs["records_key"] == "commits"
            return commits
        return {"sha": head} if "/commits/" in command[-1] else pull

    monkeypatch.setitem(resolve.__globals__, "run_command", read)
    monkeypatch.setitem(
        resolve.__globals__, "_fetch_review_collection", lambda endpoint: commits[:250]
    )
    params = {
        "pr_repo": captured["repository"],
        "pr_number": captured["pr_number"],
        "commit_ref": head[:7],
        "head_sha": head,
    }
    if change == "incomplete":
        with pytest.raises(RuntimeError, match="Incomplete PR commit inventory"):
            resolve(**params)
    else:
        assert resolve(**params) == (head if change == "complete" else None)
    assert any("/compare/" in endpoint for endpoint in reads)


@pytest.mark.parametrize("second_page", ["complete", "missing_key", "non_object"])
def test_required_command_reads_every_object_page_or_fails(
    snapshot_module, monkeypatch, second_page
):
    first = {"commits": [{"sha": "a" * 40}]}
    last = {"commits": [{"sha": "b" * 40}]}
    if second_page == "missing_key":
        last = {"other": []}
    elif second_page == "non_object":
        last = []
    result = snapshot_module["subprocess"].CompletedProcess(
        args=["gh"],
        returncode=0,
        stdout=json.dumps(first) + "\n" + json.dumps(last),
        stderr="",
    )
    monkeypatch.setattr(snapshot_module["subprocess"], "run", lambda *_a, **_k: result)
    run = snapshot_module["run_command"]
    command = ["gh", "api", "--paginate", "repos/example/repo/compare/base...head"]
    if second_page == "complete":
        assert run(command, paginated=True, records_key="commits") == [
            {"sha": "a" * 40},
            {"sha": "b" * 40},
        ]
    else:
        with pytest.raises(SystemExit):
            run(command, paginated=True, records_key="commits")


@pytest.mark.parametrize(
    "change",
    [
        "findings",
        "issue_findings",
        "head",
        "restart",
        "missing_inventory",
        "refusal",
        "request_unknown",
    ],
)
def test_snapshot_refreshes_automatic_completion_before_opening_gate(
    snapshot_module, monkeypatch, tmp_path, captured, change
):
    main = snapshot_module["main"]
    scope = main.__globals__
    head = captured["head_sha"]
    checks = [{"name": "unit", "status": "COMPLETED", "conclusion": "SUCCESS"}]
    pr = {
        "number": captured["pr_number"],
        "state": "OPEN",
        "headRefOid": head,
        "mergeStateStatus": "CLEAN",
        "mergeable": True,
        "url": f"https://github.com/{captured['repository']}/pull/{captured['pr_number']}",
        "statusCheckRollup": checks,
    }
    pr_reads = []

    def fetch_pr(_selector):
        pr_reads.append(True)
        return (
            (
                {**pr, "headRefOid": "b" * 40}
                if change == "head" and len(pr_reads) > 1
                else pr
            ),
            str(pr["number"]),
            [],
        )

    monkeypatch.setitem(scope, "fetch_pr_data", fetch_pr)
    monkeypatch.setitem(
        scope, "_fetch_base_branch", lambda **kwargs: {"commit": {"sha": "base-head"}}
    )
    monkeypatch.setitem(scope, "_fetch_required_status_checks", lambda **kwargs: [])
    monkeypatch.setitem(scope, "_fetch_commit_check_runs", lambda **kwargs: checks)
    monkeypatch.setitem(scope, "_fetch_commit_statuses", lambda **kwargs: [])
    monkeypatch.setitem(scope, "_fetch_previous_commit_sha", lambda **kwargs: None)
    monkeypatch.setitem(
        scope,
        "_fetch_head_commit_timestamp",
        lambda **kwargs: datetime.fromisoformat(
            captured["head_committed_at"].replace("Z", "+00:00")
        ),
    )
    monkeypatch.setitem(
        scope, "_resolve_automatic_review_commit", lambda **kwargs: head
    )
    monkeypatch.setitem(
        scope, "_fetch_pr_reactions", lambda **kwargs: captured["reactions"]
    )
    reads = []

    def read_comments(command, *_args, **_kwargs):
        if command[-1] == "users/chatgpt-codex-connector%5Bbot%5D":
            return captured["provider_identity"]
        reads.append(True)
        if len(reads) > 1 and change == "missing_inventory":
            return {}
        comments = copy.deepcopy(captured["comments"])
        if len(reads) > 1:
            if change in {"findings", "issue_findings"}:
                finding = {
                    "id": 51,
                    "type": "review_comment",
                    "user": "chatgpt-codex-connector[bot]",
                    "body": "[P1] Fix the authorization check",
                    "commit_id": head,
                    "created_at": "2026-10-09T13:45:47Z",
                }
                if change == "issue_findings":
                    finding.update(
                        _feedback(
                            captured, body=finding["body"], at=finding["created_at"]
                        )
                    )
                comments.append(finding)
            elif change == "restart":
                comments[0]["body"] = comments[0]["body"].replace(
                    "**Completed**", "**In Progress**"
                )
                comments[0]["updated_at"] = "2026-10-09T13:45:47Z"
            elif change == "refusal":
                comments.append(
                    _feedback(
                        captured,
                        body="You have reached your Codex usage limits for code reviews.",
                        at="2026-10-09T13:45:47Z",
                    )
                )
            elif change == "request_unknown":
                request = _feedback(
                    captured,
                    body="@codex review",
                    at="2026-10-09T13:45:47Z",
                    user="operator",
                )
                request["created_at"] = None
                comments.append(request)
        return {"comments": comments, "reviews": [], "thread_inventory_complete": True}

    monkeypatch.setitem(scope, "run_command", read_comments)
    path = tmp_path / "snapshot.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_snapshot.py",
            "--pr",
            str(pr["number"]),
            "--review-provider",
            "codex",
            "--require-fresh-review",
            "--snapshot-path",
            str(path),
        ],
    )
    if change == "head":
        with pytest.raises(SystemExit):
            main()
        assert not path.exists()
        return
    if change == "request_unknown":
        with pytest.raises(RuntimeError, match="explicit review request chronology"):
            main()
        assert not path.exists()
        return
    main()
    snapshot = json.loads(path.read_text())
    assert len(reads) >= 2
    decision = classify_snapshot(normalize_portable_snapshot(snapshot))
    assert decision.action is not ResolverAction.ATTEMPT_MERGE
    if change in {"findings", "issue_findings"}:
        assert snapshot["automatedReview"]["freshReviewForHead"] is True
        assert decision.remediation_skill == "fix-comments"
    elif change == "refusal":
        assert decision.reason_code == "automated_review_request_failed"
    elif change == "missing_inventory":
        assert decision.reason_code == "comments_unavailable"
    else:
        assert snapshot["automatedReview"]["freshReviewForHead"] is False


def _details_capture():
    return json.loads(
        FIXTURE.with_name("codex_automatic_review_details.json").read_text()
    )


def test_captured_details_summary_qualifies_exact_head(snapshot_module, monkeypatch):
    captured = _details_capture()
    evidence = _evidence(snapshot_module, monkeypatch, captured)
    assert evidence["freshReviewForHead"] is True
    assert evidence["completionId"] == 6821038011
    assert evidence["automaticSummary"]["reviewedCommitSha"] == captured["head_sha"]
    assert evidence["automaticSummary"]["reactionId"] == 648367708


@pytest.mark.parametrize(
    "fault",
    [
        "wrong-author",
        "wrong-pr",
        "missing-reaction",
        "stale-reaction",
        "newer-start",
        "newer-failure",
        "missing-close",
        "extra-close",
        "wrong-wrapper-status",
        "unknown-content",
        "missing-state",
    ],
)
def test_details_summary_preserves_authority_and_structure(
    snapshot_module, monkeypatch, fault
):
    captured = _details_capture()
    comment = captured["comments"][0]
    if fault == "wrong-author":
        comment["user"] = "operator"
    elif fault == "wrong-pr":
        comment["url"] = comment["url"].replace("/5306#", "/5305#")
    elif fault == "missing-reaction":
        captured["reactions"] = []
    elif fault == "stale-reaction":
        captured["reactions"][0]["created_at"] = "2026-11-12T10:26:35Z"
    elif fault in ("newer-start", "newer-failure"):
        newer = copy.deepcopy(comment)
        newer.update(
            _feedback(captured, body=comment["body"], at="2026-11-12T10:27:00Z")
        )
        newer["body"] = newer["body"].replace(
            "Completed", "In Progress" if fault == "newer-start" else "Failed"
        )
        captured["comments"].append(newer)
    elif fault == "missing-close":
        comment["body"] = comment["body"].rsplit("</details>", 1)[0]
    elif fault == "extra-close":
        comment["body"] += "\n</details>"
    elif fault == "wrong-wrapper-status":
        comment["body"] = comment["body"].replace(
            "✅ Completed</summary>", "❌ Failed</summary>"
        )
    elif fault == "missing-state":
        comment["body"] = "\n".join(
            line
            for line in comment["body"].splitlines()
            if not line.startswith("<!-- codex-review-state:")
        )
    else:
        comment["body"] = comment["body"].replace(
            "\n</details>", "\nFix the guard\n</details>"
        )
    assert (
        _evidence(snapshot_module, monkeypatch, captured)["freshReviewForHead"] is False
    )
