"""Persist proven Codex login expiry without invalidating a new enrollment."""

import os
import subprocess
import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db import base
from api_service.db.models import (
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    ProviderProfileAuthState,
    ProviderProfileDisabledReason,
    RuntimeMaterializationMode,
)
from api_service.services import provider_profile_service
from api_service.services.provider_profile_readiness import (
    provider_profile_launch_ready,
)
from moonmind.omnigent import codex_login


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (
            {"result": {"account": {"type": "chatgpt", "email": "private-sentinel"}}},
            "ready",
        ),
        (
            {
                "error": {
                    "code": -32603,
                    "message": "workspace routing discovery unauthorized (401) private-sentinel",
                }
            },
            "unauthorized",
        ),
        ({"error": {"code": 401, "message": "private-sentinel"}}, "unauthorized"),
        ({"result": {"account": None, "requiresOpenaiAuth": True}}, "missing_login"),
        (
            {
                "error": {
                    "code": -32603,
                    "message": "provider outage (503) private-sentinel",
                }
            },
            "unavailable",
        ),
        ({"error": {"code": -32601, "message": "method unavailable"}}, "unavailable"),
        ({"result": {"account": None}}, "unavailable"),
        ({"result": {"account": {"type": "apiKey"}}}, "unavailable"),
        (
            [
                {"error": {"code": 401}},
                {"result": {"account": {"type": "chatgpt"}}},
            ],
            "ready",
        ),
        (
            [
                {"error": {"code": 401}},
                {"error": {"code": -32603, "message": "provider outage (503)"}},
            ],
            "unavailable",
        ),
    ],
)
def test_account_read_probe_checks_provider_without_turns_or_private_output(
    tmp_path, reply, expected
):
    cli = tmp_path / "codex"
    replies = reply if isinstance(reply, list) else [reply]
    cli.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "assert sys.argv[1:] == ['app-server']\n"
        f"assert os.environ['CODEX_HOME'] == {str(tmp_path)!r}\n"
        f"pathlib.Path({str(tmp_path / 'probe.pid')!r}).write_text(str(os.getpid()))\n"
        f"replies = {replies!r}\n"
        "account_reads = 0\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if request['method'] == 'initialized': continue\n"
        "    if request['method'] == 'initialize':\n"
        "        response = {'id': request['id'], 'result': {}}\n"
        "    else:\n"
        "        assert request['method'] == 'account/read'\n"
        "        assert account_reads < 2\n"
        "        assert request['params'] == {'refreshToken': account_reads == 1}\n"
        "        response = {'id': request['id'], **replies[min(account_reads, len(replies)-1)]}\n"
        "        account_reads += 1\n"
        "    print(json.dumps({'method': 'account/updated', 'params': {'email': 'private-sentinel'}}), flush=True)\n"
        "    print(json.dumps(response), flush=True)\n"
        "    sys.stderr.write('private-sentinel\\n')\n"
    )
    cli.chmod(0o755)
    completed = subprocess.run(
        [sys.executable, "-c", codex_login.CODEX_ACCOUNT_READ_PROBE, str(tmp_path)],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}"},
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert completed.stdout == expected + "\n"
    assert completed.stderr == ""
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "probe.pid").read_text()), 0)


def test_account_read_probe_reports_unavailable_when_cli_cannot_start(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-c", codex_login.CODEX_ACCOUNT_READ_PROBE, str(tmp_path)],
        env={**os.environ, "PATH": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert completed.stdout == "unavailable\n"
    assert completed.stderr == ""


@pytest_asyncio.fixture
async def enrolled_profile(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/profiles.db")
    async with engine.begin() as connection:
        await connection.run_sync(ManagedAgentProviderProfile.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(base, "async_session_maker", factory)
    sync = AsyncMock()
    monkeypatch.setattr(provider_profile_service, "sync_provider_profile_manager", sync)
    async with factory() as session:
        profile = ManagedAgentProviderProfile(
            profile_id="codex-oauth",
            capacity_scope_ref="provider-profile:codex-oauth",
            credential_generation=4,
            runtime_id="codex_cli",
            provider_id="openai",
            credential_source=ProviderCredentialSource.OAUTH_VOLUME,
            runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
            volume_ref="retained-credential-volume",
            volume_mount_path="/home/app/.codex",
            enabled=True,
            auth_state=ProviderProfileAuthState.CONNECTED,
            max_parallel_runs=1,
            last_validated_at=datetime(2026, 10, 5, tzinfo=UTC),
            command_behavior={"operator_setting": "preserved"},
        )
        provider_profile_service.apply_oauth_connected_state(
            profile,
            mapping=provider_profile_service.get_first_party_oauth_profile(
                "codex_cli", "openai"
            ),
            validated_at=profile.last_validated_at,
        )
        session.add(profile)
        await session.commit()
        assert provider_profile_launch_ready(profile)
    try:
        yield factory, sync
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ended_login_disables_profile_and_syncs_routing(enrolled_profile):
    factory, sync = enrolled_profile

    await codex_login.record_ended_codex_login("codex-oauth", 4)

    async with factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, "codex-oauth")
        assert profile.auth_state == ProviderProfileAuthState.VALIDATION_FAILED
        assert profile.disabled_reason == ProviderProfileDisabledReason.AUTH_INVALID
        assert profile.enabled is False
        assert provider_profile_launch_ready(profile) is False
        assert profile.command_behavior["auth_state"] == "validation_failed"
        assert profile.command_behavior["auth_readiness"]["launch_ready"] is False
        assert profile.command_behavior["auth_readiness"]["failure_reason"] == (
            "codex_login_session_ended"
        )
        assert "connect_oauth" in profile.command_behavior["auth_actions"]
        assert profile.command_behavior["operator_setting"] == "preserved"
        assert profile.volume_ref == "retained-credential-volume"
        assert profile.credential_generation == 4
        failed_at = profile.last_validated_at
    sync.assert_awaited_once()
    assert sync.await_args.kwargs["runtime_id"] == "codex_cli"

    # A duplicated Activity must not rewrite the original failed observation.
    await codex_login.record_ended_codex_login("codex-oauth", 4)
    async with factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, "codex-oauth")
        assert profile.last_validated_at == failed_at


@pytest.mark.asyncio
async def test_account_rejection_records_specific_reason_without_deleting_login(
    enrolled_profile,
):
    factory, sync = enrolled_profile
    await codex_login.record_ended_codex_login(
        "codex-oauth", 4, reason="codex_account_read_unauthorized"
    )
    async with factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, "codex-oauth")
        assert profile.auth_state == ProviderProfileAuthState.VALIDATION_FAILED
        assert provider_profile_launch_ready(profile) is False
        assert profile.command_behavior["auth_readiness"]["failure_reason"] == (
            "codex_account_read_unauthorized"
        )
        assert profile.volume_ref == "retained-credential-volume"
        assert profile.credential_generation == 4
    sync.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["rotated", "api_key", "other_runtime", "disconnected", "operator_disabled"],
)
async def test_old_observation_preserves_replaced_or_disconnected_profile(
    enrolled_profile, change
):
    factory, sync = enrolled_profile
    async with factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, "codex-oauth")
        if change == "rotated":
            profile.credential_generation = 5
        elif change == "api_key":
            profile.credential_source = ProviderCredentialSource.SECRET_REF
            profile.runtime_materialization_mode = (
                RuntimeMaterializationMode.API_KEY_ENV
            )
        elif change == "other_runtime":
            profile.runtime_id = "claude_code"
            profile.provider_id = "anthropic"
        elif change == "disconnected":
            profile.auth_state = ProviderProfileAuthState.DISCONNECTED
            profile.disabled_reason = ProviderProfileDisabledReason.DISCONNECTED
            profile.enabled = False
        else:
            profile.disabled_reason = ProviderProfileDisabledReason.USER_DISABLED
            profile.enabled = False
        await session.commit()
        before = (profile.enabled, profile.auth_state, profile.disabled_reason)
        behavior = profile.command_behavior

    await codex_login.record_ended_codex_login("codex-oauth", 4)

    async with factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, "codex-oauth")
        assert (profile.enabled, profile.auth_state, profile.disabled_reason) == before
        assert profile.command_behavior == behavior
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_profile_is_not_recreated(enrolled_profile):
    factory, sync = enrolled_profile
    await codex_login.record_ended_codex_login("deleted", 4)
    async with factory() as session:
        assert await session.get(ManagedAgentProviderProfile, "deleted") is None
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_state_write_failure_is_observable_without_secret_text(
    monkeypatch, caplog
):
    def unavailable():
        raise RuntimeError("private credential sentinel")

    monkeypatch.setattr(base, "async_session_maker", unavailable)
    await codex_login.record_ended_codex_login("private-profile-sentinel", 4)
    assert "Could not record ended Codex login" in caplog.text
    assert "credential_generation=4" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "private-profile-sentinel" not in caplog.text
    assert "private credential sentinel" not in caplog.text
    first_diagnostic = caplog.records[-1].getMessage()
    await codex_login.record_ended_codex_login("another-private-profile", 4)
    assert caplog.records[-1].getMessage() == first_diagnostic


@pytest.mark.asyncio
async def test_manager_failure_does_not_undo_recorded_expiry(enrolled_profile, caplog):
    factory, sync = enrolled_profile
    sync.side_effect = RuntimeError("private credential sentinel")
    await codex_login.record_ended_codex_login("codex-oauth", 4)
    async with factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, "codex-oauth")
        assert profile.auth_state == ProviderProfileAuthState.VALIDATION_FAILED
        assert provider_profile_launch_ready(profile) is False
    assert "private credential sentinel" not in caplog.text
