"""PostgreSQL upgrade coverage for the follow-up proposal schema removal."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest.fixture
def proposal_removal_postgres_url() -> str:
    """Provide an isolated PostgreSQL database for a zero-to-head upgrade."""

    configured = os.getenv("MOONMIND_TEST_POSTGRES_URL", "").strip()
    if configured:
        configured = configured.replace(
            "postgresql+asyncpg://", "postgresql+psycopg2://", 1
        )
        configured = configured.replace("postgresql://", "postgresql+psycopg2://", 1)
        if not configured.startswith("postgresql+psycopg2://"):
            pytest.fail("MOONMIND_TEST_POSTGRES_URL must use PostgreSQL")
        yield configured
        return

    initdb_path = shutil.which("initdb")
    if initdb_path is None:
        candidates = sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
        initdb_path = str(candidates[-1]) if candidates else None
    if initdb_path is None:
        pytest.fail("PostgreSQL test binaries are unavailable in the test image")

    initdb = str(Path(initdb_path))
    pg_ctl = str(Path(initdb).with_name("pg_ctl"))
    data_root = Path(tempfile.mkdtemp(prefix="moonmind-proposal-removal-postgres-"))
    data_dir = data_root / "data"
    log_path = data_root / "postgres.log"
    command_prefix: list[str] = []
    if os.geteuid() == 0:
        shutil.chown(data_root, user="postgres", group="postgres")
        command_prefix = ["runuser", "--user", "postgres", "--"]

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    subprocess.run(
        [
            *command_prefix,
            initdb,
            "--pgdata",
            str(data_dir),
            "--username",
            "postgres",
            "--auth",
            "trust",
            "--encoding",
            "UTF8",
            "--no-locale",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            *command_prefix,
            pg_ctl,
            "--pgdata",
            str(data_dir),
            "--log",
            str(log_path),
            "--options",
            f"-F -p {port} -h 127.0.0.1 -k {data_dir}",
            "--wait",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        yield f"postgresql+psycopg2://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(
            [
                *command_prefix,
                pg_ctl,
                "--pgdata",
                str(data_dir),
                "--mode",
                "immediate",
                "--wait",
                "stop",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(data_root, ignore_errors=True)


def test_fresh_postgres_upgrade_ends_without_proposal_tables_or_enums(
    proposal_removal_postgres_url: str,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    database_url = make_url(proposal_removal_postgres_url)
    env = os.environ.copy()
    env.update(
        {
            "POSTGRES_HOST": str(database_url.host),
            "POSTGRES_PORT": str(database_url.port),
            "POSTGRES_USER": str(database_url.username),
            "POSTGRES_PASSWORD": str(database_url.password or ""),
            "POSTGRES_DB": str(database_url.database),
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "api_service/migrations/alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr[-4000:]

    engine = sa.create_engine(proposal_removal_postgres_url)
    try:
        inspector = sa.inspect(engine)
        assert "workflow_proposals" not in inspector.get_table_names()
        assert "workflow_proposal_notifications" not in inspector.get_table_names()
        with engine.connect() as connection:
            retained_types = connection.execute(
                sa.text(
                    """
                    select typname
                    from pg_type
                    where typname in (
                        'workflowproposalstatus',
                        'workflowproposalpriority',
                        'workflowproposaloriginsource'
                    )
                    """
                )
            ).scalars()
            assert list(retained_types) == []
    finally:
        engine.dispose()
