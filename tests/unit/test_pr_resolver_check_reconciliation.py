"""Applicable check evidence must never erase an unrelated or unproven failure."""

from copy import deepcopy

import pytest

from pr_resolver_core import github_checks

HEAD = "a" * 40


def _run(run_id=101, **changes):
    return {
        "id": run_id,
        "head_sha": HEAD,
        "head_branch": "feature",
        "workflow_id": 7,
        "path": ".github/workflows/ci.yml",
        "event": "pull_request",
        "pull_requests": [{"number": 1, "base": {"ref": "main", "sha": "b" * 40}}],
        "run_number": run_id,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
        **changes,
    }


def _check(check_id=1, run_id=101, **changes):
    return {
        "id": check_id,
        "head_sha": HEAD,
        "name": "ci-required",
        "app": {"id": 15368, "slug": "github-actions"},
        "details_url": f"https://github.com/acme/repo/actions/runs/{run_id}/job/{check_id + 1000}",
        "status": "completed",
        "conclusion": "success",
        **changes,
    }


def _reconcile(checks, runs):
    return github_checks.reconcile_check_runs(checks, runs, head_sha=HEAD)


def test_newer_run_replaces_old_cancelled_check_with_traceable_evidence():
    old = _check(conclusion="cancelled")
    new = _check(2, 102)
    checks, evidence = _reconcile([old, new], [_run(), _run(102)])

    assert checks == [new]
    assert evidence["supersededChecks"] == [
        {
            "checkId": 1,
            "name": "ci-required",
            "runId": 101,
            "runAttempt": 1,
            "supersededByCheckId": 2,
            "supersededByRunId": 102,
            "supersededByRunAttempt": 1,
            "reason": "newer_workflow_run",
        }
    ]
    assert evidence["unresolvedChecks"] == []


def test_explicit_job_attempts_replace_old_failure_in_same_workflow_run():
    old = _check(conclusion="failure", run_attempt=1)
    new = _check(2, run_attempt=2)
    checks, evidence = _reconcile([new, old], [_run(run_attempt=2)])

    assert checks == [new]
    assert evidence["supersededChecks"][0]["reason"] == "newer_run_attempt"
    assert evidence["supersededChecks"][0]["supersededByRunAttempt"] == 2


def test_same_run_id_does_not_prove_rerun_attempt_for_distinct_jobs():
    old = _check(conclusion="failure")
    new = _check(2)
    checks, evidence = _reconcile([old, new], [_run(run_attempt=2)])

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert evidence["unresolvedChecks"]


@pytest.mark.parametrize(
    "run_changes",
    [
        {"workflow_id": 8},
        {"workflow_id": None, "path": ".github/workflows/security.yml"},
        {"path": ".github/workflows/security.yml"},
        {"event": "push"},
        {"head_sha": "b" * 40},
        {"run_number": 101},
    ],
)
def test_different_workflow_event_head_or_nonlater_run_keeps_failure(run_changes):
    old = _check(conclusion="failure")
    new = _check(2, 102)
    checks, evidence = _reconcile([old, new], [_run(), _run(102, **run_changes)])

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []


@pytest.mark.parametrize(
    "check_changes",
    [
        {"name": "other-check"},
        {"name": "CI-REQUIRED"},
        {"head_sha": "b" * 40},
        {"app": {"id": 99, "slug": "github-actions"}},
        {"app": {"id": 15368, "slug": "github-advanced-security"}},
    ],
)
def test_different_check_or_app_cannot_erase_failure(check_changes):
    old = _check(conclusion="failure")
    new = _check(2, 102, **check_changes)
    checks, evidence = _reconcile([old, new], [_run(), _run(102)])

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []


def test_reversed_run_order_retains_newer_failure_over_older_success():
    newer_failure = _check(conclusion="failure")
    older_success = _check(2, 102)
    checks, evidence = _reconcile(
        [newer_failure, older_success], [_run(), _run(102, run_number=100)]
    )

    assert checks == [newer_failure]
    assert [item["checkId"] for item in evidence["supersededChecks"]] == [2]


def test_newer_workflow_does_not_hide_failed_job_absent_from_new_run():
    old = _check(name="unit-tests", conclusion="failure")
    new = _check(2, 102)
    checks, evidence = _reconcile([old, new], [_run(), _run(102)])

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []


@pytest.mark.parametrize("missing", ["head_sha", "event", "run_number"])
def test_incomplete_workflow_metadata_keeps_failure_and_marks_uncertainty(missing):
    old = _check(conclusion="cancelled")
    new = _check(2, 102)
    run = _run()
    run.pop(missing)
    checks, evidence = _reconcile([old, new], [run, _run(102)])

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert any(item["checkId"] == 1 for item in evidence["unresolvedChecks"])


@pytest.mark.parametrize(
    "check_changes",
    [
        {"head_sha": None},
        {"app": {"slug": "github-actions"}},
        {"details_url": "https://github.com/acme/repo/checks/1"},
        {"run_attempt": 3},
        {"run_attempt": True},
    ],
)
def test_incomplete_or_invalid_check_mapping_never_removes_failure(check_changes):
    old = _check(conclusion="failure", **check_changes)
    new = _check(2, 102)
    checks, evidence = _reconcile([old, new], [_run(), _run(102)])

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert any(item["checkId"] == 1 for item in evidence["unresolvedChecks"])


def test_workflow_path_can_establish_identity_when_workflow_ids_are_unavailable():
    old = _check(conclusion="cancelled")
    new = _check(2, 102)
    checks, evidence = _reconcile(
        [old, new], [_run(workflow_id=None), _run(102, workflow_id=None)]
    )

    assert checks == [new]
    assert len(evidence["supersededChecks"]) == 1


def test_absent_workflow_identity_preserves_all_records():
    old = _check(conclusion="cancelled")
    new = _check(2, 102)
    checks, evidence = _reconcile(
        [old, new], [_run(workflow_id=None, path=None), _run(102)]
    )

    assert checks == [old, new]
    assert evidence["unresolvedChecks"]


@pytest.mark.parametrize("status", ["queued", "in_progress"])
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "success"])
def test_terminal_workflow_reports_stranded_jobs_without_fabricating_check_results(
    status, conclusion
):
    pending = _check(status=status, conclusion=None)
    checks, evidence = _reconcile([pending], [_run(conclusion=conclusion)])

    assert checks == [pending]
    assert checks[0]["status"] == status
    assert evidence["strandedChecks"] == [
        {
            "checkId": 1,
            "name": "ci-required",
            "runId": 101,
            "runAttempt": 1,
            "workflowStatus": "completed",
            "workflowConclusion": conclusion,
            "reason": "terminal_workflow_with_incomplete_check",
        }
    ]


def test_live_workflow_pending_jobs_are_not_stranded():
    pending = _check(status="queued", conclusion=None)
    checks, evidence = _reconcile(
        [pending], [_run(status="in_progress", conclusion=None)]
    )

    assert checks == [pending]
    assert evidence["strandedChecks"] == []
    assert evidence["unresolvedChecks"] == []


def test_superseded_pending_job_does_not_leave_stranded_or_unresolved_evidence():
    old = _check(status="queued", conclusion=None)
    new = _check(2, 102)
    checks, evidence = _reconcile([old, new], [_run(conclusion="failure"), _run(102)])

    assert checks == [new]
    assert evidence["strandedChecks"] == []
    assert evidence["unresolvedChecks"] == []


def test_missing_workflow_data_blocks_problematic_actions_checks_only():
    success = _check()
    pending = _check(2, name="unit-tests", status="queued", conclusion=None)
    security = _check(
        3,
        name="CodeQL",
        app={"id": 99, "slug": "github-advanced-security"},
        conclusion="failure",
    )
    checks, evidence = _reconcile([success, pending, security], None)

    assert checks == [success, pending, security]
    assert [item["checkId"] for item in evidence["unresolvedChecks"]] == [2]


def test_success_only_and_empty_snapshots_do_not_require_actions_reads():
    success = _check()
    for originals in [[], [success]]:
        checks, evidence = _reconcile(originals, None)
        assert checks == originals
        assert evidence == {
            "supersededChecks": [],
            "strandedChecks": [],
            "unresolvedChecks": [],
        }
    assert not github_checks.head_ci_reported([], [], [], None)


def test_security_and_required_check_failures_stay_until_same_check_replacement():
    required = _check(conclusion="failure")
    security = _check(2, name="Analyze (python)", conclusion="failure")
    success = _check(3, 102)
    checks, evidence = _reconcile([required, security, success], [_run(), _run(102)])

    assert checks == [security, success]
    assert [item["checkId"] for item in evidence["supersededChecks"]] == [1]


def test_reconciliation_is_order_independent_and_does_not_mutate_inputs():
    checks = [_check(3, 103), _check(conclusion="cancelled"), _check(2, 102)]
    runs = [_run(103), _run(102), _run()]
    original_checks, original_runs = deepcopy(checks), deepcopy(runs)
    applicable, evidence = _reconcile(checks, runs)

    assert applicable == [checks[0]]
    assert {item["supersededByCheckId"] for item in evidence["supersededChecks"]} == {3}
    assert checks == original_checks
    assert runs == original_runs


def test_conflicting_records_for_same_workflow_run_fail_closed():
    old = _check(conclusion="failure")
    new = _check(2, 102)
    checks, evidence = _reconcile([old, new], [_run(), _run(workflow_id=42), _run(102)])

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert evidence["unresolvedChecks"]


def test_attempt_bearing_job_url_proves_same_run_rerun_order():
    old = _check(
        conclusion="cancelled",
        details_url="https://github.com/acme/repo/actions/runs/101/attempts/1/job/1001",
    )
    new = _check(
        2,
        details_url="https://github.com/acme/repo/actions/runs/101/attempts/2/job/1002",
    )
    checks, evidence = _reconcile([old, new], [_run(run_attempt=2)])

    assert checks == [new]
    assert evidence["supersededChecks"][0]["reason"] == "newer_run_attempt"


def test_conflicting_job_url_and_enriched_attempt_does_not_erase_failure():
    old = _check(conclusion="failure", run_attempt=1)
    new = _check(
        2,
        run_attempt=2,
        details_url="https://github.com/acme/repo/actions/runs/101/attempts/1/job/1002",
    )
    checks, evidence = _reconcile([old, new], [_run(run_attempt=2)])

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert [item["checkId"] for item in evidence["unresolvedChecks"]] == [2]


def test_larger_job_and_check_ids_cannot_replace_failure_in_same_attempt():
    old = _check(conclusion="failure", run_attempt=1)
    new = _check(999999, run_attempt=1)
    checks, evidence = _reconcile([old, new], [_run()])

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []


def test_new_workflow_run_can_replace_old_run_with_unproven_individual_attempt():
    old = _check(conclusion="cancelled")
    new = _check(2, 102)
    checks, evidence = _reconcile([old, new], [_run(run_attempt=2), _run(102)])

    assert checks == [new]
    assert evidence["supersededChecks"][0]["runAttempt"] is None
    assert evidence["unresolvedChecks"] == []


def test_missing_app_identity_with_actions_job_url_preserves_failed_check():
    old = _check(conclusion="failure", app=None)
    new = _check(2, 102)
    checks, evidence = _reconcile([old, new], [_run(), _run(102)])

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert [item["checkId"] for item in evidence["unresolvedChecks"]] == [1]


@pytest.mark.parametrize(
    "pull_requests",
    [
        [{"number": 2, "base": {"ref": "release", "sha": "b" * 40}}],
        [{"number": 1, "base": {"ref": "release", "sha": "b" * 40}}],
        [{"number": 1, "base": {"ref": "main", "sha": "c" * 40}}],
    ],
)
def test_different_pr_or_base_context_never_supersedes_failure(pull_requests):
    old = _check(conclusion="cancelled")
    new = _check(2, 102)
    checks, evidence = _reconcile(
        [old, new], [_run(), _run(102, pull_requests=pull_requests)]
    )

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []


@pytest.mark.parametrize(
    "pull_requests",
    [
        None,
        [],
        [{}],
        [{"number": 1, "base": {"ref": "main"}}],
        [{"number": 1, "base": {"sha": "b" * 40}}],
        [{"base": {"ref": "main", "sha": "b" * 40}}],
    ],
)
@pytest.mark.parametrize("incomplete_run_id", [101, 102])
def test_unproven_pr_inventory_preserves_failure_and_reports_uncertainty(
    pull_requests, incomplete_run_id
):
    old = _check(conclusion="failure")
    new = _check(2, 102)
    runs = [_run(), _run(102)]
    runs[incomplete_run_id - 101]["pull_requests"] = pull_requests
    checks, evidence = _reconcile([old, new], runs)

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert evidence["unresolvedChecks"]


def test_missing_pr_inventory_is_not_assumed_to_be_empty():
    old = _check(conclusion="cancelled")
    new = _check(2, 102)
    runs = [_run(event="push"), _run(102, event="push")]
    for run in runs:
        run.pop("pull_requests")
    checks, evidence = _reconcile([old, new], runs)

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert evidence["unresolvedChecks"]


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_explicit_empty_pr_inventory_allows_non_pr_event_supersession(event):
    old = _check(conclusion="cancelled")
    new = _check(2, 102)
    checks, evidence = _reconcile(
        [old, new],
        [
            _run(event=event, pull_requests=[], head_branch="main"),
            _run(102, event=event, pull_requests=[], head_branch="main"),
        ],
    )

    assert checks == [new]
    assert len(evidence["supersededChecks"]) == 1
    assert evidence["unresolvedChecks"] == []


def test_pr_inventory_order_does_not_change_proven_context():
    first = {"number": 1, "base": {"ref": "main", "sha": "b" * 40}}
    second = {"number": 2, "base": {"ref": "release", "sha": "c" * 40}}
    old = _check(conclusion="cancelled")
    new = _check(2, 102)
    checks, evidence = _reconcile(
        [old, new],
        [_run(pull_requests=[first, second]), _run(102, pull_requests=[second, first])],
    )

    assert checks == [new]
    assert len(evidence["supersededChecks"]) == 1


def test_same_run_rerun_does_not_need_cross_run_pr_inventory():
    old = _check(conclusion="cancelled", run_attempt=1)
    new = _check(2, run_attempt=2)
    run = _run(run_attempt=2)
    run.pop("pull_requests")
    run.pop("head_branch")
    checks, evidence = _reconcile([old, new], [run])

    assert checks == [new]
    assert evidence["supersededChecks"][0]["reason"] == "newer_run_attempt"
    assert evidence["unresolvedChecks"] == []


def test_observed_different_base_repository_prevents_cross_run_supersession():
    old = _check(conclusion="cancelled")
    new = _check(2, 102)
    runs = [_run(), _run(102)]
    runs[0]["pull_requests"][0]["base"]["repo"] = {"id": 11}
    runs[1]["pull_requests"][0]["base"]["repo"] = {"id": 12}
    checks, evidence = _reconcile([old, new], runs)

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []


def test_empty_inventory_does_not_prove_pull_request_target_context():
    old = _check(conclusion="cancelled")
    new = _check(2, 102)
    checks, evidence = _reconcile(
        [old, new],
        [
            _run(event="pull_request_target", pull_requests=[]),
            _run(102, event="pull_request_target", pull_requests=[]),
        ],
    )

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert evidence["unresolvedChecks"]


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_observed_head_branch_conflict_preserves_failure_for_same_sha(event):
    old = _check(conclusion="cancelled")
    new = _check(2, 102)
    checks, evidence = _reconcile(
        [old, new],
        [
            _run(event=event, pull_requests=[], head_branch="main"),
            _run(102, event=event, pull_requests=[], head_branch="release"),
        ],
    )

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
@pytest.mark.parametrize("incomplete_run_index", [0, 1])
@pytest.mark.parametrize(
    "branch_fields",
    [
        {},
        {"head_branch": None},
        {"head_branch": ""},
        {"head_branch": " \t"},
        {"head_branch": 42},
        {"head_branch": False},
        {"head_branch": []},
        {"head_branch": {}},
    ],
)
def test_non_pr_cross_run_missing_or_invalid_branch_preserves_failure(
    event, incomplete_run_index, branch_fields
):
    old = _check(conclusion="failure")
    new = _check(2, 102)
    runs = [
        _run(event=event, pull_requests=[], head_branch="main"),
        _run(102, event=event, pull_requests=[], head_branch="main"),
    ]
    runs[incomplete_run_index].pop("head_branch")
    runs[incomplete_run_index].update(branch_fields)
    checks, evidence = _reconcile([old, new], runs)

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert {item["reason"] for item in evidence["unresolvedChecks"]} == {
        "workflow_ref_context_unverified"
    }


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_non_pr_cross_run_both_missing_branches_cannot_establish_equivalence(event):
    old = _check(conclusion="cancelled")
    new = _check(2, 102)
    checks, evidence = _reconcile(
        [old, new],
        [
            _run(event=event, pull_requests=[], head_branch=None),
            _run(102, event=event, pull_requests=[], head_branch=None),
        ],
    )

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert {item["reason"] for item in evidence["unresolvedChecks"]} == {
        "workflow_ref_context_unverified"
    }


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_non_pr_same_run_verified_attempts_do_not_require_head_branch(event):
    old = _check(conclusion="cancelled", run_attempt=1)
    new = _check(2, run_attempt=2)
    checks, evidence = _reconcile(
        [old, new],
        [_run(event=event, pull_requests=[], run_attempt=2, head_branch=None)],
    )

    assert checks == [new]
    assert evidence["supersededChecks"][0]["reason"] == "newer_run_attempt"
    assert evidence["unresolvedChecks"] == []


@pytest.mark.parametrize("incomplete_run_index", [0, 1])
@pytest.mark.parametrize("branch", [None, "", " \t", 42, False, [], {}])
def test_pr_cross_run_requires_nonblank_string_head_branch(
    incomplete_run_index, branch
):
    old = _check(conclusion="failure")
    new = _check(2, 102)
    runs = [_run(), _run(102)]
    runs[incomplete_run_index]["head_branch"] = branch
    checks, evidence = _reconcile([old, new], runs)

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert {item["reason"] for item in evidence["unresolvedChecks"]} == {
        "workflow_ref_context_unverified"
    }


@pytest.mark.parametrize(
    "event",
    [
        "pull_request",
        "pull_request_target",
        "push",
        "workflow_dispatch",
        "repository_dispatch",
        "workflow_run",
        "provider_future_event",
    ],
)
def test_cross_run_event_context_truth_table(event):
    old = _check(conclusion="failure")
    new = _check(2, 102)
    inventory = _run()["pull_requests"] if event.startswith("pull_request") else []
    checks, evidence = _reconcile(
        [old, new],
        [
            _run(event=event, pull_requests=inventory),
            _run(102, event=event, pull_requests=inventory),
        ],
    )

    assert checks == [new]
    assert len(evidence["supersededChecks"]) == 1
    assert evidence["unresolvedChecks"] == []


@pytest.mark.parametrize("event", [None, "", " \t", 42, True, [], {"type": "push"}])
def test_missing_or_malformed_workflow_event_never_supersedes(event):
    old = _check(conclusion="failure")
    new = _check(2, 102)
    checks, evidence = _reconcile(
        [old, new], [_run(event=event), _run(102, event=event)]
    )

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert {item["reason"] for item in evidence["unresolvedChecks"]} == {
        "workflow_identity_unavailable"
    }


@pytest.mark.parametrize("name", [None, "", " \t", 42, True, [], {"name": "ci"}])
def test_missing_or_malformed_check_name_never_supersedes(name):
    old = _check(conclusion="failure", name=name)
    new = _check(2, 102, name=name)
    checks, evidence = _reconcile([old, new], [_run(), _run(102)])

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert {item["reason"] for item in evidence["unresolvedChecks"]} == {
        "check_identity_unavailable"
    }


@pytest.mark.parametrize("incomplete_run_index", [0, 1])
@pytest.mark.parametrize(
    "repo", [None, "repo", 42, [], {}, {"id": None}, {"id": True}, {"id": "invalid"}]
)
def test_malformed_observed_base_repository_is_not_absent_optional_metadata(
    incomplete_run_index, repo
):
    old = _check(conclusion="failure")
    new = _check(2, 102)
    runs = [_run(), _run(102)]
    runs[incomplete_run_index]["pull_requests"][0]["base"]["repo"] = repo
    checks, evidence = _reconcile([old, new], runs)

    assert checks == [old, new]
    assert evidence["supersededChecks"] == []
    assert {item["reason"] for item in evidence["unresolvedChecks"]} == {
        "workflow_pr_context_unverified"
    }


@pytest.mark.parametrize("explicit_attempt", [None, 1])
def test_zero_url_attempt_is_never_verified(explicit_attempt):
    old = _check(
        conclusion="failure",
        run_attempt=explicit_attempt,
        details_url="https://github.com/acme/repo/actions/runs/101/attempts/0/job/1001",
    )
    new = _check(
        2,
        details_url="https://github.com/acme/repo/actions/runs/101/attempts/1/job/1002",
    )
    checks, evidence = _reconcile([old, new], [_run()])
    assert checks == [old, new]
    assert not evidence["supersededChecks"]
    assert evidence["unresolvedChecks"][0]["reason"] == "check_attempt_unverified"


def test_zero_job_identity_cannot_bind_a_check_to_a_workflow():
    old = _check(
        conclusion="failure",
        details_url="https://github.com/acme/repo/actions/runs/101/job/0",
    )
    new = _check(2, 102)
    checks, evidence = _reconcile([old, new], [_run(), _run(102)])
    assert checks == [old, new]
    assert not evidence["supersededChecks"]
    assert evidence["unresolvedChecks"][0]["reason"] == "check_identity_unavailable"
