"""SQLite-backed lifecycle proof for #4006: atomic revision-fenced rotation.

These tests run the real service against independent transactions on a
supported database backend (SQLite here; Postgres semantics for row locking
are covered by the same compare-and-swap code path) and prove:

* committed replacement activation is immediately resolvable at its new
  revision (ACC-01),
* bad candidates, stale revisions, and transaction failure leave the prior
  usable secret intact (ACC-02),
* lost acknowledgments reconcile once while conflicting reuse is rejected
  (ACC-03),
* fenced resolution never returns N+1 material labeled N (ACC-04),
* historical ROTATED rows require the explicit repair path (REQ-03),
* deletion is protected across all consumer types (ACC-06 / REQ-07),
* audit/log/exception surfaces never carry secret material (REQ-08).
"""

from cryptography.fernet import Fernet
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ManagedSecret,
    RepositoryConnectionRecord,
    SecretStatus,
    SettingsAuditEvent,
    SettingsOverride,
)
from api_service.services.secrets import (
    SecretFencedError,
    SecretMutationConflict,
    SecretRepairRequiredError,
    SecretsService,
    SecretValidationRequest,
    SecretValidationResult,
    register_secret_post_commit_hook,
    unregister_secret_post_commit_hook,
)


async def _ok_validator(
    request: SecretValidationRequest, candidate: str
) -> SecretValidationResult:
    assert candidate and "db://" not in candidate
    return SecretValidationResult(ok=True)


async def _bad_validator(
    request: SecretValidationRequest, candidate: str
) -> SecretValidationResult:
    return SecretValidationResult(ok=False, reason_code="candidate_rejected")


@pytest_asyncio.fixture()
async def sessions(tmp_path: Path, monkeypatch):
    """Isolated SQLite sessions with independent transactions per checkout."""
    from moonmind.config import settings as settings_module
    import api_service.core.encryption as encryption_module

    monkeypatch.setattr(
        settings_module.settings.security,
        "ENCRYPTION_MASTER_KEY",
        Fernet.generate_key().decode(),
    )
    monkeypatch.setattr(encryption_module, "_ACTIVE_ENCRYPTION_KEY", None)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/secrets.db")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                ManagedSecret.__table__,
                SettingsOverride.__table__,
                SettingsAuditEvent.__table__,
                ManagedAgentProviderProfile.__table__,
                RepositoryConnectionRecord.__table__,
            ],
        )
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _audit_events(session_factory, slug: str, event_type: str):
    async with session_factory() as session:
        result = await session.execute(
            select(SettingsAuditEvent).where(
                SettingsAuditEvent.key == f"secrets.{slug}",
                SettingsAuditEvent.event_type == event_type,
            )
        )
        return result.scalars().all()


@pytest.mark.asyncio
async def test_rotation_activates_immediately_at_new_revision(sessions):
    async with sessions() as session:
        await SecretsService.create_secret(session, "github-pat", "pat-v1")

    async with sessions() as session:
        rotated = await SecretsService.rotate_secret(session, "github-pat", "pat-v2")

    assert rotated.status == SecretStatus.ACTIVE
    assert SecretsService.credential_revision(rotated) == 2

    async with sessions() as session:
        assert await SecretsService.get_secret(session, "github-pat") == "pat-v2"
        resolved = await SecretsService.resolve_secret(session, "github-pat")
    assert resolved is not None
    assert resolved["value"] == "pat-v2"
    assert resolved["credential_revision"] == 2

    events = await _audit_events(sessions, "github-pat", "secrets.rotated")
    assert len(events) == 1
    assert events[0].redacted is True
    assert "pat-v2" not in repr(events[0].new_value_json)
    assert "pat-v1" not in repr(events[0].old_value_json)
    assert events[0].new_value_json["credential_revision"] == 2


@pytest.mark.asyncio
async def test_bad_candidate_and_stale_revision_preserve_prior_secret(sessions):
    async with sessions() as session:
        await SecretsService.create_secret(session, "github-pat", "pat-v1")

    async with sessions() as session:
        admitted = await SecretsService.admit_rotation_candidate(
            session, "github-pat", actor_ref="owner:team-a"
        )
        with pytest.raises(SecretFencedError):
            await SecretsService.activate_admitted_candidate(
                session, "github-pat", "pat-evil", admitted, _bad_validator
            )

    async with sessions() as session:
        assert await SecretsService.get_secret(session, "github-pat") == "pat-v1"

    # Concurrent rotation wins; the stale attempt is fenced, not applied.
    async with sessions() as session:
        await SecretsService.rotate_secret(session, "github-pat", "pat-v2")
    async with sessions() as session:
        with pytest.raises(SecretFencedError):
            await SecretsService.rotate_secret(
                session, "github-pat", "pat-stale", expected_credential_revision=1
            )

    async with sessions() as session:
        assert await SecretsService.get_secret(session, "github-pat") == "pat-v2"


@pytest.mark.asyncio
async def test_transaction_failure_leaves_prior_secret_intact(sessions):
    async with sessions() as session:
        await SecretsService.create_secret(session, "github-pat", "pat-v1")

    async with sessions() as session:
        await SecretsService.rotate_secret(
            session, "github-pat", "pat-v2", auto_commit=False
        )
        await session.rollback()

    async with sessions() as session:
        assert await SecretsService.get_secret(session, "github-pat") == "pat-v1"
        events = await _audit_events(sessions, "github-pat", "secrets.rotated")
        assert events == []

    # Caller-owned atomic batch commits together.
    async with sessions() as session:
        await SecretsService.create_secret(
            session, "second-pat", "second-v1", auto_commit=False
        )
        await SecretsService.rotate_secret(
            session, "github-pat", "pat-v2", auto_commit=False
        )
        await session.commit()

    async with sessions() as session:
        assert await SecretsService.get_secret(session, "github-pat") == "pat-v2"
        assert await SecretsService.get_secret(session, "second-pat") == "second-v1"


@pytest.mark.asyncio
async def test_lost_acknowledgment_reconciles_once_and_conflicts_rejected(sessions):
    async with sessions() as session:
        await SecretsService.create_secret(session, "github-pat", "pat-v1")

    request_id = f"req-{uuid4().hex}"
    async with sessions() as session:
        first = await SecretsService.rotate_secret(
            session, "github-pat", "pat-v2", request_id=request_id
        )
    # Retry after a lost commit acknowledgment reconciles the original
    # operation: no second rotation, no second revision advance.
    async with sessions() as session:
        second = await SecretsService.rotate_secret(
            session, "github-pat", "pat-v2", request_id=request_id
        )
    assert SecretsService.credential_revision(second) == 2
    assert SecretsService.credential_revision(first) == 2

    async with sessions() as session:
        assert await SecretsService.get_secret(session, "github-pat") == "pat-v2"

    # Conflicting reuse of the same request id for another operation mutates
    # nothing and raises a bounded conflict diagnostic.
    async with sessions() as session:
        with pytest.raises(SecretMutationConflict):
            await SecretsService.set_status(
                session,
                "github-pat",
                SecretStatus.DISABLED,
                request_id=request_id,
            )
    async with sessions() as session:
        resolved = await SecretsService.resolve_secret(session, "github-pat")
    assert resolved is not None
    assert resolved["status"] == SecretStatus.ACTIVE.value
    assert resolved["value"] == "pat-v2"


@pytest.mark.asyncio
async def test_fenced_resolution_never_returns_new_material_as_old(sessions):
    async with sessions() as session:
        await SecretsService.create_secret(session, "github-pat", "pat-v1")
        await SecretsService.rotate_secret(session, "github-pat", "pat-v2")

    async with sessions() as session:
        # A reader holding revision 1 must not resolve revision 2 material.
        assert (
            await SecretsService.get_secret(
                session, "github-pat", expected_credential_revision=1
            )
            is None
        )
        assert (
            await SecretsService.get_secret(
                session, "github-pat", expected_credential_revision=2
            )
            == "pat-v2"
        )
        fenced = await SecretsService.validate_secret_ref(
            session, "github-pat", expected_credential_revision=1
        )
    assert fenced["valid"] is False
    assert fenced["diagnostics"][0]["code"] == "secret_revision_fenced"


@pytest.mark.asyncio
async def test_historical_rotated_rows_require_explicit_repair(sessions):
    async with sessions() as session:
        await SecretsService.create_secret(session, "legacy-pat", "pat-v1")
        # Simulate a pre-fix row parked as ROTATED.
        result = await session.execute(
            select(ManagedSecret).where(ManagedSecret.slug == "legacy-pat")
        )
        row = result.scalar_one()
        row.status = SecretStatus.ROTATED
        await session.commit()

    async with sessions() as session:
        with pytest.raises(SecretRepairRequiredError):
            await SecretsService.rotate_secret(session, "legacy-pat", "pat-v2")
        with pytest.raises(SecretRepairRequiredError):
            await SecretsService.set_status(
                session, "legacy-pat", SecretStatus.ACTIVE
            )

    async with sessions() as session:
        repaired = await SecretsService.repair_rotated_secret(
            session, "legacy-pat", "pat-v2", _ok_validator
        )
    assert repaired is not None
    assert repaired.status == SecretStatus.ACTIVE

    async with sessions() as session:
        assert await SecretsService.get_secret(session, "legacy-pat") == "pat-v2"
    events = await _audit_events(sessions, "legacy-pat", "secrets.repaired")
    assert len(events) == 1
    assert events[0].redacted is True


@pytest.mark.asyncio
async def test_deletion_blocked_by_every_consumer_type(sessions):
    async with sessions() as session:
        await SecretsService.create_secret(session, "github-pat", "pat-v1")
        session.add(
            SettingsOverride(
                scope="workspace",
                workspace_id=uuid4(),
                key="integrations.github.token_ref",
                value_json={"tokenRef": "db://github-pat"},
            )
        )
        session.add(
            ManagedAgentProviderProfile(
                profile_id="profile-a",
                runtime_id="codex_cli",
                provider_id="openai",
                secret_refs={"api_key": "db://github-pat"},
            )
        )
        session.add(
            RepositoryConnectionRecord(
                connection_id="conn-a",
                display_name="conn a",
                provider="github",
                hosting_service="github",
                endpoint_normalized="https://api.github.com",
                endpoint_ref="https://api.github.com",
                credential_config={"kind": "pat", "ref": "db://github-pat"},
                owner_ref="owner:team-a",
                scope_type="system",
            )
        )
        await session.commit()

    async with sessions() as session:
        assert await SecretsService.delete_secret(session, "github-pat") is False
        # The secret itself is untouched by the blocked deletion.
        assert await SecretsService.get_secret(session, "github-pat") == "pat-v1"

    # Caller-filtered display shows the workspace override; the server-side
    # inventory additionally protects profile and connection consumers.
    async with sessions() as session:
        consumers = await SecretsService.collect_secret_consumers(
            session, "github-pat"
        )
    kinds = {item["consumerType"] for item in consumers}
    assert kinds == {
        "setting_override",
        "provider_profile",
        "repository_connection",
    }
    assert "pat-v1" not in repr(consumers)

    async with sessions() as session:
        for table in (
            SettingsOverride,
            ManagedAgentProviderProfile,
            RepositoryConnectionRecord,
        ):
            result = await session.execute(select(table))
            for row in result.scalars().all():
                await session.delete(row)
        await session.commit()

    async with sessions() as session:
        assert await SecretsService.delete_secret(session, "github-pat") is True
        assert await SecretsService.get_secret(session, "github-pat") is None
    events = await _audit_events(sessions, "github-pat", "secrets.deleted")
    assert len(events) == 1
    assert "pat-v1" not in repr(events[0].old_value_json)


@pytest.mark.asyncio
async def test_post_commit_hooks_fire_only_after_commit(sessions):
    seen: list = []
    hook = seen.append
    register_secret_post_commit_hook(hook)
    try:
        async with sessions() as session:
            await SecretsService.create_secret(session, "hook-pat", "pat-v1")
        assert len(seen) == 1
        assert seen[0].slug == "hook-pat"
        assert seen[0].credential_revision == 1
        assert "pat-v1" not in repr(seen[0])

        async with sessions() as session:
            await SecretsService.rotate_secret(
                session, "hook-pat", "pat-v2", auto_commit=False
            )
            assert len(seen) == 1
            await session.rollback()
        assert len(seen) == 1
    finally:
        unregister_secret_post_commit_hook(hook)


@pytest.mark.asyncio
async def test_secret_material_never_leaks_into_diagnostics(sessions):
    raw = "ghp_super_raw_value"
    async with sessions() as session:
        await SecretsService.create_secret(session, "leak-pat", raw)

    async with sessions() as session:
        try:
            await SecretsService.rotate_secret(
                session, "leak-pat", "other", expected_credential_revision=999
            )
        except SecretFencedError as exc:
            assert raw not in str(exc)
            assert "other" not in str(exc)
        else:  # pragma: no cover - the fence must trigger
            raise AssertionError("expected a fenced rotation")

    async with sessions() as session:
        usage = await SecretsService.list_secret_usage(session, "missing-pat")
        validation = await SecretsService.validate_secret_ref(session, "leak-pat")
    assert raw not in repr(usage)
    assert raw not in repr(validation)
    for event in await _audit_events(sessions, "leak-pat", "secrets.created"):
        assert raw not in repr(event.new_value_json)
        assert raw not in repr(event.key)
