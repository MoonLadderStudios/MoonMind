"""Issue #4346: upgrade eligibility + guarded conversion entrypoint.

Covers the four dispositions (fresh init, eligible conversion, known
multi-person refusal, unresolved-evidence refusal), alias-evidence rules,
consistent-read/staleness guard, refusal non-mutation, subsystem coverage,
idempotent rerun, and redacted reporting.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base


def _factory(tmp_path, name="conv4346.db"):
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


@pytest.mark.asyncio
async def test_fresh_empty_database_initializes(tmp_path):
    from api_service.services.single_user_conversion import evaluate_disposition

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        decision = await evaluate_disposition(session)
        assert decision.disposition == "fresh_init"
        assert decision.eligible is True


@pytest.mark.asyncio
async def test_single_operator_is_eligible(tmp_path):
    from api_service.services.single_user_conversion import evaluate_disposition

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import UserProfile

        u = await _mk_user(session)
        session.add(UserProfile(user_id=u.id))
        await session.commit()
        decision = await evaluate_disposition(session)
        assert decision.disposition == "eligible_conversion"
        assert decision.eligible is True


@pytest.mark.asyncio
async def test_proven_aliases_convert_but_matching_email_does_not(tmp_path):
    from api_service.services.single_user_conversion import evaluate_disposition

    engine, factory = _factory(tmp_path, "alias.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import UserProfile

        u1 = await _mk_user(session, email="Same.Person@example.com")
        u2 = await _mk_user(session, email="same.person@EXAMPLE.com")
        session.add(UserProfile(user_id=u1.id))
        session.add(UserProfile(user_id=u2.id))
        await session.commit()
        # Matching email alone is insufficient evidence: still multi-person.
        refused = await evaluate_disposition(session)
        assert refused.disposition == "multi_person_refusal"
        assert refused.eligible is False
        # Durable trustworthy alias evidence groups them as one operator.
        ok = await evaluate_disposition(
            session, alias_groups=[{str(u1.id), str(u2.id)}]
        )
        assert ok.disposition == "eligible_conversion"
        assert ok.eligible is True


@pytest.mark.asyncio
async def test_admin_flags_and_single_active_login_are_insufficient(tmp_path):
    from api_service.services.single_user_conversion import evaluate_disposition

    engine, factory = _factory(tmp_path, "flags.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import UserProfile

        admin = await _mk_user(session, is_superuser=True, is_active=False)
        plain = await _mk_user(session, is_superuser=False, is_active=True)
        session.add(UserProfile(user_id=admin.id))
        session.add(UserProfile(user_id=plain.id))
        await session.commit()
        decision = await evaluate_disposition(session)
        assert decision.disposition == "multi_person_refusal"


@pytest.mark.asyncio
async def test_serialized_and_inflight_refs_attribute_owners(tmp_path):
    from api_service.services.single_user_conversion import evaluate_disposition

    engine, factory = _factory(tmp_path, "ser.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import (
            TemporalArtifact,
            TemporalExecutionOwnerType,
            TemporalExecutionRecord,
            TemporalWorkflowType,
            UserProfile,
        )
        from moonmind.statuses.workflow import MoonMindWorkflowState

        u1 = await _mk_user(session)
        u2 = await _mk_user(session)
        session.add(UserProfile(user_id=u1.id))
        await session.flush()
        session.add(
            TemporalExecutionRecord(
                workflow_id="wf-1",
                run_id="run-1",
                entry="test-entry",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id=str(u1.id),
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.EXECUTING,
            )
        )
        session.add(
            TemporalArtifact(
                artifact_id="a-1",
                storage_key="k1",
                created_by_principal=str(u2.id),
            )
        )
        await session.commit()
        decision = await evaluate_disposition(session)
        # Two distinct principals across SQL + serialized/in-flight refs.
        assert decision.disposition == "multi_person_refusal"


@pytest.mark.asyncio
async def test_missing_owner_and_unowned_rows_are_unresolved(tmp_path):
    from api_service.services.single_user_conversion import evaluate_disposition

    engine, factory = _factory(tmp_path, "unowned.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import (
            TemporalArtifact,
            UserProfile,
        )

        u = await _mk_user(session)
        session.add(UserProfile(user_id=u.id))
        # Serialized ref to a deleted/unknown user.
        session.add(
            TemporalArtifact(
                artifact_id="a-x",
                storage_key="kx",
                created_by_principal=str(uuid.uuid4()),
            )
        )
        await session.commit()
        decision = await evaluate_disposition(session)
        assert decision.disposition == "unresolved_refusal"


@pytest.mark.asyncio
async def test_concurrent_mutation_invalidates_preflight(tmp_path):
    from api_service.services import single_user_conversion as suc

    engine, factory = _factory(tmp_path, "stale.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import UserProfile

        u = await _mk_user(session)
        session.add(UserProfile(user_id=u.id))
        await session.commit()
        pre = await suc.preflight(session)
        assert pre.decision.eligible is True
        # Intervening ownership change after preflight.
        u2 = await _mk_user(session)
        session.add(UserProfile(user_id=u2.id))
        await session.commit()
        with pytest.raises(suc.StaleAttributionError):
            await suc.apply_conversion(
                session,
                pre,
                operator_authorized=True,
                transforms={"__test__": lambda s, e: {}},
            )


@pytest.mark.asyncio
async def test_refusal_performs_no_source_mutation(tmp_path):
    from api_service.services import single_user_conversion as suc

    engine, factory = _factory(tmp_path, "refuse.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from sqlalchemy import select

        from api_service.db.models import ManagedSecret, User, UserProfile

        u1 = await _mk_user(session)
        u2 = await _mk_user(session)
        session.add(UserProfile(user_id=u1.id, openai_api_key_encrypted="tok-a"))
        session.add(UserProfile(user_id=u2.id, openai_api_key_encrypted="tok-b"))
        await session.commit()
        users_before = len((await session.execute(select(User))).scalars().all())
        pre = await suc.preflight(session)
        assert pre.decision.eligible is False
        result = await suc.apply_conversion(
            session,
            pre,
            operator_authorized=True,
            transforms={"__test__": lambda s, e: {}},
        )
        assert result.published is False
        assert (await session.execute(select(User))).scalars().all() is not None
        users_after = len((await session.execute(select(User))).scalars().all())
        assert users_after == users_before
        assert (await session.execute(select(ManagedSecret))).scalars().all() == []


@pytest.mark.asyncio
async def test_eligible_apply_runs_transforms_and_reruns_idempotently(tmp_path):
    from api_service.services import single_user_conversion as suc

    engine, factory = _factory(tmp_path, "apply.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import UserProfile

        u = await _mk_user(session)
        session.add(UserProfile(user_id=u.id))
        await session.commit()
        calls: list = []

        async def _t(s, eligible):
            calls.append(set(eligible))
            return {"ok": True}

        pre = await suc.preflight(session)
        first = await suc.apply_conversion(
            session, pre, operator_authorized=True, transforms={"t": _t}
        )
        assert first.published is True
        assert len(calls) == 1
        pre2 = await suc.preflight(session)
        second = await suc.apply_conversion(
            session, pre2, operator_authorized=True, transforms={"t": _t}
        )
        assert second.published is True
        # Idempotent rerun replays the recorded result without duplicating work.
        assert len(calls) == 1
        assert second.digest == first.digest


@pytest.mark.asyncio
async def test_missing_transform_coverage_refuses(tmp_path):
    from api_service.services import single_user_conversion as suc

    engine, factory = _factory(tmp_path, "cov.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import UserProfile

        u = await _mk_user(session)
        session.add(
            UserProfile(user_id=u.id, openai_api_key_encrypted="tok-secret")
        )
        await session.commit()
        pre = await suc.preflight(session)
        # Profile secrets present but no profile_secrets transform registered.
        result = await suc.apply_conversion(
            session, pre, operator_authorized=True, transforms={}
        )
        assert result.published is False
        assert result.decision.reason_code == "missing_transform_coverage"


@pytest.mark.asyncio
async def test_reports_contain_no_credentials(tmp_path):
    from api_service.services import single_user_conversion as suc

    engine, factory = _factory(tmp_path, "redact.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        from api_service.db.models import UserProfile

        u = await _mk_user(session, email="secret-person@example.com")
        session.add(
            UserProfile(user_id=u.id, openai_api_key_encrypted="super-secret-value")
        )
        await session.commit()
        pre = await suc.preflight(session)
        blob = str(pre.decision.to_sanitized_dict())
        assert "super-secret-value" not in blob
        assert "secret-person@example.com" not in blob
