"""Issue #4346: startup guard preserves source and never seeds a bypass.

Release-level fixture: the supported upgrade/startup route consults the
guarded conversion entrypoint. Fresh databases initialize without an
account, refused sources keep serving unchanged, and no restart seeds a
default user or publishes a candidate on stale attribution.
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import ManagedSecret
from api_service.main import health_check, startup_event

# Registers the conversion ledger on ``Base.metadata`` (Alembic 386 creates it
# in production); without it a fresh process defers conversion on startup.
from api_service.services.single_user_conversion import SingleUserConversionRun
from tests.helpers.account_source import User, UserProfile, create_source_schema

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


async def _seed_db(tmp_path, name="guard4346.db"):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/{name}"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(create_source_schema)


async def _reported_conversion():
    """The disposition the deployment controller reads from ``/healthz``."""
    response = await health_check()
    body = response if isinstance(response, dict) else json.loads(response.body)
    return body.get("singleUserConversion")


@pytest.mark.parametrize("base_url", ["", "https://operator.example"])
async def test_startup_keeps_retained_access_until_replacement_is_configured(
    provider_env_keys, tmp_path, monkeypatch, base_url
):
    from api_service.main import _run_guarded_single_user_upgrade

    await _seed_db(tmp_path, "replacement-admission.db")
    async with db_base.async_session_maker() as session:
        owner = User(id=uuid.uuid4(), email="retained@example.invalid")
        session.add(owner)
        await session.commit()
        owner_id = owner.id
    monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "0.0.0.0")
    monkeypatch.delenv("MOONMIND_TRUSTED_INGRESS", raising=False)
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", base_url)
    monkeypatch.delenv("MOONMIND_TRUSTED_PROXIES", raising=False)

    result = await _run_guarded_single_user_upgrade()

    assert result == {"deferred": True, "reason": "replacement_admission_unavailable"}
    async with db_base.async_session_maker() as session:
        assert await session.get(User, owner_id) is not None
        assert (
            await session.execute(select(SingleUserConversionRun))
        ).scalars().all() == []


@pytest.mark.parametrize("base_url", ["", "https://operator.example"])
async def test_startup_converts_under_an_approved_publication(
    provider_env_keys, tmp_path, monkeypatch, base_url
):
    """The approved binding admits before and after conversion, so it may run."""
    from api_service.main import _run_guarded_single_user_upgrade

    await _seed_db(tmp_path, "approved-publication.db")
    monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "0.0.0.0")
    monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", "1")
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", base_url)
    monkeypatch.delenv("MOONMIND_TRUSTED_PROXIES", raising=False)

    result = await _run_guarded_single_user_upgrade()

    assert not result.get("deferred")
    async with db_base.async_session_maker() as session:
        assert (await session.execute(select(SingleUserConversionRun))).scalars().all()


@pytest.mark.asyncio
async def test_startup_seeds_no_default_user_on_fresh_database(
    provider_env_keys, tmp_path
):
    await _seed_db(tmp_path, "fresh.db")
    with (patch("api_service.main._initialize_operator_admission"),):
        await startup_event()
    async with db_base.async_session_maker() as session:
        from api_service.migrations.account_source import source_user_ids

        assert await source_user_ids(session) == []
    assert await _reported_conversion() == {
        "published": True,
        "disposition": "fresh_init",
        "reasonCode": "empty_source",
        "personCount": 0,
    }


@pytest.mark.asyncio
async def test_startup_preserves_refused_multi_person_source(
    provider_env_keys, tmp_path
):
    await _seed_db(tmp_path, "refused.db")
    async with db_base.async_session_maker() as session:
        u1 = User(id=uuid.uuid4(), email="op-a@example.com")
        u2 = User(id=uuid.uuid4(), email="op-b@example.com")
        session.add_all([u1, u2])
        await session.flush()
        session.add(UserProfile(user_id=u1.id, openai_api_key_encrypted="tok-a"))
        session.add(UserProfile(user_id=u2.id, openai_api_key_encrypted="tok-b"))
        await session.commit()
        before = sorted(str(u) for u in (await session.execute(select(User.id))).scalars().all())

    from api_service.services import single_user_conversion as conversion

    collect = AsyncMock(wraps=conversion.collect_inventory)
    with (
        patch("api_service.main._initialize_operator_admission"),
        patch.object(conversion, "collect_inventory", collect),
    ):
        await startup_event()
    # One startup reads retained history once; it holds the HTTP listener.
    assert collect.await_count == 1

    async with db_base.async_session_maker() as session:
        after = sorted(str(u) for u in (await session.execute(select(User.id))).scalars().all())
        assert after == before
        # Refusal performs no conversion-side mutation: no converted secrets.
        legacy = (
            (
                await session.execute(
                    select(ManagedSecret).where(
                        ManagedSecret.slug.like("legacy-user-profile-%")
                    )
                )
            )
            .scalars()
            .all()
        )
        assert legacy == []
    # The refusal reaches the deployment result as redacted metadata only.
    reported = await _reported_conversion()
    assert reported["published"] is False
    assert reported["disposition"] == "multi_person_refusal"
    assert reported["personCount"] == 2
    assert "op-a@example.com" not in json.dumps(reported)


@pytest.mark.asyncio
async def test_refused_release_workflows_and_operator_access_still_function(
    provider_env_keys, tmp_path
):
    """Release-level refusal: old workflows readable, guard reports redacted."""
    from api_service.db.models import TemporalArtifact, WorkflowRun

    await _seed_db(tmp_path, "release-refused.db")
    async with db_base.async_session_maker() as session:
        u1 = User(id=uuid.uuid4(), email="rel-a@example.com")
        u2 = User(id=uuid.uuid4(), email="rel-b@example.com")
        session.add_all([u1, u2])
        await session.flush()
        session.add(UserProfile(user_id=u1.id))
        session.add(UserProfile(user_id=u2.id))
        session.add(WorkflowRun(feature_key="rel-feat", requested_by_user_id=u1.id))
        session.add(
            TemporalArtifact(
                artifact_id="rel-art-1",
                storage_key="rel-key-1",
                created_by_principal=str(u2.id),
            )
        )
        await session.commit()

    with (patch("api_service.main._initialize_operator_admission"),):
        await startup_event()

    async with db_base.async_session_maker() as session:
        from api_service.services.single_user_conversion import (
            startup_guard_decision,
        )

        # Old-release workflows still function: rows preserved, no mutation.
        runs = (await session.execute(select(WorkflowRun))).scalars().all()
        assert len(runs) == 1
        assert runs[0].feature_key == "rel-feat"
        artifacts = (await session.execute(select(TemporalArtifact))).scalars().all()
        assert len(artifacts) == 1
        users = (await session.execute(select(User))).scalars().all()
        assert len(users) == 2
        assert (await session.execute(select(ManagedSecret))).scalars().all() == []
        # Operator access path: guard remains readable and reports refusal
        # with redacted evidence only. Startup's own admitted work (preset
        # seed catalog) may add unowned seed rows, so any refusal disposition
        # is a valid blocked outcome here.
        decision = await startup_guard_decision(session)
        assert decision.eligible is False
        assert decision.disposition in {"multi_person_refusal", "unresolved_refusal"}
        blob = str(decision.to_sanitized_dict())
        assert "rel-a@example.com" not in blob
        assert "rel-b@example.com" not in blob


@pytest.mark.asyncio
async def test_disabled_boundary_serves_after_proven_fresh_conversion(
    provider_env_keys, tmp_path
):
    """Ledger-proven fresh state serves without minting a User row."""
    from fastapi import Request

    from api_service.auth_providers import get_instance_access
    from api_service.services.single_user_conversion import run_guarded_upgrade
    from moonmind.security.operator_admission import OperatorAdmission

    await _seed_db(tmp_path, "boundary-fresh.db")
    async with db_base.async_session_maker() as session:
        result = await run_guarded_upgrade(session, operator_authorized=True)
        assert result.published is True
    async with db_base.async_session_maker() as session:
        user = await get_instance_access(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "scheme": "http",
                    "path": "/",
                    "headers": [(b"host", b"localhost")],
                    "client": ("127.0.0.1", 1234),
                }
            ),
            session,
        )
        assert isinstance(user, OperatorAdmission)
        # No synthetic account was minted to satisfy the boundary.
        from api_service.migrations.account_source import source_user_ids

        assert await source_user_ids(session) == []


@pytest.mark.asyncio
async def test_disabled_boundary_stays_setup_required_without_conversion(
    provider_env_keys, tmp_path
):
    """Failed/missing conversion must not expose account-free data."""
    from fastapi import HTTPException, Request

    from api_service.auth_providers import get_instance_access

    await _seed_db(tmp_path, "boundary-blocked.db")
    async with db_base.async_session_maker() as session:
        with pytest.raises(HTTPException) as exc:
            await get_instance_access(
                Request(
                    {
                        "type": "http",
                        "method": "GET",
                        "scheme": "http",
                        "path": "/",
                        "headers": [(b"host", b"localhost")],
                        "client": ("127.0.0.1", 1234),
                    }
                ),
                session,
            )
        assert exc.value.status_code == 503
        assert exc.value.detail == "conversion_required"
