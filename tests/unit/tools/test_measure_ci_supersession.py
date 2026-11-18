"""Superseded CI work measured from saved GitHub Actions run/job metadata."""

from __future__ import annotations

import itertools
import json

import pytest

from tools.ci.measure_ci_supersession import fetch, main, measure


def _run(run_id, created, updated, *, conclusion="success", branch="pr-a", **extra):
    return {
        "id": run_id,
        "run_attempt": 1,
        "event": "pull_request",
        "head_branch": branch,
        "head_repository": {"full_name": "owner/repo"},
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "created_at": f"2026-11-18T{created}Z",
        "updated_at": f"2026-11-18T{updated}Z",
        **extra,
    }


_JOB_IDS = itertools.count(1)


def _job(name, created, started=None, completed=None, *, conclusion="success"):
    executed = started is not None
    return {
        "name": name,
        "id": next(_JOB_IDS),
        "html_url": "https://example.test/job",
        "status": "completed" if completed else "in_progress",
        "conclusion": conclusion if completed else None,
        "runner_id": 7 if executed else 0,
        "created_at": f"2026-11-18T{created}Z",
        # GitHub supplies a placeholder start even for never-allocated jobs.
        "started_at": f"2026-11-18T{started or created}Z",
        "completed_at": f"2026-11-18T{completed}Z" if completed else None,
        "steps": (
            [
                {
                    "name": "Set up job",
                    "conclusion": "success",
                    "started_at": f"2026-11-18T{started}Z",
                    "completed_at": f"2026-11-18T{started}Z",
                }
            ]
            if executed
            else []
        ),
    }


def _ungated_jobs():
    # Heavy owners queue once selection ends, beside the policy prerequisite.
    return [
        _job("select-test-suites", "10:00:01", "10:00:02", "10:00:04"),
        _job("preflight-policy", "10:00:05", "10:00:10", "10:03:10"),
        _job(
            "integration-hermetic (hermetic)",
            "10:00:05",
            "10:00:20",
            "10:05:00",
            conclusion="cancelled",
        ),
        _job(
            "backend-matrix (reliability-shard-1, 1, 20)",
            "10:00:05",
            "10:00:30",
            "10:05:00",
            conclusion="cancelled",
        ),
        _job(
            "prepare-app-image",
            "10:00:05",
            completed="10:05:00",
            conclusion="cancelled",
        ),
        _job("frontend-static", "10:00:05", "10:00:15", "10:02:00"),
    ]


def test_superseded_run_records_cancellation_age_and_started_heavy_work():
    superseded = _run(1, "10:00:00", "10:05:00", conclusion="cancelled")
    successor = _run(2, "10:04:00", "10:30:00")
    report = measure(
        [successor, superseded],
        {
            1: _ungated_jobs(),
            2: [_job("preflight-policy", "10:04:05", "10:04:10", "10:07:00")],
        },
    )

    row = next(row for row in report["runs"] if row["id"] == 1)
    assert row["outcome"] == "superseded"
    assert row["cancellationAgeSeconds"] == 240
    # A queued cancellation with no executed step is not a heavy start.
    assert row["heavyJobsStarted"] == 2
    assert row["heavyJobsStartedBeforePreflight"] == 2
    assert row["heavyRunnerSeconds"] == 280 + 270
    assert row["preflightGated"] is False
    # Heavy runner time spent before policy completed is what the gate defers;
    # its cost is the policy time after selection that delays heavy starts.
    assert row["heavyRunnerSecondsBeforePreflight"] == 170 + 160
    assert row["admissionDelaySeconds"] == 186
    # Only allocated jobs have a completed queue wait.
    assert row["queueSeconds"] == 1 + 5 + 15 + 25 + 10
    ungated = report["cohorts"]["ungated"]
    assert ungated["superseded"] == 1
    assert ungated["supersededWithHeavyStart"] == 1
    assert ungated["heavyJobsStartedInSuperseded"] == 2
    assert ungated["cancellationAgeSeconds"]["median"] == 240
    assert ungated["runnerHours"]["supersededHeavyBeforePreflight"] == 330 / 3600


def test_cancelled_run_without_a_successor_is_not_counted_as_superseded():
    report = measure(
        [_run(1, "10:00:00", "10:05:00", conclusion="cancelled")],
        {1: _ungated_jobs()},
    )
    assert report["runs"][0]["outcome"] == "cancelled"
    assert report["runs"][0]["cancellationAgeSeconds"] is None
    assert report["cohorts"]["ungated"]["superseded"] == 0


@pytest.mark.parametrize(
    "other",
    [
        {"branch": "pr-b"},
        {"event": "schedule"},
        {"event": "merge_group"},
    ],
)
def test_supersession_follows_isolated_concurrency_groups(other):
    branch = other.pop("branch", "pr-a")
    cancelled = _run(1, "10:00:00", "10:05:00", conclusion="cancelled")
    unrelated = _run(2, "10:01:00", "10:20:00", branch=branch, **other)
    report = measure([cancelled, unrelated], {1: _ungated_jobs(), 2: []})
    assert report["runs"][0]["outcome"] == "cancelled"


def test_admission_cohorts_follow_observed_job_creation_after_preflight():
    gated_jobs = [
        _job("select-test-suites", "11:00:01", "11:00:02", "11:00:30"),
        _job("preflight-policy", "11:00:05", "11:00:10", "11:03:10"),
        _job("integration-ci (fresh-journey)", "11:03:10", "11:03:30", "11:10:00"),
        _job("omnigent-exact-artifact", "11:03:10", "11:03:40", "11:09:00"),
    ]
    report = measure(
        [
            _run(1, "10:00:00", "10:30:00"),
            _run(2, "11:00:00", "11:30:00", branch="pr-b"),
            _run(3, "12:00:00", "12:10:00", branch="pr-c"),
        ],
        {
            1: _ungated_jobs(),
            2: gated_jobs,
            3: [_job("preflight-policy", "12:00:05", "12:00:10", "12:03:00")],
        },
    )
    by_id = {row["id"]: row for row in report["runs"]}
    assert by_id[1]["preflightGated"] is False
    assert by_id[2]["preflightGated"] is True
    assert by_id[2]["heavyJobsStartedBeforePreflight"] == 0
    # No heavy owner means admission was not observable for this run.
    assert by_id[3]["preflightGated"] is None
    assert report["cohorts"]["gated"]["runs"] == 1
    assert report["cohorts"]["gated"]["completedCandidates"] == 1
    assert report["cohorts"]["gated"]["admissionDelaySeconds"]["median"] == 160
    assert report["cohorts"]["unobserved"]["runs"] == 1


def test_selection_finishing_after_preflight_leaves_admission_unobserved():
    # Under runner congestion selection can end after preflight, so ungated
    # heavy owners are also created after it; that run proves nothing.
    congested = [
        _job("select-test-suites", "10:00:01", "10:04:00", "10:05:00"),
        _job("preflight-policy", "10:00:01", "10:00:10", "10:03:10"),
        _job("integration-hermetic (hermetic)", "10:05:00", "10:05:10", "10:20:00"),
    ]
    report = measure([_run(1, "10:00:00", "10:30:00")], {1: congested})
    assert report["runs"][0]["preflightGated"] is None
    assert report["cohorts"]["gated"]["runs"] == 0


def test_overlap_push_interval_and_incomplete_timing_are_reported():
    in_flight = _run(3, "10:10:00", "10:20:00", conclusion=None, branch="pr-b")
    report = measure(
        [
            _run(1, "10:00:00", "10:05:00", conclusion="cancelled"),
            _run(2, "10:04:00", "10:30:00"),
            in_flight,
        ],
        {
            1: _ungated_jobs(),
            2: [_job("preflight-policy", "10:04:05", "10:04:10", "10:07:00")],
            3: [_job("integration-hermetic (hermetic)", "10:10:05", "10:10:10")],
        },
    )
    by_id = {row["id"]: row for row in report["runs"]}
    assert by_id[2]["concurrentRunsAtCreation"] == 1
    assert by_id[3]["concurrentRunsAtCreation"] == 1
    assert by_id[3]["outcome"] == "in_progress"
    assert by_id[3]["timingComplete"] is False
    assert report["pushIntervalSeconds"]["samples"] == 1
    assert report["pushIntervalSeconds"]["median"] == 240


def test_rerun_is_measured_as_its_superseded_first_attempt():
    first_attempt = _run(
        1, "10:00:00", "10:05:00", conclusion="cancelled", latest_run_attempt=2
    )
    report = measure(
        [first_attempt, _run(2, "10:04:00", "10:30:00")],
        {1: _ungated_jobs(), 2: []},
    )
    row = report["runs"][0]
    assert row["outcome"] == "superseded"
    assert row["attempts"] == 2
    assert report["cohorts"]["ungated"]["rerunRuns"] == 1


def _pull(number, created, closed=None):
    return {
        "number": number,
        "created_at": f"2026-11-18T{created}Z",
        "closed_at": f"2026-11-18T{closed}Z" if closed else None,
    }


def test_pull_request_runs_follow_their_pull_request_concurrency_group():
    # Two PRs share one head branch; each cancels only its own runs.
    pulls = {"owner/repo:pr-a": [_pull(7, "09:00:00"), _pull(8, "10:02:00")]}
    report = measure(
        [
            _run(1, "10:00:00", "10:05:00", conclusion="cancelled"),
            _run(2, "10:03:00", "10:30:00"),
        ],
        {1: _ungated_jobs(), 2: []},
        pulls,
    )
    by_id = {row["id"]: row for row in report["runs"]}
    assert by_id[1]["group"] == "pull_request/#7"
    # Run 2 could belong to either open PR, so it supersedes nothing and adds
    # no push interval.
    assert by_id[1]["outcome"] == "cancelled"
    assert by_id[2]["group"] == "pull_request/ambiguous-run-2"
    assert report["pushIntervalSeconds"]["samples"] == 0
    assert report["window"]["ambiguousPullRequestRuns"] == 1


def test_sequential_pull_requests_on_one_branch_are_separate_groups():
    pulls = {
        "owner/repo:pr-a": [_pull(7, "09:00:00", "10:02:00"), _pull(8, "10:03:00")]
    }
    report = measure(
        [
            _run(1, "10:00:00", "10:05:00", conclusion="cancelled"),
            _run(2, "10:04:00", "10:30:00"),
        ],
        {1: _ungated_jobs(), 2: []},
        pulls,
    )
    assert [row["group"] for row in report["runs"]] == [
        "pull_request/#7",
        "pull_request/#8",
    ]
    assert report["runs"][0]["outcome"] == "cancelled"
    assert report["window"]["ambiguousPullRequestRuns"] == 0


def test_fetch_saves_first_attempt_runs_jobs_and_head_pull_requests(tmp_path):
    calls = []
    # The runs list describes a rerun's latest attempt; attempt 1 was cancelled.
    runs = [_run(9, "10:00:00", "10:50:00", run_attempt=2)]
    first_attempt = _run(9, "10:00:00", "10:05:00", conclusion="cancelled")
    pull = {**_pull(7, "09:00:00"), "title": "unused"}

    def api(path):
        calls.append(path)
        if path.endswith("/runs/9/attempts/1"):
            return first_attempt
        page = int(path.rsplit("&page=", 1)[1])
        if "/pulls?" in path:
            return [pull] if page == 1 else []
        if "/runs?" in path:
            return {"workflow_runs": runs if page == 1 else []}
        return {
            "jobs": [
                _job(f"job-{n}", "10:00:01") for n in range(100 if page == 1 else 3)
            ]
        }

    fetch(api, "owner/repo", "ci.yml", runs=1, directory=tmp_path)

    assert (
        "repos/owner/repo/actions/runs/9/attempts/1/jobs?per_page=100&page=2" in calls
    )
    assert (
        "repos/owner/repo/pulls?state=all&head=owner:pr-a&per_page=100&page=1" in calls
    )
    pages = json.loads((tmp_path / "jobs" / "9.json").read_text())
    assert sum(len(page["jobs"]) for page in pages) == 103
    assert json.loads((tmp_path / "runs.json").read_text()) == [
        {**first_attempt, "latest_run_attempt": 2}
    ]
    assert json.loads((tmp_path / "pulls.json").read_text()) == {
        "owner/repo:pr-a": [_pull(7, "09:00:00")]
    }


def test_cli_writes_report_from_saved_metadata(tmp_path, capsys):
    (tmp_path / "jobs").mkdir()
    (tmp_path / "runs.json").write_text(json.dumps([_run(1, "10:00:00", "10:30:00")]))
    (tmp_path / "pulls.json").write_text(json.dumps({}))
    (tmp_path / "jobs" / "1.json").write_text(json.dumps([{"jobs": _ungated_jobs()}]))
    output = tmp_path / "report.json"

    assert main(["summarize", "--dir", str(tmp_path), "--output", str(output)]) == 0

    report = json.loads(output.read_text())
    assert report["schema"] == "moonmind.ci-supersession/v1"
    assert report["cohorts"]["ungated"]["completedCandidates"] == 1
    assert "Superseded" in capsys.readouterr().out


def test_cli_refuses_an_interrupted_fetch(tmp_path, capsys):
    # Missing job metadata must not read as zero-cost completed runs.
    (tmp_path / "jobs").mkdir()
    runs = [_run(1, "10:00:00", "10:30:00"), _run(2, "11:00:00", "11:30:00")]
    (tmp_path / "runs.json").write_text(json.dumps(runs))
    (tmp_path / "pulls.json").write_text(json.dumps({}))
    (tmp_path / "jobs" / "1.json").write_text(json.dumps([{"jobs": _ungated_jobs()}]))
    output = tmp_path / "report.json"

    assert main(["summarize", "--dir", str(tmp_path), "--output", str(output)]) == 1

    assert not output.exists()
    assert "1 of 2 runs" in capsys.readouterr().err
