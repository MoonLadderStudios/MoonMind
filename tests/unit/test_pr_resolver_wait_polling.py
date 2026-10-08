"""Read-only CLI journeys through the real resolver and comment collectors."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# These journeys launch the real CLI/collectors; keep their complete coverage
# in the existing slow owner rather than the pure-Python fast shard.
pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
HEAD = "a" * 40


@pytest.fixture
def resolver_cli(tmp_path):
    """Replay only the GitHub transport, keeping all production helpers real."""
    transport = tmp_path / "transport"
    transport.mkdir()
    fixture = tmp_path / "github.json"
    calls = tmp_path / "calls.jsonl"
    state = {
        "pr": {
            "number": 1,
            "url": "https://github.com/owner/repo/pull/1",
            "title": "Resolver fixture",
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": HEAD,
            "headRefName": "feature",
            "baseRefName": "main",
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "UNSTABLE",
            "reviewDecision": "",
            "updatedAt": "2026-10-08T00:00:00Z",
            "statusCheckRollup": [],
        },
        "checks": [{"id": 1, "name": "unit", "status": "in_progress"}],
        "statuses": [],
        "issue_comments": [],
        "review_comments": [],
        "reviews": [],
        "runs": [],
        "jobs": [],
        "base_sha": "c" * 40,
        "protected": False,
        "required_checks": [],
    }
    (transport / "replay.py").write_text(
        "import json,os\n"
        "from pathlib import Path\n"
        "def read(kind, target):\n"
        " with open(os.environ['REPLAY_CALLS'], 'a') as out: out.write(json.dumps([kind,target])+'\\n')\n"
        " data=json.loads(Path(os.environ['REPLAY_FIXTURE']).read_text())\n"
        " if data.get('fail_endpoint') and data['fail_endpoint'] in target: raise RuntimeError('fixture transport unavailable')\n"
        " if kind=='view':\n"
        "  observed=[json.loads(line) for line in Path(os.environ['REPLAY_CALLS']).read_text().splitlines()]\n"
        "  full_reads=sum(k=='view' and 'isDraft' in t for k,t in observed)\n"
        "  return data.get('pr_after_inventory', data['pr']) if full_reads>1 else data['pr']\n"
        " path=target.split('?')[0].removeprefix('https://api.github.com/').removeprefix('/')\n"
        " if path=='graphql': return {'data':{'repository':{'pullRequest':{'reviewThreads':{'nodes':[], 'pageInfo':{'hasNextPage':False}}}}}}\n"
        " if path=='repos/owner/repo/branches/main':\n"
        "  observed=[json.loads(line) for line in Path(os.environ['REPLAY_CALLS']).read_text().splitlines()]\n"
        "  full_reads=sum(k=='view' and 'isDraft' in t for k,t in observed)\n"
        "  sha=data.get('base_after_inventory',data['base_sha']) if full_reads>1 else data['base_sha']\n"
        "  return {'protected':data['protected'],'commit':{'sha':sha}}\n"
        " if path.endswith('/branches/main/protection'):\n"
        "  observed=[json.loads(line) for line in Path(os.environ['REPLAY_CALLS']).read_text().splitlines()]\n"
        "  full_reads=sum(k=='view' and 'isDraft' in t for k,t in observed)\n"
        "  contexts=data.get('required_after_inventory',data['required_checks']) if full_reads>1 else data['required_checks']\n"
        "  return {'required_status_checks':{'contexts':contexts}}\n"
        " if path.endswith('/rules/branches/main'): return []\n"
        " if path.endswith('/check-runs'): return {'check_runs':data['checks']}\n"
        " if path.endswith('/statuses'): return data['statuses']\n"
        " if path.endswith('/actions/runs'): return {'workflow_runs':data['runs']}\n"
        " if '/actions/runs/' in path and path.endswith('/jobs'): return {'jobs':data['jobs']}\n"
        " if path.endswith('/pulls/1/commits'): return [{'sha':data['pr']['headRefOid']}]\n"
        " if '/commits/' in path: return {'commit':{'committer':{'date':'2026-10-07T00:00:00Z'}}}\n"
        " if path.endswith('/pulls/1/reviews'):\n"
        "  page=int(target.rsplit('page=',1)[-1]) if '&page=' in target else 1\n"
        "  return data['reviews'][(page-1)*100:page*100] if kind=='http' else data['reviews']\n"
        " if path.endswith('/issues/1/comments'):\n"
        "  if 'reviews_after_issue_comments' in data:\n"
        "   data['reviews']=data.pop('reviews_after_issue_comments')\n"
        "   Path(os.environ['REPLAY_FIXTURE']).write_text(json.dumps(data))\n"
        "  return data['issue_comments']\n"
        " if path.endswith('/pulls/1/comments'): return data['review_comments']\n"
        " if path.endswith('/reactions'): return []\n"
        " if path.endswith('/pulls/1'): return {'title':'Resolver fixture','html_url':data['pr']['url']}\n"
        " raise AssertionError((kind,target))\n"
    )
    gh = transport / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json,sys,os\nfrom pathlib import Path\nfrom replay import read\n"
        "args=sys.argv[1:]\n"
        "if args[:2]==['auth','token']: print('test-only-token')\n"
        "elif args[:2]==['pr','view']: print(json.dumps(read('view', ' '.join(args))))\n"
        "elif args[0]=='api':\n"
        " endpoint=next(x for x in args[1:] if x.startswith('repos/'))\n"
        " payload=read('api',endpoint)\n"
        " key=next((k for k in ['check_runs','workflow_runs','jobs'] if isinstance(payload,dict) and k in payload),None)\n"
        " if key and len(payload[key])>100:\n"
        "  for start in range(0,len(payload[key]),100): print(json.dumps({key:payload[key][start:start+100]}))\n"
        " else: print(json.dumps(payload))\n"
        " data=json.loads(Path(os.environ['REPLAY_FIXTURE']).read_text())\n"
        " if data.get('malformed_endpoint') and data['malformed_endpoint'] in endpoint: print('{}')\n"
        "else: raise AssertionError(args)\n"
    )
    gh.chmod(0o755)
    (transport / "sitecustomize.py").write_text(
        "import io,json,urllib.request\nfrom replay import read\n"
        "def urlopen(request, **kwargs):\n"
        " return io.BytesIO(json.dumps(read('http', request.full_url)).encode())\n"
        "urllib.request.urlopen=urlopen\n"
    )
    env = {
        **os.environ,
        "PATH": str(transport) + os.pathsep + os.environ["PATH"],
        "PYTHONPATH": os.pathsep.join([str(transport), str(ROOT)]),
        "REPLAY_FIXTURE": str(fixture),
        "REPLAY_CALLS": str(calls),
    }
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        env.pop(name, None)

    def run(*, review=False):
        fixture.write_text(json.dumps(state))
        calls.write_text("")
        command = [
            sys.executable,
            str(ROOT / ".agents/skills/pr-resolver/bin/pr_resolve_finalize.py"),
            "--pr",
            "1",
            "--dry-run",
            "--finish-mode",
            "fix_only",
            "--strict-exit-codes",
            "--review-provider",
            "codex" if review else "",
            "--require-fresh-review" if review else "--no-require-fresh-review",
        ]
        result = subprocess.run(
            command, cwd=tmp_path, env=env, text=True, capture_output=True, check=False
        )
        assert result.returncode in {0, 2}, result.stdout + result.stderr
        payload = json.loads((tmp_path / "var/pr_resolver/result.json").read_text())
        snapshot_path = tmp_path / "var/pr_resolver/snapshot.json"
        snapshot = (
            json.loads(snapshot_path.read_text()) if snapshot_path.exists() else {}
        )
        return (
            payload,
            snapshot,
            [json.loads(line) for line in calls.read_text().splitlines()],
        )

    return state, run


def test_repeated_ci_wait_skips_inventory_and_refreshes_before_remediation(
    resolver_cli,
):
    state, run = resolver_cli
    result, _, initial = run()
    assert result["reason"] == "ci_running"
    result, snapshot, waiting = run()
    assert result["reason"] == "ci_running"
    assert len(waiting) == 4  # PR, base commit, exact-head checks, legacy statuses.
    assert not any(kind == "http" for kind, _ in waiting)
    assert len(waiting) < len(initial)
    assert snapshot["observationOnly"] is True

    # A finding arrives while CI finishes. The cached clean inventory must
    # never authorize a merge or even a fix-only clean receipt.
    state["checks"][0].update(status="completed", conclusion="success")
    state["issue_comments"] = [
        {
            "id": 20,
            "user": {"login": "maintainer"},
            "body": "Fix the race",
            "created_at": "2026-10-08T01:00:00Z",
        }
    ]
    result, snapshot, transition = run()
    assert result["reason"] == "actionable_comments"
    assert snapshot.get("observationOnly") is not True
    assert any("/issues/1/comments" in target for _, target in transition)


@pytest.mark.parametrize(
    "change", ["head", "base", "policy", "pr_updated", "checks_unavailable"]
)
def test_changed_observation_never_reuses_the_comment_inventory(resolver_cli, change):
    state, run = resolver_cli
    run()
    if change == "head":
        state["pr"]["headRefOid"] = "b" * 40
    elif change == "base":
        # Identity changes force a full snapshot. The fixture branch transport
        # leaves requirements unknown, which must remain blocked.
        state["base_sha"] = "d" * 40
    elif change == "pr_updated":
        state["pr"]["updatedAt"] = "2026-10-08T02:00:00Z"
    elif change == "checks_unavailable":
        state["checks"] = []
    result, snapshot, calls = run(review=change == "policy")
    assert snapshot.get("observationOnly") is not True
    assert any(kind == "http" for kind, _ in calls)
    assert result["status"] != "review_clean"


def test_full_snapshot_reuses_ordered_reviews_and_collects_inventory_once(resolver_cli):
    state, run = resolver_cli
    state["checks"][0].update(status="completed", conclusion="success")
    state["reviews"] = [
        {
            "id": 50,
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "",
            "state": "COMMENTED",
            "commit_id": HEAD,
            "submitted_at": "2026-10-08T00:00:00Z",
        }
    ]
    result, snapshot, calls = run(review=True)
    assert result["status"] == "review_clean"
    assert snapshot["automatedReview"]["freshReviewForHead"] is True
    assert sum("/pulls/1/reviews" in target for _, target in calls) == 1
    assert sum("/issues/1/comments" in target for _, target in calls) == 1
    assert sum("/pulls/1/comments" in target for _, target in calls) == 1
    assert (
        sum(kind == "view" for kind, _ in calls) == 3
    )  # snapshot, helper, final head guard


def _actions_check(check_id, run_id, conclusion=None, *, name="ci-required"):
    return {
        "id": check_id,
        "name": name,
        "head_sha": HEAD,
        "app": {"id": 15368, "slug": "github-actions"},
        "details_url": f"https://github.com/owner/repo/actions/runs/{run_id}/job/{check_id}",
        "status": "completed" if conclusion else "queued",
        "conclusion": conclusion,
    }


def _workflow(run_id, number, *, status="completed", conclusion="success", attempt=1):
    return {
        "id": run_id,
        "head_sha": HEAD,
        "workflow_id": 10,
        "path": ".github/workflows/test.yml",
        "event": "pull_request",
        "run_number": number,
        "run_attempt": attempt,
        "status": status,
        "conclusion": conclusion,
        "pull_requests": [{"number": 1, "base": {"ref": "main", "sha": "c" * 40}}],
    }


def test_cli_reconciles_cancelled_run_only_for_the_same_check_identity(resolver_cli):
    state, run = resolver_cli
    state["checks"] = [
        _actions_check(1, 10, "cancelled"),
        _actions_check(2, 20, "success"),
    ]
    state["runs"] = [_workflow(10, 1, conclusion="cancelled"), _workflow(20, 2)]
    result, snapshot, _ = run()
    assert result["status"] == "review_clean"
    assert snapshot["ci"]["supersededChecks"][0]["checkId"] == 1
    state["checks"][1]["name"] = "different-check"
    result, snapshot, _ = run()
    assert result["reason"] == "ci_failures"
    assert not snapshot["ci"]["supersededChecks"]


@pytest.mark.parametrize("extra_degraded", [False, True])
def test_cli_reports_stranded_jobs_in_terminal_workflow_instead_of_waiting(
    resolver_cli,
    extra_degraded,
):
    state, run = resolver_cli
    state["checks"] = [_actions_check(1, 10)]
    state["runs"] = [_workflow(10, 1, status="in_progress", conclusion=None)]
    assert run()[0]["reason"] == "ci_running"
    _, snapshot, calls = run()
    assert snapshot["observationOnly"] is True
    assert len(calls) == 5
    state["runs"][0].update(status="completed", conclusion="failure")
    if extra_degraded:
        state["checks"].append({"id": 3, "name": "other", "status": "completed"})
    result, snapshot, calls = run()
    assert result["reason"] == "ci_workflow_terminal"
    assert result["next_step"] == "manual_review"
    assert not snapshot["ci"]["isRunning"]
    assert snapshot["ci"]["strandedChecks"][0]["workflowConclusion"] == "failure"
    assert any(kind == "http" for kind, _ in calls)


def test_cli_reconciles_attempts_only_after_verifying_job_identity(resolver_cli):
    state, run = resolver_cli
    state["checks"] = [
        _actions_check(1, 10, "cancelled"),
        _actions_check(2, 10, "success"),
    ]
    state["runs"] = [_workflow(10, 1, attempt=2)]
    state["jobs"] = [
        {
            "id": index,
            "run_id": 10,
            "run_attempt": index,
            "head_sha": HEAD,
            "check_run_url": f"https://api.github.com/repos/owner/repo/check-runs/{index}",
        }
        for index in [1, 2]
    ]
    result, snapshot, _ = run()
    assert result["status"] == "review_clean"
    assert snapshot["ci"]["supersededChecks"][0]["reason"] == "newer_run_attempt"
    state["jobs"][0][
        "check_run_url"
    ] = "https://api.github.com/repos/other/repo/check-runs/1"
    result, snapshot, _ = run()
    assert result["status"] != "review_clean"
    assert snapshot["ci"]["unresolvedChecks"]


def test_wait_only_evidence_can_never_authorize_a_terminal_or_remediation():
    from dataclasses import replace

    from pr_resolver_core import (
        CanonicalPullRequestSnapshot,
        ResolverAction,
        classify_snapshot,
    )

    observed = CanonicalPullRequestSnapshot(
        observation_only=True,
        checks_signal_available=True,
        checks_complete=False,
        actionable_comments=True,
    )
    assert classify_snapshot(observed).action is ResolverAction.WAIT
    for complete in [False, True]:
        decision = classify_snapshot(
            replace(
                observed, merged=True, checks_complete=complete, checks_passing=True
            )
        )
        assert decision.action in {
            ResolverAction.WAIT,
            ResolverAction.STOP_MANUAL_REVIEW,
        }


@pytest.mark.parametrize(
    "checks", [[], [{"id": 1, "name": "security", "status": "completed"}]]
)
def test_missing_or_unknown_ci_never_qualifies_as_clean(resolver_cli, checks):
    state, run = resolver_cli
    state["checks"] = checks
    result, snapshot, _ = run()
    assert result["status"] != "review_clean"
    assert snapshot["ci"]["signalQuality"] == "degraded"


def test_full_snapshot_revalidates_head_even_when_review_loop_is_disabled(resolver_cli):
    state, run = resolver_cli
    state["checks"][0].update(status="completed", conclusion="success")
    state["pr_after_inventory"] = {**state["pr"], "headRefOid": "b" * 40}
    result, snapshot, _ = run()
    assert result["status"] != "review_clean"
    assert not snapshot


@pytest.mark.parametrize("state", ["FAILURE", "QUEUED"])
def test_full_snapshot_rejects_ci_that_changes_during_inventory(resolver_cli, state):
    data, run = resolver_cli
    data["checks"][0].update(status="completed", conclusion="success")
    data["pr_after_inventory"] = {
        **data["pr"],
        "statusCheckRollup": [{"name": "unit", "state": state}],
    }
    result, snapshot, _ = run()
    assert result["reason"] == "snapshot_refresh_failed"
    assert not snapshot


def test_issue_reply_completion_refreshes_earlier_review_bodies(resolver_cli):
    state, run = resolver_cli
    state["checks"][0].update(status="completed", conclusion="success")
    state["issue_comments"] = [
        {
            "id": 1,
            "user": {"login": "maintainer"},
            "body": "@codex review",
            "created_at": "2026-10-08T00:00:00Z",
        },
        {
            "id": 2,
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "Codex Review: Didn't find any major issues. 🚀",
            "created_at": "2026-10-08T00:01:00Z",
        },
    ]
    state["reviews_after_issue_comments"] = [
        {
            "id": 3,
            "user": {"login": "chatgpt-codex-connector[bot]"},
            "body": "[P2] Preserve the pending retry.",
            "state": "COMMENTED",
            "commit_id": HEAD,
            "submitted_at": "2026-10-08T00:00:30Z",
        },
    ]
    result, snapshot, _ = run(review=True)
    assert result["reason"] == "actionable_comments"
    assert snapshot["commentsSummary"]["actionableCommentIds"] == [3]


@pytest.mark.parametrize("failure", ["fail_endpoint", "malformed_endpoint"])
def test_unavailable_checks_cannot_reuse_an_earlier_ci_wait(resolver_cli, failure):
    state, run = resolver_cli
    assert run()[0]["reason"] == "ci_running"
    state[failure] = "/check-runs"
    result, snapshot, calls = run()
    assert result["reason"] == "ci_signal_degraded"
    assert snapshot.get("observationOnly") is not True
    assert any(kind == "http" for kind, _ in calls)


def test_incomplete_thread_inventory_cannot_be_clean(resolver_cli):
    state, run = resolver_cli
    state["checks"][0].update(status="completed", conclusion="success")
    state["fail_endpoint"] = "graphql"
    result, snapshot, _ = run()
    assert result["reason"] == "comments_unavailable"
    assert not snapshot["commentsFetch"]["succeeded"]


def test_review_body_finding_on_second_page_still_blocks(resolver_cli):
    state, run = resolver_cli
    state["checks"][0].update(status="completed", conclusion="success")
    state["reviews"] = [
        {
            "id": n,
            "body": "",
            "state": "COMMENTED",
            "commit_id": "b" * 40,
            "submitted_at": "2026-10-07T00:00:00Z",
            "user": {"login": "chatgpt-codex-connector[bot]"},
        }
        for n in range(100)
    ] + [
        {
            "id": 101,
            "body": "[P2] Preserve data.",
            "state": "COMMENTED",
            "commit_id": HEAD,
            "submitted_at": "2026-10-08T00:01:00Z",
            "user": {"login": "chatgpt-codex-connector[bot]"},
        }
    ]
    result, snapshot, calls = run(review=True)
    assert result["reason"] == "actionable_comments"
    assert snapshot["commentsSummary"]["actionableCommentIds"] == [101]
    assert sum("/pulls/1/reviews" in target for _, target in calls) == 2


def test_workflow_and_job_attempt_evidence_reads_all_pages(resolver_cli):
    state, run = resolver_cli
    state["checks"] = [
        _actions_check(1, 10, "cancelled"),
        _actions_check(2, 10, "success"),
    ]
    state["runs"] = [_workflow(n, n) for n in range(100, 200)] + [
        _workflow(10, 1, attempt=2)
    ]
    state["jobs"] = [{"id": n} for n in range(100, 200)] + [
        {
            "id": n,
            "run_id": 10,
            "run_attempt": n,
            "head_sha": HEAD,
            "check_run_url": f"https://api.github.com/repos/owner/repo/check-runs/{n}",
        }
        for n in [1, 2]
    ]
    result, snapshot, _ = run()
    assert result["status"] == "review_clean"
    assert snapshot["ci"]["supersededChecks"][0]["reason"] == "newer_run_attempt"
    state["malformed_endpoint"] = "/jobs"
    result, snapshot, _ = run()
    assert result["status"] != "review_clean"
    assert snapshot["ci"]["unresolvedChecks"]


def test_other_pull_request_run_cannot_clear_the_target_failure(resolver_cli):
    state, run = resolver_cli
    state["checks"] = [
        _actions_check(1, 10, "cancelled"),
        _actions_check(2, 20, "success"),
    ]
    state["runs"] = [_workflow(10, 1, conclusion="cancelled"), _workflow(20, 2)]
    state["runs"][1]["pull_requests"][0]["number"] = 2
    result, snapshot, _ = run()
    assert result["reason"] == "ci_failures"
    assert not snapshot["ci"]["supersededChecks"]


def test_base_advance_invalidates_an_unchanged_head_wait(resolver_cli):
    state, run = resolver_cli
    assert run()[0]["reason"] == "ci_running"
    state["base_sha"] = "d" * 40
    result, snapshot, calls = run()
    assert result["reason"] == "ci_running"
    assert snapshot["pr"]["baseRefOid"] == "d" * 40
    assert snapshot.get("observationOnly") is not True
    assert any(kind == "http" for kind, _ in calls)


def test_base_change_during_inventory_cannot_authorize_completion(resolver_cli):
    state, run = resolver_cli
    state["checks"][0].update(status="completed", conclusion="success")
    state["base_after_inventory"] = "d" * 40
    result, snapshot, _ = run()
    assert result["reason"] == "snapshot_refresh_failed"
    assert not snapshot


def test_unknown_base_commit_cannot_authorize_completion(resolver_cli):
    state, run = resolver_cli
    state["checks"][0].update(status="completed", conclusion="success")
    state["base_sha"] = None
    result, snapshot, _ = run()
    assert result["reason"] == "snapshot_refresh_failed"
    assert not snapshot


def test_required_context_change_refreshes_an_unchanged_head_wait(resolver_cli):
    state, run = resolver_cli
    state["checks"][0].update(status="completed", conclusion="success")
    state["protected"] = True
    state["required_checks"] = ["external"]
    state["statuses"] = [{"context": "external", "state": "pending"}]
    assert run()[0]["reason"] == "ci_running"
    result, snapshot, calls = run()
    assert result["reason"] == "ci_running"
    assert snapshot["observationOnly"] is True
    wait_calls = calls
    state["required_checks"] = []
    result, snapshot, calls = run()
    assert result["status"] == "review_clean"
    assert snapshot.get("observationOnly") is not True
    assert any(kind == "http" for kind, _ in calls)
    assert len(wait_calls) == 6  # PR/base/checks/statuses plus protection and rules.


def test_requirement_change_during_inventory_cannot_authorize_completion(resolver_cli):
    state, run = resolver_cli
    state["checks"][0].update(status="completed", conclusion="success")
    state["protected"] = True
    state["required_after_inventory"] = ["new-security-check"]
    result, snapshot, _ = run()
    assert result["reason"] == "snapshot_refresh_failed"
    assert not snapshot
