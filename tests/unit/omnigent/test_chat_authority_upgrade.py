"""Exercise the data repair against real tables without touching a deployment."""

import importlib
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from api_service.db.models import (
    Base,
    OmnigentCommand,
    OmnigentExecutionPlanRecord,
    OmnigentReconciliationDecision,
    OmnigentRuntimeBindingRecord,
    OmnigentSession,
    OmnigentTurnAttempt,
)

_PLAN = "omnigent-execution-plan:sha256:" + "a" * 64
_BINDING = "omnigent-runtime-binding:sha256:" + "b" * 64
_ATTESTATION = "artifact:art_host_harness"
_MIGRATION = "api_service.migrations.versions.406_omnigent_chat_ownership"


@pytest.fixture
def connection(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path}/upgrade.db")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        yield connection
    engine.dispose()


def _seed(connection, *, fault=None, reason="compatibility_unknown_after_admission"):
    connection.execute(
        sa.insert(OmnigentExecutionPlanRecord).values(
            plan_ref=_PLAN,
            schema_version="moonmind.omnigent-execution-plan.v1",
            payload_json={},
            harness_id="codex-native",
            harness_implementation_ref="harness:codex",
            host_class_ref="host-class",
            launch_policy_ref="launch-policy",
            execution_realizer_ref=(
                "codex-profile-bound@1"
                if fault == "owner"
                else "generic-omnigent-host@1"
            ),
        )
    )
    connection.execute(
        sa.insert(OmnigentRuntimeBindingRecord).values(
            runtime_binding_ref=_BINDING,
            binding_id=_BINDING,
            latest_snapshot_ref="snapshot:runtime",
            execution_plan_ref=_PLAN,
            state="session_active" if fault == "active_runtime" else "cleaned",
            session_id="other-provider" if fault == "provider" else "provider-session",
            attestation_refs_json=(
                {}
                if fault == "missing_attestation"
                else {
                    "hostHarnessAttestationRef": (
                        7 if fault == "invalid_attestation" else _ATTESTATION
                    )
                }
            ),
            terminal_result_json=(
                None if fault == "missing_result" else {"executionState": "completed"}
            ),
        )
    )
    connection.execute(
        sa.insert(OmnigentSession).values(
            session_id="session",
            moonmind_workflow_id="workflow",
            provider="omnigent",
            execution_plan_ref=None if fault == "plan" else _PLAN,
            runtime_binding_ref=_BINDING,
            provider_session_ref="provider-session",
            active_turn_attempt_id="turn",
            revision=5 if fault == "revision" else 4,
            fencing_generation=1 if fault == "fence" else 0,
            compatibility_ref="retained-compatibility" if fault == "existing" else None,
            historical_read_state="quarantined",
            reconciled_state="quarantined",
            last_decision_ref="quarantine",
        )
    )
    connection.execute(
        sa.insert(OmnigentTurnAttempt).values(
            turn_attempt_id="turn",
            session_id="session",
            idempotency_key="turn-key",
            state="accepted",
        )
    )
    connection.execute(
        sa.insert(OmnigentCommand).values(
            command_id="execute",
            session_id="session",
            turn_attempt_id="turn",
            command_type=(
                "workflow_chat" if fault == "chat" else "execute_admitted_plan"
            ),
            idempotency_key="command-key",
            payload_digest=_PLAN,
            status="delivery_unknown" if fault == "uncertain" else "applied",
        )
    )
    connection.execute(
        sa.insert(OmnigentReconciliationDecision).values(
            decision_id="quarantine",
            session_id="session",
            decision_code=(
                "operator_quarantine"
                if fault == "decision"
                else "quarantine_ambiguous_state"
            ),
            reason_code=(
                "repeated_reconciliation_no_progress" if fault == "reason" else reason
            ),
            expected_revision=3,
        )
    )


def _upgrade(connection, monkeypatch):
    migration = importlib.import_module(_MIGRATION)
    monkeypatch.setattr(migration, "op", SimpleNamespace(get_bind=lambda: connection))
    migration.upgrade()
    return migration


def _row(connection, model):
    return connection.execute(sa.select(model.__table__)).mappings().one()


@pytest.mark.parametrize(
    "reason",
    ["compatibility_unknown_after_admission", "moonmind_active_no_recent_evidence"],
)
@pytest.mark.parametrize("fault", [None, "active_runtime", "existing"])
def test_upgrade_recovers_only_verified_work_and_is_idempotent(
    connection, monkeypatch, reason, fault
):
    _seed(connection, fault=fault, reason=reason)
    original_decision = dict(_row(connection, OmnigentReconciliationDecision))
    original_binding = dict(_row(connection, OmnigentRuntimeBindingRecord))
    original_turn = dict(_row(connection, OmnigentTurnAttempt))
    original_command = dict(_row(connection, OmnigentCommand))
    migration = _upgrade(connection, monkeypatch)
    session = _row(connection, OmnigentSession)
    turn = _row(connection, OmnigentTurnAttempt)
    assert session["historical_read_state"] == "live"
    assert session["reconciled_state"] is None
    assert session["compatibility_ref"] == (
        "retained-compatibility" if fault == "existing" else None
    )
    assert session["revision"] == 5
    assert session["active_turn_attempt_id"] == "turn"
    assert session["terminal_state"] is None
    assert dict(turn) == original_turn
    assert dict(_row(connection, OmnigentCommand)) == original_command
    decisions = (
        connection.execute(sa.select(OmnigentReconciliationDecision.__table__))
        .mappings()
        .all()
    )
    assert len(decisions) == 2
    assert (
        dict(next(row for row in decisions if row["decision_id"] == "quarantine"))
        == original_decision
    )
    recovered = next(
        row for row in decisions if row["decision_id"] == session["last_decision_ref"]
    )
    assert recovered["decision_code"] == "agent_run_ownership_recovered"
    assert recovered["expected_revision"] == 4
    assert dict(_row(connection, OmnigentRuntimeBindingRecord)) == original_binding
    migration.upgrade()
    migration.downgrade()
    assert dict(_row(connection, OmnigentSession)) == dict(session)
    assert dict(_row(connection, OmnigentTurnAttempt)) == dict(turn)
    assert (
        connection.scalar(
            sa.select(sa.func.count()).select_from(OmnigentReconciliationDecision)
        )
        == 2
    )


@pytest.mark.parametrize(
    "fault",
    [
        "owner",
        "decision",
        "reason",
        "revision",
        "fence",
        "provider",
        "plan",
        "missing_attestation",
        "invalid_attestation",
    ],
)
def test_upgrade_preserves_unrelated_or_changed_quarantines(
    connection, monkeypatch, fault
):
    _seed(connection, fault=fault)
    _upgrade(connection, monkeypatch)
    session = _row(connection, OmnigentSession)
    assert session["historical_read_state"] == "quarantined"
    assert session["last_decision_ref"] == "quarantine"
    assert (
        connection.scalar(
            sa.select(sa.func.count()).select_from(OmnigentReconciliationDecision)
        )
        == 1
    )


@pytest.mark.parametrize(
    "fault", ["chat", "uncertain", "missing_result", "active_runtime"]
)
def test_upgrade_never_turns_acceptance_or_uncertainty_into_completion(
    connection, monkeypatch, fault
):
    _seed(connection, fault=fault)
    _upgrade(connection, monkeypatch)
    session = _row(connection, OmnigentSession)
    turn = _row(connection, OmnigentTurnAttempt)
    assert session["active_turn_attempt_id"] == "turn"
    assert turn["state"] == "accepted"
    assert turn["terminal_state"] is None
