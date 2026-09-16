"""Real-database coverage for #4006: atomic, revision-fenced rotation.

Each test uses independent transactions on SQLite (the supported local
backend) via ``Base.metadata.create_all``. Acceptance mapping:

* ACC-01: rotation activates an immediately resolvable replacement.
* ACC-02: bad candidate / changed policy / concurrent rotation / txn failure
  leave the prior usable secret intact.
* ACC-03: lost acknowledgments and repeated request IDs advance once;
  conflicts do not mutate.
* ACC-04: fenced resolution; lost notifications cannot validate stale reads.
* ACC-05: update/import/connection paths share revision/audit/cache effects.
* ACC-06: shared references and attach/delete races are protected without
  cross-scope disclosure.
* ACC-07: restart recovery (outbox sweep), repaired historical ROTATED
  state, and secret-safe serialization.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedSecret,
    RepositoryConnectionRecord,
    SecretInvalidationOutbox,
    SecretStatus,
    SettingsAuditEvent,
    SettingsOverride,
)
from api_service.services.secrets import (
    SecretConflictError,
    SecretFencedError,
    SecretReferenceProtectedError,
    SecretRepairRequiredError,
    SecretsService,
    subscribe_secret_invalidations,
    unsubscribe_secret_invalidations,
)


async def _maker(tmp_path, name="issue4006.db"):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/{name}"
    engine = create_async_engine(db_url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return maker, engine


async def _revisions(db, slug):
    result = await db.execute(
        select(
            ManagedSecret.credential_revision,
            ManagedSecret.policy_revision,
            ManagedSecret.status,
        ).where(ManagedSecret.slug == slug)
    )
    row = result.one()
    return {"credential": row[0], "policy": row[1], "status": row[2]}


# ACC-01 --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_activates_replacement_at_new_revision(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        async with maker() as db:
            created = await SecretsService.create_secret(db, "github-pat-main", "old-pat")
            assert created.credential_revision == 1

        async with maker() as db:
            rotated = await SecretsService.rotate_secret(db, "github-pat-main", "new-pat")
            assert rotated is not None
            assert rotated.status == SecretStatus.ACTIVE
            assert rotated.credential_revision == 2

        async with maker() as db:
            assert await SecretsService.get_secret(db, "github-pat-main") == "new-pat"
            assert (
                await SecretsService.get_secret(
                    db, "github-pat-main", expected_revision=2
                )
                == "new-pat"
            )
            resolved = await SecretsService.get_secret_with_revision(db, "github-pat-main")
            assert resolved == {
                "value": "new-pat",
                "credential_revision": 2,
                "policy_revision": 1,
            }

        async with maker() as db:
            result = await db.execute(
                select(SettingsAuditEvent).where(
                    SettingsAuditEvent.key == "secrets.github-pat-main"
                )
            )
            events = result.scalars().all()
            assert {e.event_type for e in events} >= {"secrets.created", "secrets.rotated"}
            assert all(e.redacted for e in events)
            outbox = (
                await db.execute(
                    select(SecretInvalidationOutbox).where(
                        SecretInvalidationOutbox.slug == "github-pat-main"
                    )
                )
            ).scalars().all()
            assert len(outbox) == 1
            assert outbox[0].credential_revision == 2
            assert outbox[0].cause == "rotation"
    finally:
        await engine.dispose()


# ACC-02 --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bad_candidate_leaves_prior_intact(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        async with maker() as db:
            await SecretsService.create_secret(db, "deploy-token", "good")

        async with maker() as db:
            with pytest.raises(SecretFencedError):
                await SecretsService.rotate_secret(
                    db, "deploy-token", "evil", validator=lambda _c: False
                )

        async with maker() as db:
            assert await SecretsService.get_secret(db, "deploy-token") == "good"
            assert (await _revisions(db, "deploy-token"))["credential"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_changed_validation_policy_fences_activation(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        async with maker() as db:
            await SecretsService.create_secret(db, "api-key", "v1")

        async with maker() as db:
            envelope = await SecretsService.prepare_rotation_validation(
                db, "api-key", "v2", validator=lambda _c: True
            )
            assert envelope["expected_credential_revision"] == 1

        # A concurrent rotation advances authority before activation.
        async with maker() as db:
            await SecretsService.rotate_secret(db, "api-key", "v2-race")

        # The stale envelope must not activate or overwrite the winner.
        async with maker() as db:
            with pytest.raises(SecretFencedError):
                await SecretsService.rotate_secret(
                    db, "api-key", "v2", validation=envelope
                )

        async with maker() as db:
            assert await SecretsService.get_secret(db, "api-key") == "v2-race"
            assert (await _revisions(db, "api-key"))["credential"] == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_rotations_serialize_on_expected_revision(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        async with maker() as db:
            await SecretsService.create_secret(db, "race-key", "v1")

        async with maker() as db:
            first = await SecretsService.rotate_secret(
                db, "race-key", "v2", expected_credential_revision=1
            )
            assert first.credential_revision == 2

        async with maker() as db:
            with pytest.raises(SecretFencedError):
                await SecretsService.rotate_secret(
                    db, "race-key", "v3", expected_credential_revision=1
                )

        async with maker() as db:
            assert await SecretsService.get_secret(db, "race-key") == "v2"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_transaction_failure_leaves_prior_intact(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        async with maker() as db:
            await SecretsService.create_secret(db, "fragile", "stable")

        async with maker() as db:
            pending = await SecretsService.rotate_secret(
                db, "fragile", "tentative", commit=False
            )
            assert pending.credential_revision == 2
            await db.rollback()

        async with maker() as db:
            assert await SecretsService.get_secret(db, "fragile") == "stable"
            assert (await _revisions(db, "fragile"))["credential"] == 1
    finally:
        await engine.dispose()


# ACC-03 --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_retry_advances_once_and_conflict_rejected(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        async with maker() as db:
            await SecretsService.create_secret(db, "idem-key", "v1")

        async with maker() as db:
            first = await SecretsService.rotate_secret(
                db, "idem-key", "v2", request_id="req-123"
            )
            assert first.credential_revision == 2

        # Lost acknowledgment: retry reconciles without rotating again.
        async with maker() as db:
            second = await SecretsService.rotate_secret(
                db, "idem-key", "v2", request_id="req-123"
            )
            assert second.credential_revision == 2

        async with maker() as db:
            assert await SecretsService.get_secret(db, "idem-key") == "v2"
            assert (await _revisions(db, "idem-key"))["credential"] == 2

        # Conflicting reuse must not mutate the value.
        async with maker() as db:
            with pytest.raises(SecretConflictError):
                await SecretsService.rotate_secret(
                    db, "idem-key", "v3-evil", request_id="req-123"
                )

        async with maker() as db:
            assert await SecretsService.get_secret(db, "idem-key") == "v2"
    finally:
        await engine.dispose()


# ACC-04 --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fenced_resolution_and_lost_notification_recovery(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        async with maker() as db:
            await SecretsService.create_secret(db, "fenced", "v1")
            await SecretsService.rotate_secret(db, "fenced", "v2")

        async with maker() as db:
            # Stale expected revision never returns current material as old.
            assert (
                await SecretsService.get_secret(db, "fenced", expected_revision=1)
                is None
            )
            assert (
                await SecretsService.get_secret(db, "fenced", expected_revision=2)
                == "v2"
            )

        delivered: list[dict] = []

        async def _subscriber(event):
            delivered.append(event)

        # No subscriber was registered during rotation, so evidence is pending.
        async with maker() as db:
            pending = (
                await db.execute(
                    select(SecretInvalidationOutbox).where(
                        SecretInvalidationOutbox.delivered.is_(False)
                    )
                )
            ).scalars().all()
            assert len(pending) >= 1

        subscribe_secret_invalidations(_subscriber)
        try:
            async with maker() as db:
                swept = await SecretsService.sweep_invalidations(db)
                assert swept >= 1
        finally:
            unsubscribe_secret_invalidations(_subscriber)

        assert delivered and all(e["slug"] == "fenced" for e in delivered)
        async with maker() as db:
            remaining = (
                await db.execute(
                    select(SecretInvalidationOutbox).where(
                        SecretInvalidationOutbox.delivered.is_(False)
                    )
                )
            ).scalars().all()
            assert remaining == []
            # Authoritative read stays correct regardless of notification fate.
            assert await SecretsService.get_secret(db, "fenced") == "v2"
    finally:
        await engine.dispose()


# ACC-05 --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unified_mutation_paths_share_revision_audit_cache_effects(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        async with maker() as db:
            await SecretsService.create_secret(db, "K1", "a")
            await SecretsService.create_secret(db, "K2", "b")

        async with maker() as db:
            updated = await SecretsService.update_secret(db, "K1", "a2")
            assert updated.credential_revision == 2
            assert updated.policy_revision == 1

        async with maker() as db:
            changed = await SecretsService.set_status(
                db, "K1", SecretStatus.DISABLED, reason="cadence"
            )
            # Metadata-only: policy advances, credential does not.
            assert changed.credential_revision == 2
            assert changed.policy_revision == 2

        async with maker() as db:
            count = await SecretsService.import_from_env(
                db, {"K2": "b2"}, overwrite_active=True
            )
            assert count == 1
            assert await SecretsService.get_secret(db, "K2") == "b2"
            assert (await _revisions(db, "K2"))["credential"] == 2

        async with maker() as db:
            events = (
                await db.execute(select(SettingsAuditEvent))
            ).scalars().all()
            kinds = {e.event_type for e in events}
            assert {"secrets.updated", "secrets.status.changed", "secrets.imported"} <= kinds
            outbox = (
                await db.execute(select(SecretInvalidationOutbox))
            ).scalars().all()
            causes = {row.cause for row in outbox}
            assert {"update", "status", "import"} <= causes
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_caller_owned_transaction_commits_once(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        async with maker() as db:
            await SecretsService.create_secret(db, "combo", "v1", commit=False)
            await SecretsService.rotate_secret(db, "combo", "v2", commit=False)
            await db.commit()

        async with maker() as db:
            assert await SecretsService.get_secret(db, "combo") == "v2"
            assert (await _revisions(db, "combo"))["credential"] == 2
    finally:
        await engine.dispose()


# ACC-06 --------------------------------------------------------------------


async def _connection(maker, slug):
    async with maker() as db:
        db.add(
            RepositoryConnectionRecord(
                connection_id="conn-main",
                display_name="main",
                provider="git",
                hosting_service="github",
                endpoint_normalized="https://github.com",
                endpoint_ref="https://github.com",
                allowed_operations=["read"],
                client_policy={},
                credential_config={"pat": {"ref": f"db://{slug}"}},
                owner_ref="owner",
                scope_type="system",
                allowed_principal_refs=[],
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_delete_protected_by_connection_reference(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        async with maker() as db:
            await SecretsService.create_secret(db, "shared-pat", "token")
        await _connection(maker, "shared-pat")

        async with maker() as db:
            usage = await SecretsService.list_secret_usage(db, "shared-pat")
            assert any(
                u["consumerType"] == "repository_connection" for u in usage["usages"]
            )

        async with maker() as db:
            assert await SecretsService.delete_secret(db, "shared-pat") is False
            # Protected reference survives the refused delete.
            assert await SecretsService.get_secret(db, "shared-pat") == "token"

        async with maker() as db:
            with pytest.raises(SecretReferenceProtectedError) as excinfo:
                await SecretsService.delete_secret(db, "shared-pat", strict=True)
            # Bounded counts only: no consumer identities cross scopes.
            assert excinfo.value.counts["repository_connection"] == 1
            assert "conn-main" not in str(excinfo.value)

        # Detach, then deletion succeeds with a tombstone audit.
        async with maker() as db:
            result = await db.execute(
                select(RepositoryConnectionRecord).where(
                    RepositoryConnectionRecord.connection_id == "conn-main"
                )
            )
            record = result.scalar_one()
            record.credential_config = {}
            await db.commit()

        async with maker() as db:
            assert await SecretsService.delete_secret(db, "shared-pat") is True
            assert await SecretsService.get_secret(db, "shared-pat") is None
            tombstones = (
                await db.execute(
                    select(SettingsAuditEvent).where(
                        SettingsAuditEvent.event_type == "secrets.deleted"
                    )
                )
            ).scalars().all()
            assert len(tombstones) == 1
            assert tombstones[0].redacted is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_setting_override_reference_blocks_delete(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        async with maker() as db:
            await SecretsService.create_secret(db, "guarded", "v1")
            db.add(
                SettingsOverride(
                    scope="workspace",
                    key="integrations.github.token_ref",
                    value_json="db://guarded",
                )
            )
            await db.commit()

        async with maker() as db:
            assert await SecretsService.delete_secret(db, "guarded") is False
    finally:
        await engine.dispose()


# ACC-07 --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repair_path_for_historical_rotated_record(tmp_path):
    maker, engine = await _maker(tmp_path)
    try:
        async with maker() as db:
            # Simulate a pre-#4006 historical record stuck in ROTATED state.
            db.add(
                ManagedSecret(
                    slug="legacy",
                    ciphertext="legacy-value",
                    status=SecretStatus.ROTATED,
                    credential_revision=1,
                    policy_revision=1,
                    details={},
                )
            )
            await db.commit()

        async with maker() as db:
            assert await SecretsService.get_secret(db, "legacy") is None
            validation = await SecretsService.validate_secret_ref(db, "legacy")
            assert validation["diagnostics"][0]["code"] == "secret_repair_required"

        # Blind reactivation is refused on every mutation path.
        async with maker() as db:
            with pytest.raises(SecretRepairRequiredError):
                await SecretsService.rotate_secret(db, "legacy", "nope")
        async with maker() as db:
            with pytest.raises(SecretRepairRequiredError):
                await SecretsService.update_secret(db, "legacy", "nope")

        # Reviewed repair with a validated candidate restores resolvability.
        async with maker() as db:
            repaired = await SecretsService.repair_rotated_secret(
                db, "legacy", "fresh", validator=lambda _c: True
            )
            assert repaired.status == SecretStatus.ACTIVE
            assert repaired.credential_revision == 2

        async with maker() as db:
            assert await SecretsService.get_secret(db, "legacy") == "fresh"

        # A rejected repair candidate leaves history untouched.
        async with maker() as db:
            db.add(
                ManagedSecret(
                    slug="legacy2",
                    ciphertext="legacy-value",
                    status=SecretStatus.ROTATED,
                    credential_revision=1,
                    policy_revision=1,
                    details={},
                )
            )
            await db.commit()
        async with maker() as db:
            with pytest.raises(SecretFencedError):
                await SecretsService.repair_rotated_secret(
                    db, "legacy2", "bad", validator=lambda _c: False
                )
            assert await SecretsService.get_secret(db, "legacy2") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_audit_outbox_and_receipts_never_carry_secret_material(tmp_path):
    maker, engine = await _maker(tmp_path)
    marker = "zz-top-secret-marker-9f8"
    try:
        async with maker() as db:
            await SecretsService.create_secret(db, "quiet", marker)
            await SecretsService.rotate_secret(
                db, "quiet", marker + "-rot", request_id="req-quiet-1"
            )
            await SecretsService.set_status(
                db, "quiet", SecretStatus.DISABLED, request_id="req-quiet-2"
            )

        async with maker() as db:
            audits = (await db.execute(select(SettingsAuditEvent))).scalars().all()
            outbox = (await db.execute(select(SecretInvalidationOutbox))).scalars().all()
            from api_service.db.models import SecretMutationReceipt

            receipts = (await db.execute(select(SecretMutationReceipt))).scalars().all()
            for row in (*audits, *outbox, *receipts):
                blob = repr(
                    {
                        k: v
                        for k, v in vars(row).items()
                        if not k.startswith("_sa_")
                    }
                )
                assert marker not in blob
                assert marker + "-rot" not in blob
    finally:
        await engine.dispose()
