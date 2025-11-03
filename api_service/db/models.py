from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import (  # Added Uuid, String, UniqueConstraint
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy_utils import EncryptedType  # Added EncryptedType

from api_service.core.encryption import (  # Added import for get_encryption_key
    get_encryption_key,
)


class Base(DeclarativeBase):
    pass


# Note: fastapi-users[sqlalchemy] uses GUID/UUID by default for id.
# If you need an Integer ID, you would use SQLAlchemyBaseUserTable[int]
# and ensure your UserManager and FastAPIUsers instances are typed accordingly.
# For this implementation, we'll stick to UUIDs as it's more common with fastapi-users.
class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "user"
    # id is inherited from SQLAlchemyBaseUserTableUUID and is a UUID type
    # email is inherited
    # hashed_password is inherited
    # is_active is inherited
    # is_superuser is inherited
    # is_verified is inherited

    hashed_password = Column(Text, nullable=True)  # Made nullable
    oidc_provider = Column(String(32), index=True, nullable=True)
    oidc_subject = Column(String(255), index=True, nullable=True)

    __table_args__ = (
        UniqueConstraint("oidc_provider", "oidc_subject", name="uq_oidc_identity"),
    )
    user_profile = relationship(
        "UserProfile", back_populates="user", uselist=False
    )  # Added relationship to UserProfile


class UserProfile(Base):
    __tablename__ = "user_profile"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Uuid, ForeignKey("user.id"), unique=True, nullable=False
    )  # Changed to Uuid

    # Example profile field
    google_api_key_encrypted = Column(
        EncryptedType(Text, get_encryption_key), nullable=True
    )
    openai_api_key_encrypted = Column(
        EncryptedType(Text, get_encryption_key), nullable=True
    )
    github_token_encrypted = Column(
        EncryptedType(Text, get_encryption_key), nullable=True
    )
    # Add other provider keys here as needed

    user = relationship("User", back_populates="user_profile")


class ManifestRecord(Base):
    __tablename__ = "manifest"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    last_indexed_at = Column(DateTime(timezone=True), nullable=True)


__all__ = [
    "Base",
    "User",
    "UserProfile",
    "ManifestRecord",
]


from moonmind.workflows.speckit_celery import (  # noqa: E402  # isort: skip
    models as workflow_models,
)

__all__.extend(workflow_models.__all__)
