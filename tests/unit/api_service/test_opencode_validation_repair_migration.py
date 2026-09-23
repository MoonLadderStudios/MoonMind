"""Migration coverage for the generic OpenCode validation-failure repair.

MoonLadderStudios/MoonMind#4526: rows stranded behind the old generic
``Pinned OpenCode runtime validation failed.`` marker re-enter ordinary
recovery; every other row stays exactly as it is.
"""

from __future__ import annotations

import importlib
import json

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = "api_service.migrations.versions.389_opencode_validation_repair"

_PRE_CHANGE_TABLE = sa.text(
    """
    CREATE TABLE managed_agent_provider_profiles (
        profile_id VARCHAR(128) PRIMARY KEY,
        runtime_id VARCHAR(64) NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT 1,
        auth_state VARCHAR(64),
        disabled_reason VARCHAR(64),
        secret_refs JSON,
        command_behavior JSON
    )
    """
)

_GENERIC = "Pinned OpenCode runtime validation failed."


def _row(profile_id, **overrides):
    behavior = {
        "auth_state": "validation_failed",
        "auth_readiness": {
            "connected": False,
            "launch_ready": False,
            "backing_secret_exists": False,
            "failure_reason": _GENERIC,
        },
    }
    record = {
        "profile_id": profile_id,
        "runtime_id": "opencode",
        "enabled": 0,
        "auth_state": "validation_failed",
        "disabled_reason": "auth_invalid",
        "secret_refs": json.dumps({"opencode_api_key": "db://saved-key"}),
        "command_behavior": json.dumps(behavior),
    }
    record.update(overrides)
    return record


def _run_upgrade(rows):
    migration = importlib.import_module(MIGRATION)
    assert migration.down_revision == "388_github_event_receipts_3967"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(_PRE_CHANGE_TABLE)
        for record in rows:
            connection.execute(
                sa.text(
                    "INSERT INTO managed_agent_provider_profiles "
                    "(profile_id, runtime_id, enabled, auth_state, "
                    "disabled_reason, secret_refs, command_behavior) VALUES "
                    "(:profile_id, :runtime_id, :enabled, :auth_state, "
                    ":disabled_reason, :secret_refs, :command_behavior)"
                ),
                record,
            )
        from unittest.mock import patch

        operations = Operations(MigrationContext.configure(connection))
        with patch.object(migration, "op", operations):
            migration.upgrade()

        out = {}
        for row in connection.execute(
            sa.text(
                "SELECT profile_id, runtime_id, enabled, auth_state, "
                "disabled_reason, secret_refs, command_behavior "
                "FROM managed_agent_provider_profiles"
            )
        ).mappings():
            out[row["profile_id"]] = dict(row)
    engine.dispose()
    return migration, out


def test_repair_transitions_only_generic_stranded_rows() -> None:
    other_behavior = json.dumps(
        {
            "auth_state": "validation_failed",
            "auth_readiness": {
                "connected": False,
                "launch_ready": False,
                "failure_reason": "OpenCode credential rejected: invalid api key",
            },
        }
    )
    migration, rows = _run_upgrade(
        [
            _row("eligible"),
            _row("stranded-without-credential", secret_refs=json.dumps({})),
            _row("explicit-disable", disabled_reason="user_disabled"),
            _row("no-credential-other-reason", secret_refs=json.dumps({}),
                 command_behavior=other_behavior),
            _row(
                "genuine-rejection",
                command_behavior=other_behavior,
            ),
            _row("other-runtime", runtime_id="codex_cli"),
            _row("already-connected", enabled=1, auth_state="connected",
                 disabled_reason=None),
        ]
    )

    eligible = rows["eligible"]
    assert eligible["enabled"] == 1
    assert eligible["auth_state"] == "connected"
    assert eligible["disabled_reason"] is None
    # Saved credential untouched.
    assert json.loads(eligible["secret_refs"]) == {"opencode_api_key": "db://saved-key"}
    behavior = json.loads(eligible["command_behavior"])
    readiness = behavior["auth_readiness"]
    assert readiness["connected"] is True
    assert readiness["backing_secret_exists"] is True
    assert readiness["launch_ready"] is False
    assert readiness["failure_reason"] != _GENERIC
    assert "re-validation scheduled" in readiness["failure_reason"]
    assert behavior["auth_state"] == "connected"
    assert "runtime_revalidation_failure" not in behavior

    # Rows stranded by the historical path carry the generic marker without a
    # saved key: they return to retryable enrollment pending, not to
    # connected, and no credential is fabricated.
    stranded = rows["stranded-without-credential"]
    assert stranded["enabled"] == 0
    assert stranded["auth_state"] == "api_key_pending"
    assert stranded["disabled_reason"] == "missing_credentials"
    assert json.loads(stranded["secret_refs"]) == {}
    stranded_behavior = json.loads(stranded["command_behavior"])
    stranded_readiness = stranded_behavior["auth_readiness"]
    assert stranded_readiness["connected"] is False
    assert stranded_readiness["backing_secret_exists"] is False
    assert stranded_readiness["launch_ready"] is False
    assert stranded_readiness["failure_reason"] != _GENERIC
    assert "re-validation scheduled" in stranded_readiness["failure_reason"] or \
        "Credential required" in stranded_behavior.get("auth_status_label", "")
    assert stranded_behavior["auth_state"] == "api_key_pending"

    # Everything else is byte-identical to its input.
    assert rows["explicit-disable"]["disabled_reason"] == "user_disabled"
    assert rows["explicit-disable"]["enabled"] == 0
    assert json.loads(rows["no-credential-other-reason"]["command_behavior"]) == json.loads(
        other_behavior
    )
    assert json.loads(rows["genuine-rejection"]["command_behavior"]) == json.loads(
        other_behavior
    )
    assert rows["other-runtime"]["runtime_id"] == "codex_cli"
    assert rows["other-runtime"]["auth_state"] == "validation_failed"
    assert rows["already-connected"]["auth_state"] == "connected"

    # Re-running the upgrade is harmless.
    _, rerun = _run_upgrade(
        [
            _row(
                "eligible",
                enabled=eligible["enabled"],
                auth_state=eligible["auth_state"],
                disabled_reason=eligible["disabled_reason"],
                secret_refs=eligible["secret_refs"],
                command_behavior=eligible["command_behavior"],
            )
        ]
    )
    assert rerun["eligible"] == eligible

    # The repair is forward-only by design.
    migration.downgrade()
