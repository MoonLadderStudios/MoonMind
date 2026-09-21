"""Review findings for PR #4461 (issue #4346, operator + Codex P1 comments).

Covers: unknown inventory reads refusing as ``inventory_unavailable``
(never empty/eligible), operator authorization gating for alias and
collision evidence, context-aware settings collisions (same effective
context only), deployment provenance for seeded presets, null-owner
``workflow_runs`` as unowned retained data, dangling provider-profile
rewires in the profile-secrets transform, same-count content changes
tripping staleness, interruption convergence without an ``in_progress``
lock, fail-closed stale rows without the conversion claim, and replays
that never default a missing completion to success.
"""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base


def _factory(tmp_path, name="conv4461.db"):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


async def _mk_user(session, **kw):
    from api_service.db.models import User

    u = User(id=uuid.uuid4(), email=kw.get("email", f"{uuid.uuid4().hex}@ex.com"))
    for k, v in kw.items():
        if k != "email":
            setattr(u, k, v)
    session.add(u)
    await session.flush()
    return u


def _profile_row(profile_id, **overrides):
    from api_service.db.models import ManagedAgentProviderProfile

    params = {
        "profile_id": profile_id,
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "credential_generation": 1,
        "enabled": True,
        "auth_state": "connected",
        "default_model": "gpt-5",
        "default_effort": "high",
    }
    params.update(overrides)
    return ManagedAgentProviderProfile(**params)


@pytest.mark.asyncio
async def test_unreadable_inventory_refuses_as_unknown(tmp_path):
    """A failed required read is unknown evidence, never empty/eligible."""
    from api_service.services.single_user_conversion import (
        collect_inventory,
        evaluate_disposition,
    )

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # The table stays in metadata but disappears from storage, so the
    # SELECT fails with a real database error instead of a skip.
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE settings_overrides"))
    async with factory() as session:
        inv = await collect_inventory(session)
        assert inv.inventory_errors, "failed read must be recorded"
        assert any("settings" in surface for surface in inv.inventory_errors)
        decision = await evaluate_disposition(session)
        assert decision.disposition == "unresolved_refusal"
        assert decision.reason_code == "inventory_unavailable"
        assert decision.eligible is False


@pytest.mark.asyncio
async def test_alias_evidence_requires_operator_authorization(tmp_path):
    from api_service.services.single_user_conversion import (
        ConversionAuthorizationError,
        evaluate_disposition,
    )

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import UserProfile

        u1 = await _mk_user(session)
        u2 = await _mk_user(session)
        session.add(UserProfile(user_id=u1.id))
        session.add(UserProfile(user_id=u2.id))
        await session.commit()
        with pytest.raises(ConversionAuthorizationError):
            await evaluate_disposition(
                session, alias_groups=[{str(u1.id), str(u2.id)}]
            )
        ok = await evaluate_disposition(
            session,
            alias_groups=[{str(u1.id), str(u2.id)}],
            operator_authorized=True,
        )
        assert ok.disposition == "eligible_conversion"


@pytest.mark.asyncio
async def test_collision_resolution_requires_operator_authorization(tmp_path):
    from api_service.services.single_user_conversion import (
        ConversionAuthorizationError,
        evaluate_disposition,
    )

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        with pytest.raises(ConversionAuthorizationError):
            await evaluate_disposition(
                session, collision_resolutions={"theme": "dark"}
            )


@pytest.mark.asyncio
async def test_settings_same_key_different_scopes_is_not_a_collision(tmp_path):
    """Override precedence across scopes is not incompatible data."""
    from api_service.services.single_user_conversion import evaluate_disposition

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import SettingsOverride

        u = await _mk_user(session)
        session.add(
            SettingsOverride(
                scope="user", user_id=u.id, key="theme", value_json={"v": 1}
            )
        )
        session.add(
            SettingsOverride(
                scope="workspace", user_id=u.id, key="theme", value_json={"v": 2}
            )
        )
        await session.commit()
        decision = await evaluate_disposition(session)
        assert decision.disposition == "eligible_conversion"
        assert decision.eligible is True


@pytest.mark.asyncio
async def test_settings_same_context_distinct_values_collide(tmp_path):
    """Two effective values for one context need an explicit disposition."""
    from api_service.services.single_user_conversion import evaluate_disposition

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import SettingsOverride

        u1 = await _mk_user(session)
        u2 = await _mk_user(session)
        session.add(
            SettingsOverride(
                scope="user", user_id=u1.id, key="theme", value_json={"v": 1}
            )
        )
        session.add(
            SettingsOverride(
                scope="user", user_id=u2.id, key="theme", value_json={"v": 2}
            )
        )
        await session.commit()
        aliased = await evaluate_disposition(
            session,
            alias_groups=[{str(u1.id), str(u2.id)}],
            operator_authorized=True,
        )
        assert aliased.disposition == "unresolved_refusal"
        assert aliased.reason_code == "settings_collision"
        resolved = await evaluate_disposition(
            session,
            alias_groups=[{str(u1.id), str(u2.id)}],
            collision_resolutions={"theme": {"v": 1}},
            operator_authorized=True,
        )
        assert resolved.disposition == "eligible_conversion"


@pytest.mark.asyncio
async def test_seeded_presets_carry_deployment_provenance(tmp_path):
    """Seed-stamped presets are deployment-owned; legacy ones still refuse."""
    from api_service.services.single_user_conversion import evaluate_disposition

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import Preset, PresetScopeType

        session.add(
            Preset(
                slug="seeded",
                scope_type=PresetScopeType.GLOBAL,
                title="Seeded",
                description="deployment seed",
                created_by=None,
                seed_source="seed-catalog",
            )
        )
        await session.commit()
        decision = await evaluate_disposition(session)
        assert decision.disposition == "fresh_init"
        assert decision.eligible is True

        session.add(
            Preset(
                slug="legacy",
                scope_type=PresetScopeType.GLOBAL,
                title="Legacy",
                description="unowned retained preset",
                created_by=None,
                seed_source=None,
            )
        )
        await session.commit()
        refused = await evaluate_disposition(session)
        assert refused.disposition == "unresolved_refusal"
        assert refused.reason_code == "unowned_rows"


@pytest.mark.asyncio
async def test_null_owner_workflow_runs_are_unowned(tmp_path):
    """Ownerless runs are retained data, not empty sources."""
    from api_service.services.single_user_conversion import evaluate_disposition

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import WorkflowRun

        # Both user columns null (as after ON DELETE SET NULL): a retained
        # run with no attribution, not an empty source.
        session.add(WorkflowRun(feature_key="orphan", requested_by_user_id=None))
        await session.commit()
        decision = await evaluate_disposition(session)
        assert decision.disposition == "unresolved_refusal"
        assert decision.reason_code == "unowned_rows"
        assert decision.eligible is False


@pytest.mark.asyncio
async def test_profile_transform_rewires_dangling_refs(tmp_path, monkeypatch):
    """Migrated credentials are referenced, not merely copied."""
    from api_service.db.models import ManagedAgentProviderProfile, ManagedSecret
    from api_service.services import single_user_conversion as suc

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import UserProfile

        u = await _mk_user(session)
        session.add(UserProfile(user_id=u.id, openai_api_key_encrypted="tok-live"))
        session.add(
            _profile_row(
                "codex_openai_api",
                secret_refs={"openai_api_key": "env://OPENAI_API_KEY"},
            )
        )
        await session.commit()
        result = await suc.run_guarded_upgrade(session, operator_authorized=True)
        assert result.published is True
        transform = result.transforms["profile_secrets"]
        assert transform["rewired_profiles"] == 1
        assert transform["rewires"][0]["profile_id"] == "codex_openai_api"
        assert "tok-live" not in str(result.to_sanitized_dict())
        profile = await session.get(
            ManagedAgentProviderProfile, "codex_openai_api"
        )
        assert profile.secret_refs["openai_api_key"].startswith("db://")
        assert profile.credential_generation == 2
        assert len((await session.execute(select(ManagedSecret))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_profile_transform_keeps_working_env_refs(tmp_path, monkeypatch):
    """A resolvable env credential is never clobbered by a legacy copy."""
    from api_service.db.models import ManagedAgentProviderProfile
    from api_service.services import single_user_conversion as suc

    monkeypatch.setenv("OPENAI_API_KEY", "sk-working")
    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import UserProfile

        u = await _mk_user(session)
        session.add(UserProfile(user_id=u.id, openai_api_key_encrypted="tok-stale"))
        session.add(
            _profile_row(
                "codex_openai_api",
                secret_refs={"openai_api_key": "env://OPENAI_API_KEY"},
            )
        )
        await session.commit()
        result = await suc.run_guarded_upgrade(session, operator_authorized=True)
        assert result.published is True
        assert result.transforms["profile_secrets"]["rewired_profiles"] == 0
        profile = await session.get(
            ManagedAgentProviderProfile, "codex_openai_api"
        )
        assert profile.secret_refs == {"openai_api_key": "env://OPENAI_API_KEY"}
        assert profile.credential_generation == 1


@pytest.mark.asyncio
async def test_same_count_value_change_trips_staleness(tmp_path):
    """Owner sets/counts alone do not bind the source; values do too."""
    from api_service.services import single_user_conversion as suc

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import SettingsOverride

        u = await _mk_user(session)
        row = SettingsOverride(
            scope="user", user_id=u.id, key="theme", value_json={"v": 1}
        )
        session.add(row)
        await session.commit()
        bound = await suc.preflight(session)
        row.value_json = {"v": 2}
        await session.commit()
        with pytest.raises(suc.StaleAttributionError):
            await suc.apply_conversion(
                session, bound, operator_authorized=True, transforms={}
            )


@pytest.mark.asyncio
async def test_interrupted_apply_leaves_nothing_and_retry_publishes(tmp_path):
    """No in_progress row: interruption rolls back and the retry converges."""
    from api_service.db.models import ManagedSecret, SecretStatus
    from api_service.services import single_user_conversion as suc

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    calls = {"n": 0}

    async def flaky(session, eligible):
        calls["n"] += 1
        if calls["n"] == 1:
            session.add(
                ManagedSecret(
                    slug="partial",
                    ciphertext="x",
                    status=SecretStatus.ACTIVE,
                )
            )
            raise RuntimeError("simulated crash after progress")
        return {"outcome": "recovered"}

    async with factory() as session:
        with pytest.raises(RuntimeError, match="simulated crash"):
            await suc.run_guarded_upgrade(
                session,
                operator_authorized=True,
                transforms={"profile_secrets": flaky},
            )
        # Nothing was committed: no ledger row and no partial secret.
        assert (
            await session.execute(select(suc.SingleUserConversionRun))
        ).scalars().all() == []
        assert (await session.execute(select(ManagedSecret))).scalars().all() == []
        result = await suc.run_guarded_upgrade(
            session,
            operator_authorized=True,
            transforms={"profile_secrets": flaky},
        )
        assert result.published is True
        assert calls["n"] == 2
        rows = (
            await session.execute(select(suc.SingleUserConversionRun))
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "complete"


@pytest.mark.asyncio
async def test_stale_in_progress_fails_closed_without_claim(tmp_path):
    """Without the conversion claim, liveness is unprovable: never steal."""
    from api_service.services import single_user_conversion as suc

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        bound = await suc.preflight(session)
        session.add(
            suc.SingleUserConversionRun(
                preflight_digest=bound.digest,
                status="in_progress",
                result_json={},
            )
        )
        await session.commit()
        with pytest.raises(suc.ConcurrentConversionError):
            await suc.apply_conversion(
                session, bound, operator_authorized=True, transforms={}
            )
        row = (
            await session.execute(
                select(suc.SingleUserConversionRun).where(
                    suc.SingleUserConversionRun.preflight_digest == bound.digest
                )
            )
        ).scalars().one()
        assert row.status == "in_progress"


@pytest.mark.asyncio
async def test_replay_never_defaults_missing_completion_to_success(tmp_path):
    """A stored row without a completion record replays as unpublished."""
    from api_service.services import single_user_conversion as suc

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        bound = await suc.preflight(session)
        assert bound.decision.disposition == "fresh_init"
        session.add(
            suc.SingleUserConversionRun(
                preflight_digest=bound.digest,
                status="complete",
                result_json={},
            )
        )
        await session.commit()
        result = await suc.apply_conversion(
            session, bound, operator_authorized=True, transforms={}
        )
        assert result.published is False


@pytest.mark.asyncio
async def test_digest_binds_collision_resolution_values(tmp_path):
    """Different operator choices for one key must digest differently."""
    from api_service.services import single_user_conversion as suc

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        first = await suc.preflight(
            session,
            collision_resolutions={"theme": {"v": 1}},
            operator_authorized=True,
        )
        corrected = await suc.preflight(
            session,
            collision_resolutions={"theme": {"v": 2}},
            operator_authorized=True,
        )
        assert first.digest != corrected.digest
        repeated = await suc.preflight(
            session,
            collision_resolutions={"theme": {"v": 1}},
            operator_authorized=True,
        )
        assert repeated.digest == first.digest


@pytest.mark.asyncio
async def test_machine_owned_sources_are_not_principals(tmp_path):
    """owner_type-qualified surfaces ignore system/service owners."""
    from api_service.services.single_user_conversion import evaluate_disposition

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import WorkflowExecutionSourceMapping

        session.add(
            WorkflowExecutionSourceMapping(
                workflow_id="sys-wf",
                source="temporal",
                source_record_id="rec-1",
                owner_type="system",
                owner_id=str(uuid.uuid4()),
            )
        )
        session.add(
            WorkflowExecutionSourceMapping(
                workflow_id="svc-wf",
                source="temporal",
                source_record_id="rec-2",
                owner_type="service",
                owner_id=str(uuid.uuid4()),
            )
        )
        await session.commit()
        decision = await evaluate_disposition(session)
        # Machine-owned rows are neither people nor refusals: no unknown
        # principals, no multi-person block, conversion stays eligible.
        assert decision.eligible is True
        assert decision.disposition in {"fresh_init", "eligible_conversion"}


@pytest.mark.asyncio
async def test_stored_outcomes_scrub_sensitive_top_level_keys(tmp_path):
    """Ledger summaries keep metadata, never credential material."""
    from api_service.services import single_user_conversion as suc

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        bound = await suc.preflight(session)

        async def _leaky(s, eligible):
            return {
                "migrated": 1,
                "token": "raw-token-material",
                "items": [{"slug": "s", "secret_ref": "db://s"}],
            }

        result = await suc.apply_conversion(
            session, bound, operator_authorized=True, transforms={"t": _leaky}
        )
        assert result.published is True
        stored = result.transforms["t"]
        assert stored["migrated"] == 1
        assert "token" not in stored
        assert stored["items"] == [{"slug": "s"}]
        blob = str(result.to_sanitized_dict())
        assert "raw-token-material" not in blob
