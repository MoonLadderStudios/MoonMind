"""Integration coverage for MM-1091 checkpoint branch migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "migrations"
        / "versions"
        / "333_mm1091_checkpoint_branch_apis.py"
    )
    spec = importlib.util.spec_from_file_location(
        "mm1091_checkpoint_branch_apis",
        migration_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checkpoint_branch_migration_creates_tables_and_idempotency_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "temporal_execution_sources",
        metadata,
        sa.Column("workflow_id", sa.String(255), primary_key=True),
    )
    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO temporal_execution_sources (workflow_id) "
                "VALUES ('mm:wf-branch')"
            )
        )
        context = MigrationContext.configure(conn)
        monkeypatch.setattr(migration, "op", Operations(context))

        migration.upgrade()

        inspector = sa.inspect(conn)
        assert {
            "workflow_checkpoint_branches",
            "workflow_checkpoint_branch_turns",
            "workflow_checkpoint_branch_operations",
        }.issubset(set(inspector.get_table_names()))

        conn.execute(
            sa.text(
                "INSERT INTO workflow_checkpoint_branches "
                "(branch_id, workflow_id, root_workflow_id, source_run_id, "
                "source_checkpoint_boundary, source_checkpoint_ref, label, "
                "branch_kind, workspace_policy, runtime_context_policy, "
                "idempotency_key) "
                "VALUES ('cbr_test', 'mm:wf-branch', 'mm:wf-branch', "
                "'run-branch', 'after_execution', "
                "'artifact://checkpoints/after-implement', 'Branch', "
                "'checkpoint', 'apply_previous_execution_diff_to_clean_baseline', "
                "'fresh_agent_run', 'mm-1091:create')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO workflow_checkpoint_branch_operations "
                "(operation_id, workflow_id, branch_id, operation, "
                "idempotency_key, request_digest, response_payload) "
                "VALUES (:operation_id, 'mm:wf-branch', 'cbr_test', "
                "'checkpoint_branch.create', 'mm-1091:create', "
                "'sha256:request', '{}')"
            ),
            {"operation_id": uuid4().hex},
        )

        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO workflow_checkpoint_branch_operations "
                    "(operation_id, workflow_id, branch_id, operation, "
                    "idempotency_key, request_digest, response_payload) "
                    "VALUES (:operation_id, 'mm:wf-branch', 'cbr_test', "
                    "'checkpoint_branch.create', 'mm-1091:create', "
                    "'sha256:request', '{}')"
                ),
                {"operation_id": uuid4().hex},
            )
