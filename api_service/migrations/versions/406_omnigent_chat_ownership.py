"""Recover attested AgentRun sessions quarantined by the Session supervisor sweep."""

from hashlib import sha256

import sqlalchemy as sa
from alembic import op

revision = "406_omnigent_chat_ownership"
down_revision = "405_recurring_owner_terminal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sessions = sa.table(
        "omnigent_sessions",
        sa.column("session_id", sa.String),
        sa.column("execution_plan_ref", sa.String),
        sa.column("runtime_binding_ref", sa.String),
        sa.column("provider_session_ref", sa.String),
        sa.column("revision", sa.Integer),
        sa.column("fencing_generation", sa.Integer),
        sa.column("historical_read_state", sa.String),
        sa.column("reconciled_state", sa.String),
        sa.column("last_decision_ref", sa.String),
        sa.column("next_reconciliation_deadline", sa.DateTime(timezone=True)),
    )
    plans = sa.table(
        "omnigent_execution_plans",
        sa.column("plan_ref", sa.String),
        sa.column("execution_realizer_ref", sa.String),
    )
    bindings = sa.table(
        "omnigent_runtime_bindings",
        sa.column("binding_id", sa.String),
        sa.column("execution_plan_ref", sa.String),
        sa.column("omnigent_session_id", sa.String),
        sa.column("attestation_refs_json", sa.JSON),
    )
    decisions = sa.table(
        "omnigent_reconciliation_decisions",
        sa.column("decision_id", sa.String),
        sa.column("session_id", sa.String),
        sa.column("expected_revision", sa.Integer),
        sa.column("fencing_generation", sa.Integer),
        sa.column("decision_code", sa.String),
        sa.column("reason_code", sa.String),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(sessions, bindings.c.attestation_refs_json)
        .join(plans, plans.c.plan_ref == sessions.c.execution_plan_ref)
        .join(
            bindings,
            sa.and_(
                bindings.c.binding_id == sessions.c.runtime_binding_ref,
                bindings.c.execution_plan_ref == sessions.c.execution_plan_ref,
                bindings.c.omnigent_session_id == sessions.c.provider_session_ref,
            ),
        )
        .join(
            decisions,
            sa.and_(
                decisions.c.decision_id == sessions.c.last_decision_ref,
                decisions.c.session_id == sessions.c.session_id,
            ),
        )
        .where(
            plans.c.execution_realizer_ref == "generic-omnigent-host@1",
            sessions.c.historical_read_state == "quarantined",
            sessions.c.reconciled_state == "quarantined",
            decisions.c.decision_code == "quarantine_ambiguous_state",
            decisions.c.reason_code.in_(
                (
                    "compatibility_unknown_after_admission",
                    "moonmind_active_no_recent_evidence",
                )
            ),
            decisions.c.expected_revision == sessions.c.revision - 1,
            decisions.c.fencing_generation == sessions.c.fencing_generation,
        )
        .with_for_update(of=sessions)
    ).mappings()
    for row in rows:
        refs = row["attestation_refs_json"]
        attestation = (
            refs.get("hostHarnessAttestationRef") if isinstance(refs, dict) else None
        )
        if not isinstance(attestation, str) or not attestation.strip():
            continue
        recovery_id = (
            "odc_"
            + sha256(
                f"agent-run-ownership-recovery:{row['session_id']}:{row['revision']}".encode()
            ).hexdigest()[:40]
        )
        updated = connection.execute(
            sessions.update()
            .where(
                sessions.c.session_id == row["session_id"],
                sessions.c.revision == row["revision"],
                sessions.c.fencing_generation == row["fencing_generation"],
                sessions.c.historical_read_state == "quarantined",
                sessions.c.last_decision_ref == row["last_decision_ref"],
            )
            .values(
                historical_read_state="live",
                reconciled_state=None,
                last_decision_ref=recovery_id,
                next_reconciliation_deadline=None,
                revision=row["revision"] + 1,
            )
        )
        if updated.rowcount:
            connection.execute(
                decisions.insert().values(
                    decision_id=recovery_id,
                    session_id=row["session_id"],
                    expected_revision=row["revision"],
                    fencing_generation=row["fencing_generation"],
                    decision_code="agent_run_ownership_recovered",
                    reason_code="session_supervisor_not_owner",
                )
            )


def downgrade() -> None:
    # Keep recovered work and the original quarantine diagnostics on rollback.
    pass
