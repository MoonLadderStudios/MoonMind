"""pr-resolver treats CI infrastructure outages as wait-and-rerun, not review.

A GitHub Actions platform failure (artifact storage quota, lost runner, artifact
service 5xx) cannot be fixed by changing the PR. The snapshot classifies it,
finalize reruns the failed jobs on the same head after a bounded backoff, and
only an exhausted rerun budget becomes a precise blocker.
"""

from __future__ import annotations

import json
import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN = REPO_ROOT / ".agents" / "skills" / "pr-resolver" / "bin"

QUOTA_MESSAGE = (
    "Failed to CreateArtifact: Artifact storage quota has been hit. Unable to "
    "upload any new artifacts. Usage is recalculated every 6-12 hours.\nMore info "
    "on storage limits: https://docs.github.com/en/billing"
)
MISSING_ARTIFACT_MESSAGE = (
    "Unable to download artifact(s): Artifact not found for name: "
    "integrated-ci-result-36790876840-1\n        Please ensure that your artifact "
    "is not expired"
)
HEAD_SHA = "2e6249be545d27eaa7982d156494030e710f4f15"


@pytest.fixture
def snapshot_module() -> dict[str, Any]:
    return runpy.run_path(str(BIN / "pr_resolve_snapshot.py"))


@pytest.fixture
def finalize_module() -> dict[str, Any]:
    return runpy.run_path(str(BIN / "pr_resolve_finalize.py"))


@pytest.fixture
def contract_module() -> dict[str, Any]:
    return runpy.run_path(str(BIN / "pr_resolve_contract.py"))


def _check_run(check_id: int, name: str, *, run_id: int = 36790876840) -> dict:
    return {
        "id": check_id,
        "name": name,
        "status": "completed",
        "conclusion": "failure",
        "details_url": (
            "https://github.com/MoonLadderStudios/Tactics/actions/runs/"
            f"{run_id}/job/{check_id}"
        ),
    }


def _failure(message: str) -> dict:
    return {"annotation_level": "failure", "message": message}


def _warning(message: str) -> dict:
    return {"annotation_level": "warning", "message": message}


def _summarize(
    snapshot_module: dict[str, Any],
    checks: list[dict],
    annotations: dict[int, list[dict]],
    *,
    run_attempt: int = 1,
    updated_at: str = "2026-09-30T23:27:40Z",
) -> dict:
    summarize = snapshot_module["summarize_ci_infrastructure"]
    return summarize(
        checks,
        fetch_annotations=lambda check_id: annotations.get(check_id, []),
        fetch_run=lambda run_id: {
            "id": run_id,
            "run_attempt": run_attempt,
            "status": "completed",
            "conclusion": "failure",
            "updated_at": updated_at,
        },
    )


def test_quota_failure_and_its_dependent_gate_are_infrastructure_only(
    snapshot_module: dict[str, Any],
) -> None:
    # Observed Tactics run 36790876840: every upload step hit the quota and the
    # CI Gate then failed only because the manifest artifact was never uploaded.
    summary = _summarize(
        snapshot_module,
        [
            _check_run(110143591736, "Build Unreal Project Docker Image and Run Tests"),
            _check_run(110144108975, "CI Gate"),
        ],
        {
            110143591736: [
                _failure(QUOTA_MESSAGE),
                _warning("Retention days cannot be greater than the maximum"),
                _failure(QUOTA_MESSAGE),
            ],
            110144108975: [
                _failure("Process completed with exit code 1."),
                _failure(MISSING_ARTIFACT_MESSAGE),
            ],
        },
    )

    assert summary["infrastructureOnly"] is True
    kinds = {item["name"]: item["kind"] for item in summary["infrastructureFailures"]}
    assert kinds == {
        "Build Unreal Project Docker Image and Run Tests": "artifact_storage_quota",
        "CI Gate": "missing_upstream_artifact",
    }
    assert summary["infrastructureRuns"] == [
        {
            "runId": 36790876840,
            "runAttempt": 1,
            "status": "completed",
            "updatedAt": "2026-09-30T23:27:40Z",
            "kinds": ["artifact_storage_quota", "missing_upstream_artifact"],
        }
    ]


def test_real_failure_beside_quota_failure_stays_a_ci_failure(
    snapshot_module: dict[str, Any],
) -> None:
    summary = _summarize(
        snapshot_module,
        [_check_run(1, "Build"), _check_run(2, "Unit tests")],
        {
            1: [_failure(QUOTA_MESSAGE)],
            2: [_failure("Process completed with exit code 1.")],
        },
    )

    assert summary["infrastructureOnly"] is False


def test_generic_exit_beside_quota_in_the_same_job_stays_a_ci_failure(
    snapshot_module: dict[str, Any],
) -> None:
    # A test step's own exit status and an ``if: always()`` upload that hit the
    # quota leave both annotations; the test failure must not be masked.
    summary = _summarize(
        snapshot_module,
        [_check_run(1, "Build and test")],
        {1: [_failure("Process completed with exit code 1."), _failure(QUOTA_MESSAGE)]},
    )

    assert summary["infrastructureOnly"] is False


def test_annotations_are_read_from_every_page(
    snapshot_module: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    commands: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        commands.append(list(cmd))
        pages = json.dumps([_failure(QUOTA_MESSAGE)]) + json.dumps(
            [_failure("error TS2345: Argument of type 'string' is not assignable")]
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=pages, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    annotations = snapshot_module["_fetch_check_run_annotations"](
        pr_repo="MoonLadderStudios/Tactics", check_id=1
    )

    assert "--paginate" in commands[0]
    assert [item["message"][:11] for item in annotations] == [
        QUOTA_MESSAGE[:11],
        "error TS234",
    ]


def test_missing_artifact_without_infrastructure_cause_is_a_real_failure(
    snapshot_module: dict[str, Any],
) -> None:
    # A wrong artifact name is a workflow bug, not an outage.
    summary = _summarize(
        snapshot_module,
        [_check_run(2, "CI Gate")],
        {2: [_failure(MISSING_ARTIFACT_MESSAGE)]},
    )

    assert summary["infrastructureOnly"] is False


def test_failure_without_annotations_is_not_assumed_to_be_infrastructure(
    snapshot_module: dict[str, Any],
) -> None:
    summary = _summarize(snapshot_module, [_check_run(1, "Build")], {1: []})

    assert summary["infrastructureOnly"] is False


def test_application_server_error_in_test_output_is_not_masked(
    snapshot_module: dict[str, Any],
) -> None:
    summary = _summarize(
        snapshot_module,
        [_check_run(1, "API tests")],
        {1: [_failure("AssertionError: expected 200, got 500 Internal Server Error")]},
    )

    assert summary["infrastructureOnly"] is False


def test_lost_runner_is_infrastructure(snapshot_module: dict[str, Any]) -> None:
    summary = _summarize(
        snapshot_module,
        [_check_run(1, "Build")],
        {
            1: [
                _failure(
                    "The self-hosted runner: tactics-ci-1 lost communication with "
                    "the server. Verify the machine is running and has a healthy "
                    "network connection."
                )
            ]
        },
    )

    assert summary["infrastructureOnly"] is True
    assert summary["infrastructureFailures"][0]["kind"] == "runner_lost"


def test_check_outside_github_actions_is_not_infrastructure(
    snapshot_module: dict[str, Any],
) -> None:
    check = _check_run(1, "external-ci")
    check["details_url"] = "https://ci.example.invalid/build/1"

    summary = _summarize(snapshot_module, [check], {1: [_failure(QUOTA_MESSAGE)]})

    assert summary["infrastructureOnly"] is False


def _infra_snapshot(*, run_attempt: int = 1, updated_at: str) -> dict:
    return {
        "repository": "MoonLadderStudios/Tactics",
        "pr": {
            "number": 2765,
            "url": "https://github.com/MoonLadderStudios/Tactics/pull/2765",
            "state": "OPEN",
            "headRefOid": HEAD_SHA,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "UNSTABLE",
        },
        "ci": {
            "isRunning": False,
            "hasFailures": True,
            "hasAuthoritativeFailures": True,
            "signalQuality": "ok",
            "infrastructureOnly": True,
            "infrastructureFailures": [
                {
                    "name": "Build",
                    "kind": "artifact_storage_quota",
                    "message": QUOTA_MESSAGE[:200],
                    "runId": 36790876840,
                }
            ],
            "infrastructureRuns": [
                {
                    "runId": 36790876840,
                    "runAttempt": run_attempt,
                    "status": "completed",
                    "updatedAt": updated_at,
                    "kinds": ["artifact_storage_quota"],
                }
            ],
        },
        "commentsFetch": {"succeeded": True, "source": "fixture"},
        "commentsSummary": {
            "hasActionableComments": False,
            "includeBotReviewComments": True,
        },
    }


def test_finalize_classifies_infrastructure_only_ci_as_transient(
    finalize_module: dict[str, Any],
) -> None:
    decision = finalize_module["evaluate_finalize_action"](
        _infra_snapshot(updated_at="2026-09-30T23:27:40Z")
    )

    assert decision == {"action": "rerun_infrastructure_ci", "reason": "ci_infra_transient"}


def test_finalize_keeps_comment_priority_over_infrastructure_ci(
    finalize_module: dict[str, Any],
) -> None:
    snapshot = _infra_snapshot(updated_at="2026-09-30T23:27:40Z")
    snapshot["commentsSummary"]["hasActionableComments"] = True

    decision = finalize_module["evaluate_finalize_action"](snapshot)

    assert decision == {"action": "blocked", "reason": "actionable_comments"}


def test_finalize_reports_precise_blocker_after_rerun_budget(
    finalize_module: dict[str, Any],
) -> None:
    decision = finalize_module["evaluate_finalize_action"](
        _infra_snapshot(run_attempt=3, updated_at="2026-09-30T23:27:40Z")
    )

    assert decision == {"action": "blocked", "reason": "ci_infra_rerun_exhausted"}


def test_finalize_waits_for_the_final_attempt_to_finish(
    finalize_module: dict[str, Any],
) -> None:
    # The commit's check runs still show the prior attempt's failures while the
    # last bounded rerun is queued; that is not exhaustion yet.
    snapshot = _infra_snapshot(run_attempt=3, updated_at="2026-09-30T23:27:40Z")
    snapshot["ci"]["infrastructureRuns"][0]["status"] = "in_progress"

    decision = finalize_module["evaluate_finalize_action"](snapshot)
    plan = finalize_module["plan_infrastructure_reruns"](
        snapshot, now=datetime(2026, 9, 30, 23, 37, 40, tzinfo=UTC)
    )

    assert decision == {"action": "rerun_infrastructure_ci", "reason": "ci_infra_transient"}
    assert plan == {"dueRunIds": [], "retryAfterSeconds": 60}


def test_rerun_plan_waits_out_quota_backoff_before_rerunning(
    finalize_module: dict[str, Any],
) -> None:
    plan = finalize_module["plan_infrastructure_reruns"](
        _infra_snapshot(updated_at="2026-09-30T23:27:40Z"),
        now=datetime(2026, 9, 30, 23, 37, 40, tzinfo=UTC),
    )

    assert plan["dueRunIds"] == []
    assert plan["retryAfterSeconds"] == 20 * 60


def test_rerun_plan_reruns_lost_runner_immediately(
    finalize_module: dict[str, Any],
) -> None:
    snapshot = _infra_snapshot(updated_at="2026-09-30T23:27:40Z")
    snapshot["ci"]["infrastructureRuns"][0]["kinds"] = ["runner_lost"]

    plan = finalize_module["plan_infrastructure_reruns"](
        snapshot, now=datetime(2026, 9, 30, 23, 27, 50, tzinfo=UTC)
    )

    assert plan["dueRunIds"] == [36790876840]


def _run_finalize(
    finalize_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    snapshot: dict,
    reruns: list[tuple[str, int]],
    rerun_error: str = "",
) -> tuple[int, dict]:
    main = finalize_module["main"]
    globals_dict = main.__globals__

    def _write_snapshot(
        _script: Path, _pr: str | None, snapshot_path: Path, **_kwargs: object
    ) -> None:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    monkeypatch.setitem(globals_dict, "_run_snapshot", _write_snapshot)
    monkeypatch.setitem(
        globals_dict,
        "_rerun_failed_jobs",
        lambda repo, run_id: reruns.append((repo, run_id))
        or (not rerun_error, rerun_error),
    )
    monkeypatch.setitem(
        globals_dict, "_merge_pr", lambda *_args: pytest.fail("must not merge")
    )
    monkeypatch.setenv("MOONMIND_STEP_EXECUTION_ID", "mm:wf:agent:node-1:execution1")
    result_path = tmp_path / "result.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_finalize.py",
            "--strict-exit-codes",
            "--pr",
            "2765",
            "--snapshot-path",
            str(tmp_path / "snapshot.json"),
            "--result-path",
            str(result_path),
        ],
    )
    with pytest.raises(SystemExit) as raised:
        main()
    return int(raised.value.code), json.loads(result_path.read_text(encoding="utf-8"))


def test_finalize_reruns_due_infrastructure_failure_and_reenters_the_gate(
    finalize_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    long_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    reruns: list[tuple[str, int]] = []

    _code, payload = _run_finalize(
        finalize_module,
        monkeypatch,
        tmp_path,
        _infra_snapshot(updated_at=long_ago),
        reruns,
    )

    assert reruns == [("MoonLadderStudios/Tactics", 36790876840)]
    assert payload["status"] == "blocked"
    assert payload["final_reason"] == "ci_infra_transient"
    assert payload["next_step"] == "retry_finalize_after_backoff"
    assert payload["mergeAutomationDisposition"] == "reenter_gate"
    assert "phase" not in payload
    assert payload["gatedContinuation"]["reason"] == "ci_infra_transient"
    assert payload["gatedContinuation"]["retryAfterSeconds"] == 60
    assert "Artifact storage quota has been hit" in payload["decision"]


def test_finalize_waits_without_rerunning_inside_quota_backoff(
    finalize_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    reruns: list[tuple[str, int]] = []

    _code, payload = _run_finalize(
        finalize_module,
        monkeypatch,
        tmp_path,
        _infra_snapshot(updated_at=recent),
        reruns,
    )

    assert reruns == []
    assert payload["mergeAutomationDisposition"] == "reenter_gate"
    retry_after = payload["gatedContinuation"]["retryAfterSeconds"]
    assert 24 * 60 <= retry_after <= 25 * 60


def test_finalize_exhausted_infrastructure_budget_names_the_outage(
    finalize_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    reruns: list[tuple[str, int]] = []

    _code, payload = _run_finalize(
        finalize_module,
        monkeypatch,
        tmp_path,
        _infra_snapshot(run_attempt=3, updated_at="2026-09-30T23:27:40Z"),
        reruns,
    )

    assert reruns == []
    assert payload["final_reason"] == "ci_infra_rerun_exhausted"
    assert payload["mergeAutomationDisposition"] == "manual_review"
    assert "phase" not in payload
    assert "Artifact storage quota has been hit" in payload["decision"]
    assert "3 attempts" in payload["decision"]


def test_finalize_reports_a_rerun_github_refuses(
    finalize_module: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    long_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    reruns: list[tuple[str, int]] = []

    _code, payload = _run_finalize(
        finalize_module,
        monkeypatch,
        tmp_path,
        _infra_snapshot(updated_at=long_ago),
        reruns,
        rerun_error="HTTP 403: Resource not accessible by integration",
    )

    assert reruns == [("MoonLadderStudios/Tactics", 36790876840)]
    assert payload["final_reason"] == "ci_infra_rerun_failed"
    assert payload["mergeAutomationDisposition"] == "manual_review"
    assert "Resource not accessible by integration" in payload["decision"]


def test_rerun_already_started_elsewhere_is_not_a_failure(
    finalize_module: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="HTTP 403: This workflow is already running"
        ),
    )

    assert finalize_module["_rerun_failed_jobs"]("owner/repo", 1) == (False, "")


def test_contract_routes_infrastructure_reasons(
    contract_module: dict[str, Any],
) -> None:
    classify = contract_module["classify_retry_action"]
    next_step = contract_module["remediation_next_step"]

    assert classify("ci_infra_transient", merge_not_ready_grace_remaining=0) == (
        "finalize_only_retry"
    )
    assert next_step("ci_infra_transient") == "retry_finalize_after_backoff"
    assert classify("ci_infra_rerun_exhausted", merge_not_ready_grace_remaining=0) == (
        "stop"
    )
    assert "ci_infra_rerun_exhausted" in contract_module["NON_RETRYABLE_REASONS"]
    assert "ci_infra_rerun_failed" in contract_module["NON_RETRYABLE_REASONS"]


def test_orchestrate_waits_the_finalize_supplied_infrastructure_delay() -> None:
    run_orchestration = runpy.run_path(str(BIN / "pr_resolve_orchestrate.py"))[
        "run_orchestration"
    ]
    finalize_results = iter(
        [
            {
                "status": "blocked",
                "merge_outcome": "blocked",
                "reason": "ci_infra_transient",
                "gatedContinuation": {"retryAfterSeconds": 1200},
            },
            {"status": "merged", "merge_outcome": "merged", "reason": "ci_complete"},
        ]
    )
    sleeps: list[int] = []

    result, exit_code = run_orchestration(
        finalize_runner=lambda _attempt: next(finalize_results),
        full_runner=lambda *_args: pytest.fail("infrastructure is never remediated"),
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: 0.0,
        finalize_max_retries=5,
        fix_max_iterations=3,
        base_sleep_seconds=30,
        max_sleep_seconds=120,
        max_elapsed_seconds=7200,
        merge_not_ready_grace_retries=1,
    )

    assert exit_code == 0
    assert result["status"] == "merged"
    assert sleeps == [1200]
