from moonmind.workflows.executions.objective_metrics import objective_sample_metrics


def row(workflow, run, at, outcome, **memo):
    return {
        "workflow_id": workflow,
        "run_id": run,
        "created_at": at,
        "memo": {"objectiveParentId": None, "objectiveOutcome": outcome, **memo},
    }


def test_recovered_objective_keeps_failed_attempts_and_excludes_idle():
    rows = [
        row("a", "a1", "2026-01-01T00:00:00Z", "failed"),
        row(
            "b",
            "b1",
            "2026-01-02T00:00:00Z",
            "succeeded",
            objectiveRecoverySource={
                "workflowId": "a",
                "runId": "a1",
                "verified": True,
            },
            objectiveProgress={
                "remediationAttempts": 2,
                "evidenceRetries": 1,
                "savedWorkAvailable": True,
            },
        ),
        row("idle", "i1", "2026-01-03T00:00:00Z", "idle"),
        row("child", "c1", "2026-01-02T00:00:00Z", "failed", objectiveParentId="b"),
    ]
    result = objective_sample_metrics(rows)
    assert result["outcomes"] == {"succeeded": 1, "idle": 1}
    assert result["attemptOutcomes"] == {"failed": 1, "succeeded": 1, "idle": 1}
    assert result["eligibleObjectives"] == 1
    assert result["objectiveSuccessRate"] == 1
    assert result["recoveredSuccessfulObjectives"] == 1
    assert result["attemptProgress"]["remediationAttempts"] == 2
    assert result["attemptsWithSavedWork"] == 1
    assert result["childWorkflows"] == 1


def test_datetime_ordering_and_duplicate_observations_do_not_hide_failures():
    first = row("a", "a1", "2026-01-01T01:00:00+02:00", "succeeded")
    second = row("a", "a2", "2026-01-01T00:00:00Z", "failed")
    result = objective_sample_metrics([first, first, second])
    assert result["outcomes"] == {"failed": 1}
    assert result["observedWorkflowRuns"] == 2
    assert result["attemptOutcomes"] == {"succeeded": 1, "failed": 1}


def test_missing_or_unverified_lineage_is_explicit_not_an_objective_success():
    result = objective_sample_metrics(
        [
            row(
                "b",
                "b1",
                "2026-01-01T00:00:00Z",
                "succeeded",
                objectiveRecoverySource={
                    "workflowId": "missing",
                    "runId": "old",
                    "verified": True,
                },
            ),
            row(
                "c",
                "c1",
                "2026-01-01T00:00:00Z",
                "failed",
                objectiveRecoverySource={
                    "workflowId": "b",
                    "runId": "b1",
                    "verified": False,
                },
            ),
            row("d", "d1", "invalid-date", "succeeded"),
        ]
    )
    assert result["eligibleObjectives"] == 0
    assert result["objectiveSuccessRate"] is None
    assert result["unknownOutcomesOrLineage"] == 2
    assert result["unknownChronology"] == 1
    assert result["attemptOutcomes"] == {"succeeded": 1, "failed": 1}


def test_scheduled_recovery_preserves_all_run_cost_and_unknown_evidence():
    first = row(
        "a",
        "a1",
        "2026-01-01T00:00:00Z",
        "failed",
        objectiveScheduled=True,
        objectiveProgress={"evidenceRetries": 2, "savedWorkAvailable": True},
    )
    second = row(
        "a",
        "a2",
        "2026-01-02T00:00:00Z",
        "succeeded",
        objectiveScheduled=True,
        objectiveProgress={"evidenceRetries": 1},
    )
    result = objective_sample_metrics(
        [
            first,
            first,
            second,
            row("idle", "i", "2026-01-03T00:00:00Z", "idle", objectiveScheduled=True),
            row(
                "manual",
                "m",
                "2026-01-03T00:00:00Z",
                "failed",
                objectiveScheduled=False,
            ),
            row("old", "o", "2026-01-03T00:00:00Z", "failed"),
        ]
    )
    assert result["scheduledObjectives"] == {
        "outcomes": {"succeeded": 1, "idle": 1},
        "eligible": 1,
        "successRate": 1.0,
        "unknownScheduling": 1,
    }
    assert result["attemptProgress"]["evidenceRetries"] == 3
    assert result["attemptsWithSavedWork"] == 1
    assert result["unknownSavedWorkAvailability"] == 4
