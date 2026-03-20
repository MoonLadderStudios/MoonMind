"""drop orchestrator tables and workflow_task_states orchestrator columns

Revision ID: c1d2e3f4a5b6
Revises: 59830c78b458
Create Date: 2026-03-20

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "59830c78b458"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM task_source_mappings WHERE source = 'orchestrator'")
    op.execute(
        "DELETE FROM workflow_task_states WHERE orchestrator_run_id IS NOT NULL"
    )

    op.drop_constraint(
        "workflow_task_states_orchestrator_run_id_fkey",
        "workflow_task_states",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_orchestrator_task_state_attempt",
        "workflow_task_states",
        type_="unique",
    )
    op.drop_index(
        "ix_workflow_task_states_orchestrator_run_id",
        table_name="workflow_task_states",
    )
    op.drop_constraint(
        "ck_workflow_task_state_orchestrator_plan_step",
        "workflow_task_states",
        type_="check",
    )
    op.drop_constraint(
        "ck_workflow_task_state_run_id_exclusive",
        "workflow_task_states",
        type_="check",
    )

    op.drop_column("workflow_task_states", "worker_state")
    op.drop_column("workflow_task_states", "plan_step_status")
    op.drop_column("workflow_task_states", "plan_step")
    op.drop_column("workflow_task_states", "orchestrator_run_id")

    op.drop_constraint(
        "ck_workflow_task_state_task_name_required",
        "workflow_task_states",
        type_="check",
    )
    op.alter_column(
        "workflow_task_states",
        "workflow_run_id",
        existing_nullable=True,
        nullable=False,
    )
    op.alter_column(
        "workflow_task_states",
        "task_name",
        existing_nullable=True,
        nullable=False,
    )
    op.create_check_constraint(
        "ck_workflow_task_state_workflow_run_required",
        "workflow_task_states",
        "workflow_run_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_workflow_task_state_task_name_required",
        "workflow_task_states",
        "task_name IS NOT NULL",
    )

    op.drop_table("orchestrator_task_steps")
    op.drop_table("orchestrator_run_artifacts")
    op.drop_table("orchestrator_runs")
    op.drop_table("orchestrator_action_plans")
    op.drop_table("approval_gates")


def downgrade() -> None:
    raise NotImplementedError("Orchestrator removal migration is not reversible")
