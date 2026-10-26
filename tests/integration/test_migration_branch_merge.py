"""Real PostgreSQL upgrades from either published index branch retain data."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.integration import test_single_user_conversion_postgres_4346 as postgres

conversion_postgres_url = postgres.conversion_postgres_url
pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]
ROOT = Path(__file__).resolve().parents[2]
COMMON = "401_artifact_recovery_indexes"
PENDING = "402_pending_execution_intents"
CONVERSION = "403_conversion_completion_lookup"
MERGED = "404_merge_index_heads"
INDEXES = {
    "ix_temporal_execution_pending_intents",
    "ix_single_user_conversion_published_schema",
}


@pytest.mark.parametrize("starting", ["clean", COMMON, PENDING, CONVERSION, "both"])
def test_published_branches_upgrade_and_rollback_without_losing_rows(
    conversion_postgres_url, starting
):
    schema = "migration_merge_" + uuid4().hex
    url = make_url(conversion_postgres_url)
    sync_url = url.set(drivername="postgresql+psycopg2")
    admin = create_engine(sync_url)
    engine = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={schema}"}
    )
    env = {
        **os.environ,
        "POSTGRES_HOST": url.host,
        "POSTGRES_PORT": str(url.port or 5432),
        "POSTGRES_USER": url.username,
        "POSTGRES_PASSWORD": url.password or "",
        "POSTGRES_DB": url.database,
        "PGOPTIONS": f"-csearch_path={schema}",
        "ENCRYPTION_MASTER_KEY": base64.urlsafe_b64encode(bytes(32)).decode(),
        "OTEL_SDK_DISABLED": "true",
    }

    def migrate(action, target):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "api_service/migrations/alembic.ini",
                action,
                target,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stderr[-5000:]

    def revisions():
        with engine.connect() as conn:
            return set(
                conn.execute(text("SELECT version_num FROM alembic_version")).scalars()
            )

    def indexes():
        with engine.connect() as conn:
            return dict(
                conn.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = :schema "
                        "AND indexname IN ('ix_temporal_execution_pending_intents', "
                        "'ix_single_user_conversion_published_schema')"
                    ),
                    {"schema": schema},
                ).all()
            )

    def retained_rows():
        with engine.connect() as conn:
            execution = (
                conn.execute(
                    text(
                        "SELECT workflow_id, run_id, namespace, owner_type, state, close_status, "
                        "memo, parameters, input_ref, plan_ref, manifest_ref, create_idempotency_key, "
                        "created_at, updated_at FROM temporal_execution_sources"
                    )
                )
                .mappings()
                .one()
            )
            conversion = (
                conn.execute(
                    text(
                        "SELECT id, preflight_digest, status, result_json FROM single_user_conversion_runs"
                    )
                )
                .mappings()
                .one()
            )
            return dict(execution), dict(conversion)

    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        if starting == "clean":
            migrate("upgrade", MERGED)
        elif starting == "both":
            migrate("upgrade", PENDING)
            migrate("upgrade", CONVERSION)
            assert revisions() == {PENDING, CONVERSION}
        else:
            migrate("upgrade", starting)
            assert revisions() == {starting}

        original_indexes = indexes()
        memo = {"temporalStart": {"state": "pending", "requestId": "retained-request"}}
        parameters = {
            "input": "artifact://retained",
            "scheduledFor": "2030-01-02T03:04:05Z",
        }
        proof = {
            "published": True,
            "production_transforms_completed": True,
            "schema_revision": COMMON,
            "retained": "conversion evidence",
        }
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO temporal_execution_sources "
                    "(workflow_id, run_id, namespace, workflow_type, owner_type, state, entry, "
                    "search_attributes, memo, artifact_refs, parameters, paused, awaiting_external, "
                    "attention_required, step_count, wait_cycle_count, rerun_count, input_ref, "
                    "plan_ref, manifest_ref, create_idempotency_key) VALUES "
                    "('fixture-workflow', 'fixture-run', 'fixture', 'MoonMind.UserWorkflow', "
                    "'system', 'initializing', 'workflow', '{}', CAST(:memo AS jsonb), '[]', "
                    "CAST(:parameters AS jsonb), false, false, false, 0, 0, 0, "
                    "'artifact://input', 'artifact://plan', 'artifact://manifest', 'retained-key')"
                ),
                {"memo": json.dumps(memo), "parameters": json.dumps(parameters)},
            )
            conn.execute(
                text(
                    "INSERT INTO single_user_conversion_runs (id, preflight_digest, status, result_json) "
                    "VALUES (:id, 'retained-preflight', 'complete', CAST(:proof AS jsonb))"
                ),
                {"id": str(uuid4()), "proof": json.dumps(proof)},
            )
        retained = retained_rows()

        migrate("upgrade", MERGED)
        merged = revisions()
        assert merged == {MERGED}
        assert retained_rows() == retained
        installed_indexes = indexes()
        assert set(installed_indexes) == INDEXES
        assert original_indexes.items() <= installed_indexes.items()

        # Rolling back the merge to either parent retains both applied
        # branches. Rolling back to their common parent removes both indexes.
        target = starting if starting in {COMMON, PENDING, CONVERSION} else COMMON
        migrate("downgrade", target)
        assert revisions() == ({PENDING, CONVERSION} if target != COMMON else {COMMON})
        assert retained_rows() == retained
        expected = INDEXES if target != COMMON else set()
        assert set(indexes()) == expected
        migrate("upgrade", MERGED)
        assert revisions() == merged
        assert indexes() == installed_indexes
        assert retained_rows() == retained
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
