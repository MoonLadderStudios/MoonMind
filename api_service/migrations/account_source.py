"""Read-only account-era source tables for the guarded conversion.

These Core tables are deliberately outside live model metadata. They describe
retained storage, including encrypted fields, without a person resolver or an
account service. Successful conversion archives the source in the same ledger
transaction; subsequent conversions can still inspect its attribution evidence.
"""

from sqlalchemy import Column, Integer, MetaData, String, Table, Uuid, inspect, select
from sqlalchemy_utils import StringEncryptedType

from api_service.core.encryption import get_encryption_key

ACCOUNT_TABLES = (
    "user",
    "user_profile",
    "user_external_identities",
    "moonmind_sessions",
    "moonmind_user_session_generations",
    "moonmind_account_nonces",
    "moonmind_auth_migration_decision",
    "moonmind_oidc_transactions",
)


async def source_tables(session) -> dict[str, Table]:
    connection = await session.connection()
    present = set(
        await connection.run_sync(lambda conn: inspect(conn).get_table_names())
    )
    metadata = MetaData()
    tables = {}
    for name in ("user", "user_profile"):
        archived = f"retired_{name}"
        if name in present and archived in present:
            raise RuntimeError(f"conflicting retained account source: {name}")
        actual = name if name in present else archived if archived in present else None
        if actual is None:
            continue
        columns = (
            [Column("id", Uuid)]
            if name == "user"
            else [
                Column("id", Integer),
                Column("user_id", Uuid),
                *(
                    Column(
                        f"{field}_encrypted",
                        StringEncryptedType(String, get_encryption_key),
                    )
                    for field in (
                        "google_api_key",
                        "openai_api_key",
                        "github_token",
                        "anthropic_api_key",
                    )
                ),
            ]
        )
        tables[name] = Table(actual, metadata, *columns)
    return tables


async def source_user_ids(session) -> list:
    users = (await source_tables(session)).get("user")
    if users is None:
        return []
    return list((await session.execute(select(users.c.id))).scalars().all())


async def source_profiles(session) -> list:
    profiles = (await source_tables(session)).get("user_profile")
    if profiles is None:
        return []
    return list((await session.execute(select(profiles))).mappings().all())


async def archive_accounts(session, _eligible_ids) -> dict:
    """Preserve source bytes after transforms, without a runnable account schema."""
    from sqlalchemy import text

    connection = await session.connection()
    present = set(
        await connection.run_sync(lambda conn: inspect(conn).get_table_names())
    )
    for name in ACCOUNT_TABLES:
        if name in present and f"retired_{name}" in present:
            raise RuntimeError(f"conflicting retained account archive: {name}")
    if session.get_bind().dialect.name == "postgresql":
        names = ", ".join(f'"{name}"' for name in ACCOUNT_TABLES if name in present)
        if names:
            from sqlalchemy.exc import DBAPIError

            try:
                # The inventory's writer lock permits readers. Renaming needs
                # exclusive access: refuse promptly instead of trapping an old
                # streaming request behind the conversion transaction.
                await session.execute(
                    text(f"LOCK TABLE {names} IN ACCESS EXCLUSIVE MODE NOWAIT")
                )
            except DBAPIError as exc:
                code = getattr(exc.orig, "sqlstate", None) or getattr(
                    exc.orig, "pgcode", None
                )
                if code != "55P03":
                    raise
                from api_service.services.single_user_conversion import (
                    ConcurrentConversionError,
                )

                raise ConcurrentConversionError(
                    "Account retirement requires exclusive access; retry after retained readers finish"
                ) from exc
    archived = []
    for name in ACCOUNT_TABLES:
        if name not in present:
            continue
        # Names are fixed migration-owned identifiers. No row is deleted,
        # re-encrypted or reassigned, and PostgreSQL DDL rolls back on failure.
        await session.execute(text(f'ALTER TABLE "{name}" RENAME TO "retired_{name}"'))
        archived.append(name)
    return {"archived_tables": archived}
