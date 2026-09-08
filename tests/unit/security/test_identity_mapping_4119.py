"""K3 identity mapping and migration coverage (MoonLadderStudios/MoonMind#4119).

Hermetic SQLite tests for the production identity authority: one active
``(issuer, subject) -> User.id`` relation, transactional uniqueness-protected
binding, operator dry-run/apply with digest-bound preflight, safe reporting,
and rollback-compatible additive schema. Real PostgreSQL race evidence lives
in ``tests/integration/security/test_identity_migration_postgres_4119.py``;
these SQLite tests prove logic and dispositions, never the race itself.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service import auth as auth_module
from api_service.db.models import (
    IdentityMigrationRun,
    RecurringWorkflowDefinition,
    User,
    UserExternalIdentity,
    UserProfile,
)
from api_service.services.identity_migration import (
    ConcurrentApplyError,
    MigrationAuthorizationError,
    StalePreflightError,
    apply_migration,
    database_fingerprint,
    preflight,
)
from api_service.services.identity_service import (
    ControlledEnrollmentRequiredError,
    IdentityValidationError,
    collect_identity_report,
    credential_enrollment_required,
    get_or_create_user_for_identity,
    ownership_snapshot,
    resolve_user_id_for_identity,
    validate_external_identity,
)

ISSUER_A = "https://idp-a.example.invalid/realms/moonmind"
ISSUER_B = "https://idp-b.example.invalid/realms/moonmind"
LONG_ISSUER = "https://idp.example.invalid/" + "r" * 2000

_TABLES = (
    User.__table__,
    UserExternalIdentity.__table__,
    UserProfile.__table__,
    IdentityMigrationRun.__table__,
    RecurringWorkflowDefinition.__table__,
)


async def _db(tmp_path, name="k3.db"):
    url = f"sqlite+aiosqlite:///{tmp_path}/{name}"
    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        for table in _TABLES:
            await conn.run_sync(table.create, checkfirst=True)
    return engine, maker


async def _make_user(session: AsyncSession, **overrides: Any) -> User:
    params: dict[str, Any] = {
        "id": uuid.uuid4(),
        "email": f"user-{uuid.uuid4().hex}@example.invalid",
        "hashed_password": None,
        "is_active": True,
        "is_superuser": False,
        "is_verified": False,
    }
    params.update(overrides)
    user = User(**params)
    session.add(user)
    await session.flush()
    return user


async def _count(session: AsyncSession, table) -> int:
    return (await session.execute(select(func.count()).select_from(table))).scalar() or 0


# ---------------------------------------------------------------------------
# R2: smallest adequate schema extension, one active mapping
# ---------------------------------------------------------------------------


def test_identity_table_stores_full_issuer_without_truncation():
    issuer_col = UserExternalIdentity.__table__.c["issuer"]
    subject_col = UserExternalIdentity.__table__.c["subject"]
    assert isinstance(issuer_col.type, type(subject_col.type))
    assert getattr(issuer_col.type, "length", None) is None
    assert getattr(subject_col.type, "length", None) is None
    constraints = {getattr(c, "name", "") for c in UserExternalIdentity.__table__.constraints}
    assert "uq_user_external_identity" in constraints


def test_legacy_columns_retained_as_read_only_evidence():
    table = User.__table__
    assert table.c["oidc_provider"].type.length == 32
    assert table.c["oidc_subject"].type.length == 255
    assert any(getattr(c, "name", "") == "uq_oidc_identity" for c in table.constraints)


def test_validation_never_truncates_and_rejects_reserved():
    issuer, subject = validate_external_identity(ISSUER_A, "UserA-123")
    assert (issuer, subject) == (ISSUER_A, "UserA-123")  # exact case preserved
    with pytest.raises(IdentityValidationError):
        validate_external_identity("", "sub")
    with pytest.raises(IdentityValidationError):
        validate_external_identity(ISSUER_A, "")
    with pytest.raises(IdentityValidationError):
        validate_external_identity(ISSUER_A, "local")
    with pytest.raises(IdentityValidationError):
        validate_external_identity(ISSUER_A, "__public__")
    with pytest.raises(IdentityValidationError):
        validate_external_identity("x" * 2049, "sub")


@pytest.mark.asyncio
async def test_legacy_only_row_does_not_resolve(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        user = await _make_user(
            session, oidc_provider="keycloak", oidc_subject="legacy-sub-1"
        )
        await session.commit()
        assert await resolve_user_id_for_identity(session, ISSUER_A, "legacy-sub-1") is None
        assert user.oidc_provider == "keycloak"  # legacy evidence preserved


@pytest.mark.asyncio
async def test_same_subject_across_issuers_resolves_distinct_users(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        alice_a, created_a = await get_or_create_user_for_identity(
            session, ISSUER_A, "shared-sub", email="a@example.invalid"
        )
        alice_b, created_b = await get_or_create_user_for_identity(
            session, ISSUER_B, "shared-sub", email="b@example.invalid"
        )
        assert created_a and created_b
        assert alice_a.id != alice_b.id
        assert await resolve_user_id_for_identity(session, ISSUER_A, "shared-sub") == alice_a.id
        assert await resolve_user_id_for_identity(session, ISSUER_B, "shared-sub") == alice_b.id
        await session.commit()


@pytest.mark.asyncio
async def test_subject_matching_is_case_sensitive(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        await get_or_create_user_for_identity(session, ISSUER_A, "usera")
        await session.commit()
        assert await resolve_user_id_for_identity(session, ISSUER_A, "UserA") is None


# ---------------------------------------------------------------------------
# R3: transactional binding, no email merge, renames keep UUID/flags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_login_rename_keeps_uuid_and_flags(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        user, _ = await get_or_create_user_for_identity(
            session, ISSUER_A, "stable-sub", email="old@example.invalid"
        )
        user.is_active = False
        user.is_superuser = True
        await session.flush()
        same, created = await get_or_create_user_for_identity(
            session, ISSUER_A, "stable-sub", email="new@example.invalid"
        )
        assert not created
        assert same.id == user.id
        assert same.email == "new@example.invalid"
        assert same.is_active is False  # local flags untouched by rename
        assert same.is_superuser is True
        await session.commit()


@pytest.mark.asyncio
async def test_same_email_across_issuers_refuses_merge(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        first, _ = await get_or_create_user_for_identity(
            session, ISSUER_A, "sub-1", email="shared@example.invalid"
        )
        await session.commit()
        with pytest.raises(ControlledEnrollmentRequiredError):
            await get_or_create_user_for_identity(
                session, ISSUER_B, "sub-2", email="shared@example.invalid"
            )
        await session.rollback()
        assert await resolve_user_id_for_identity(session, ISSUER_B, "sub-2") is None
        assert (await session.get(User, first.id)) is not None


@pytest.mark.asyncio
async def test_recycled_email_never_transfers_ownership(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        owner, _ = await get_or_create_user_for_identity(
            session, ISSUER_A, "owner-sub", email="recycled@example.invalid"
        )
        await session.commit()
        with pytest.raises(ControlledEnrollmentRequiredError):
            await get_or_create_user_for_identity(
                session, ISSUER_A, "new-sub", email="recycled@example.invalid"
            )
        await session.rollback()
        assert (await session.get(User, owner.id)) is not None


@pytest.mark.asyncio
async def test_concurrent_first_login_creates_one_mapping_and_profile(tmp_path):
    from sqlalchemy.exc import OperationalError

    from api_service.services.identity_service import IdentityConflictError

    engine, maker = await _db(tmp_path)
    barrier = asyncio.Barrier(8)

    async def first_login(n: int) -> UUID | None:
        for attempt in range(6):
            async with maker() as session:
                try:
                    await barrier.wait()
                    user, _ = await get_or_create_user_for_identity(
                        session, ISSUER_A, "race-sub", email=f"race-{n}@example.invalid"
                    )
                    uid = user.id
                    await session.commit()
                    return uid
                except (IdentityConflictError, OperationalError):
                    await session.rollback()
                    await asyncio.sleep(0.01 * (attempt + 1))
                except ControlledEnrollmentRequiredError:
                    # A loser that collided on email converges on the winner.
                    await session.rollback()
                    winner = await resolve_user_id_for_identity(
                        session, ISSUER_A, "race-sub"
                    )
                    if winner is not None:
                        return winner
                    await asyncio.sleep(0.01 * (attempt + 1))
                except Exception:
                    await session.rollback()
                    raise
        async with maker() as session:
            return await resolve_user_id_for_identity(session, ISSUER_A, "race-sub")

    winners = await asyncio.gather(*[first_login(n) for n in range(8)])
    assert all(w is not None for w in winners)
    async with maker() as session:
        rows = (await session.execute(select(UserExternalIdentity))).scalars().all()
        assert len(rows) == 1
        owner = rows[0].user_id
        assert set(winners) == {owner}
        profiles = (
            (await session.execute(select(UserProfile).where(UserProfile.user_id == owner)))
            .scalars()
            .all()
        )
        assert len(profiles) == 1
        accounts = (
            (await session.execute(select(User).where(User.email.like("race-%"))))
            .scalars()
            .all()
        )
        # Loser user rows are discarded, never stranded as unmapped duplicates.
        assert [u.id for u in accounts] == [owner]
        assert accounts[0].is_superuser is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_new_users_are_never_promoted(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        user, _ = await get_or_create_user_for_identity(session, ISSUER_A, "plain-sub")
        assert user.is_superuser is False
        assert user.is_active is True


# ---------------------------------------------------------------------------
# R5/R6: controlled enrollment, default-UUID mismatch, operator claim
# ---------------------------------------------------------------------------


def test_unported_credentials_require_enrollment():
    assert credential_enrollment_required(None) is True
    assert credential_enrollment_required("") is True
    assert credential_enrollment_required("$2b$12$legacy-bcrypt-hash") is True
    assert credential_enrollment_required("$argon2id$v=19$m=65536,ok") is False


@pytest.mark.asyncio
async def test_default_uuid_mismatch_fails_closed(tmp_path, monkeypatch):
    engine, maker = await _db(tmp_path, "default.db")
    monkeypatch.setattr(auth_module.settings.oidc, "DEFAULT_USER_ID", str(uuid.uuid4()))
    monkeypatch.setattr(
        auth_module.settings.oidc, "DEFAULT_USER_EMAIL", "default@example.invalid"
    )
    monkeypatch.setattr(auth_module.settings.oidc, "DEFAULT_USER_PASSWORD", "x")

    class _FakeManager:
        async def get(self, _uid):
            raise Exception("not found")

        async def get_by_email(self, _email):
            class _Row:
                id = uuid.uuid4()  # different UUID owns the default email

            return _Row()

    async with maker() as session:
        with pytest.raises(ControlledEnrollmentRequiredError) as exc:
            await auth_module.get_or_create_default_user(session, _FakeManager())
        assert exc.value.code == "default_id_mismatch"
    await engine.dispose()


@pytest.mark.asyncio
async def test_default_admin_claim_requires_operator_authorization():
    with pytest.raises(PermissionError):
        await auth_module.claim_default_admin(None, None, operator_authorized=False)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# R4: dry-run writes nothing; apply/rerun/stale/concurrent are safe
# ---------------------------------------------------------------------------


async def _seed_mixed_dataset(session: AsyncSession) -> dict[str, UUID]:
    fresh = await _make_user(session, email="fresh@example.invalid")
    legacy = await _make_user(
        session,
        email="legacy@example.invalid",
        oidc_provider="keycloak",
        oidc_subject="legacy-sub",
    )
    local_default = await _make_user(
        session,
        id=UUID("00000000-0000-0000-0000-000000000000"),
        email="default@example.com",
    )
    admin = await _make_user(
        session, email="admin@example.invalid", is_superuser=True
    )
    for user in (fresh, legacy, local_default, admin):
        session.add(UserProfile(user_id=user.id))
    sched = RecurringWorkflowDefinition(
        name="owner-schedule",
        cron="0 * * * *",
        timezone="UTC",
        target={},
        policy={},
        owner_user_id=legacy.id,
    )
    session.add(sched)
    await session.flush()
    return {"fresh": fresh.id, "legacy": legacy.id, "default": local_default.id, "admin": admin.id}


def _enrollment(ids: dict[str, UUID]) -> dict[str, Any]:
    return {
        str(ids["legacy"]): {
            "issuer": ISSUER_A,
            "subject": "legacy-sub",
            "source_provider": "keycloak",
        },
        str(ids["fresh"]): {
            "issuer": ISSUER_A,
            "subject": "fresh-sub",
            "source_provider": "keycloak",
        },
    }


@pytest.mark.asyncio
async def test_dry_run_performs_no_writes(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        ids = await _seed_mixed_dataset(session)
        await session.commit()
        before_fp = await database_fingerprint(session)
        before_counts = {
            "users": await _count(session, User.__table__),
            "identities": await _count(session, UserExternalIdentity.__table__),
            "runs": await _count(session, IdentityMigrationRun.__table__),
        }
        result = await preflight(
            session,
            provider_to_issuer={"keycloak": ISSUER_A},
            enrollment_evidence=_enrollment(ids),
            default_email="default@example.com",
        )
        assert result.digest
        assert len(result.rows) == 2
        assert await database_fingerprint(session) == before_fp
        assert await _count(session, User.__table__) == before_counts["users"]
        assert await _count(session, UserExternalIdentity.__table__) == before_counts["identities"]
        assert await _count(session, IdentityMigrationRun.__table__) == before_counts["runs"]


@pytest.mark.asyncio
async def test_apply_rerun_and_resume_converge(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        ids = await _seed_mixed_dataset(session)
        await session.commit()
        before = await ownership_snapshot(session)

        first = await preflight(
            session,
            provider_to_issuer={"keycloak": ISSUER_A},
            enrollment_evidence=_enrollment(ids),
        )
        outcome = await apply_migration(
            session,
            first,
            operator_authorized=True,
            provider_to_issuer={"keycloak": ISSUER_A},
            enrollment_evidence=_enrollment(ids),
        )
        assert sorted(outcome.migrated) == sorted([str(ids["legacy"]), str(ids["fresh"])])
        assert outcome.blocked == []

        # Rerun with a fresh preflight over the migrated DB: every row skips.
        second = await preflight(
            session,
            provider_to_issuer={"keycloak": ISSUER_A},
            enrollment_evidence=_enrollment(ids),
        )
        rerun = await apply_migration(
            session,
            second,
            operator_authorized=True,
            provider_to_issuer={"keycloak": ISSUER_A},
            enrollment_evidence=_enrollment(ids),
        )
        assert rerun.migrated == []
        assert sorted(rerun.skipped) == sorted([str(ids["legacy"]), str(ids["fresh"])])

        # Empty-enrollment apply replays its recorded result.
        empty = await preflight(session, provider_to_issuer={})
        once = await apply_migration(
            session, empty, operator_authorized=True, provider_to_issuer={}
        )
        twice = await apply_migration(
            session, empty, operator_authorized=True, provider_to_issuer={}
        )
        assert once.to_sanitized_dict() == twice.to_sanitized_dict()

        after = await ownership_snapshot(session)
        assert after["user_ids"] == before["user_ids"]
        assert after["fk_owner_counts"] == before["fk_owner_counts"]


@pytest.mark.asyncio
async def test_apply_with_partial_blockers_preserves_unrelated_owners(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        ids = await _seed_mixed_dataset(session)
        await session.commit()
        evidence = _enrollment(ids)
        evidence[str(uuid.uuid4())] = {  # missing user: blocked, not fatal
            "issuer": ISSUER_A,
            "subject": "ghost-sub",
            "source_provider": "keycloak",
        }
        criticized = await preflight(
            session,
            provider_to_issuer={"keycloak": ISSUER_A},
            enrollment_evidence=evidence,
        )
        outcome = await apply_migration(
            session,
            criticized,
            operator_authorized=True,
            provider_to_issuer={"keycloak": ISSUER_A},
            enrollment_evidence=evidence,
        )
        assert len(outcome.migrated) == 2
        assert len(outcome.blocked) == 1
        assert outcome.blocked[0]["code"] == "unresolved_ownership"
        assert await resolve_user_id_for_identity(session, ISSUER_A, "legacy-sub") == ids["legacy"]
        assert await resolve_user_id_for_identity(session, ISSUER_A, "ghost-sub") is None


@pytest.mark.asyncio
async def test_stale_preflight_refuses_to_apply(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        ids = await _seed_mixed_dataset(session)
        await session.commit()
        stale = await preflight(
            session,
            provider_to_issuer={"keycloak": ISSUER_A},
            enrollment_evidence=_enrollment(ids),
        )
        # Concurrent identity edit after the reviewed dry-run.
        intruder = await _make_user(session, email="intruder@example.invalid")
        session.add(
            UserExternalIdentity(
                user_id=intruder.id, issuer=ISSUER_B, subject="intruder-sub"
            )
        )
        await session.commit()
        with pytest.raises(StalePreflightError):
            await apply_migration(
                session,
                stale,
                operator_authorized=True,
                provider_to_issuer={"keycloak": ISSUER_A},
                enrollment_evidence=_enrollment(ids),
            )
        # Stale mapping input is equally rejected.
        with pytest.raises(StalePreflightError):
            await apply_migration(
                session,
                stale,
                operator_authorized=True,
                provider_to_issuer={"keycloak": LONG_ISSUER},
                enrollment_evidence=_enrollment(ids),
            )


@pytest.mark.asyncio
async def test_concurrent_apply_fails_closed_and_completed_replays(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        ids = await _seed_mixed_dataset(session)
        await session.commit()
        digest_holder = await preflight(
            session,
            provider_to_issuer={"keycloak": ISSUER_A},
            enrollment_evidence=_enrollment(ids),
        )
        session.add(
            IdentityMigrationRun(
                preflight_digest=digest_holder.digest, status="in_progress"
            )
        )
        await session.commit()
        with pytest.raises(ConcurrentApplyError):
            await apply_migration(
                session,
                digest_holder,
                operator_authorized=True,
                provider_to_issuer={"keycloak": ISSUER_A},
                enrollment_evidence=_enrollment(ids),
            )


@pytest.mark.asyncio
async def test_apply_requires_operator_authorization(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        ids = await _seed_mixed_dataset(session)
        await session.commit()
        checked = await preflight(
            session,
            provider_to_issuer={"keycloak": ISSUER_A},
            enrollment_evidence=_enrollment(ids),
        )
        with pytest.raises(MigrationAuthorizationError):
            await apply_migration(
                session,
                checked,
                operator_authorized=False,
                provider_to_issuer={"keycloak": ISSUER_A},
                enrollment_evidence=_enrollment(ids),
            )


# ---------------------------------------------------------------------------
# R5/R7: safe reporting, ownership reconciliation, rollback compatibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_flags_ambiguity_without_secrets(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        first = await _make_user(
            session, email="dup@example.invalid", hashed_password="$2b$12$legacy"
        )
        second = await _make_user(session, email="DUP@example.invalid")
        legacy = await _make_user(
            session, oidc_provider="keycloak", oidc_subject="orphan-legacy"
        )
        orphan = UserProfile(user_id=uuid.uuid4())
        session.add(orphan)
        mismatch = await _make_user(session, email="default@example.com")
        await session.commit()

        report = await collect_identity_report(
            session,
            provider_to_issuer={"keycloak": ""},
            default_email="default@example.com",
        )
        codes = {issue.code for issue in report.issues}
        assert {"duplicate_email", "orphan_profile", "missing_mapping",
                "default_id_mismatch", "unresolved_mapping"} <= codes
        assert report.has_blockers() is True
        assert report.unmapped_users >= 4
        payload = json.dumps(report.to_sanitized_dict())
        assert "$2b$12$legacy" not in payload
        assert "orphan-legacy" not in payload  # raw subjects never exported
        assert "token" not in payload.lower()
        # Ambiguity blocks migration but preserves every row untouched.
        assert (await session.get(User, first.id)) is not None
        assert (await session.get(User, second.id)) is not None
        assert (await session.get(User, legacy.id)) is not None
        assert (await session.get(User, mismatch.id)) is not None


@pytest.mark.asyncio
async def test_migration_preserves_hashes_uuids_and_fk_owners(tmp_path):
    _, maker = await _db(tmp_path)
    async with maker() as session:
        ids = await _seed_mixed_dataset(session)
        legacy_before = await session.get(User, ids["legacy"])
        assert legacy_before is not None
        legacy_before.hashed_password = "$argon2id$v=19$kept-secret"
        await session.commit()

        checked = await preflight(
            session,
            provider_to_issuer={"keycloak": ISSUER_A},
            enrollment_evidence=_enrollment(ids),
        )
        await apply_migration(
            session,
            checked,
            operator_authorized=True,
            provider_to_issuer={"keycloak": ISSUER_A},
            enrollment_evidence=_enrollment(ids),
        )
        # Old-app read path still works: legacy columns and owners intact.
        legacy_after = await session.get(User, ids["legacy"])
        assert legacy_after is not None
        assert legacy_after.id == ids["legacy"]
        assert legacy_after.oidc_provider == "keycloak"
        assert legacy_after.hashed_password == "$argon2id$v=19$kept-secret"
        schedules = (
            (await session.execute(select(RecurringWorkflowDefinition))).scalars().all()
        )
        assert len(schedules) == 1
        assert schedules[0].owner_user_id == ids["legacy"]


def test_downgrade_only_drops_new_tables():
    from pathlib import Path

    source = Path("api_service/migrations/versions/374_identity_mapping_k3.py").read_text(
        encoding="utf-8"
    )
    assert "drop_table" in source
    assert 'drop_table("user")' not in source
    assert "user_external_identities" in source
    assert "identity_migration_runs" in source
    assert "374_identity_mapping_k3" in source
    assert '"373_lease_identity_text"' in source
