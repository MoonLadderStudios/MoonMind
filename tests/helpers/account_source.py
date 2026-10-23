"""Frozen account-era fixtures for retained-data conversion tests only."""

from uuid import uuid4

from sqlalchemy import Boolean, Column, Integer, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy_utils import StringEncryptedType

from api_service.core.encryption import get_encryption_key


class AccountSourceBase(DeclarativeBase):
    pass


class User(AccountSourceBase):
    __tablename__ = "user"
    id = Column(Uuid, primary_key=True, default=uuid4)
    email = Column(String, nullable=False)
    hashed_password = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    oidc_provider = Column(String, nullable=True)
    oidc_subject = Column(String, nullable=True)


class UserProfile(AccountSourceBase):
    __tablename__ = "user_profile"
    id = Column(Integer, primary_key=True)
    user_id = Column(Uuid, nullable=False)
    google_api_key_encrypted = Column(
        StringEncryptedType(String, get_encryption_key), nullable=True
    )
    openai_api_key_encrypted = Column(
        StringEncryptedType(String, get_encryption_key), nullable=True
    )
    github_token_encrypted = Column(
        StringEncryptedType(String, get_encryption_key), nullable=True
    )
    anthropic_api_key_encrypted = Column(
        StringEncryptedType(String, get_encryption_key), nullable=True
    )
    agent_skill_repo_sources_enabled = Column(Boolean, default=True)
    agent_skill_local_sources_enabled = Column(Boolean, default=False)


def create_source_schema(connection, **kwargs):
    """Create historical inputs explicitly, without altering live metadata."""
    from api_service.db.models import Base

    AccountSourceBase.metadata.create_all(connection)
    Base.metadata.create_all(connection, **kwargs)


def drop_source_schema(connection, **kwargs):
    from api_service.db.models import Base

    Base.metadata.drop_all(connection, **kwargs)
    AccountSourceBase.metadata.drop_all(connection)
    # These are the same disposable fixture tables after guarded retirement.
    from sqlalchemy import MetaData, Table, inspect

    present = set(inspect(connection).get_table_names())
    for name in ("retired_user_profile", "retired_user"):
        if name in present:
            Table(name, MetaData(), autoload_with=connection).drop(connection)


async def add_source_user(session, user):
    """Perturb frozen source evidence, including an already archived source."""
    from sqlalchemy import MetaData, Table, inspect

    connection = await session.connection()
    present = await connection.run_sync(lambda c: inspect(c).get_table_names())
    if "user" in present:
        session.add(user)
        await session.flush()
        return
    table = await connection.run_sync(
        lambda c: Table("retired_user", MetaData(), autoload_with=c)
    )
    # Reflection sees SQLite UUID storage as CHAR; retain the fixture UUID binder.
    table.c.id.type = Uuid()
    await session.execute(
        table.insert().values(
            id=user.id,
            email=user.email,
            is_active=True,
            is_superuser=bool(user.is_superuser),
            is_verified=True,
        )
    )
