
from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy import Column, ForeignKey, Integer, Text, Uuid  # Added Uuid
from sqlalchemy_utils import EncryptedType  # Added EncryptedType

from api_service.core.encryption import (
    get_encryption_key,
)  # Added import for get_encryption_key


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

    # You can add custom fields here if needed, for example:
    # first_name = Column(String(length=50), nullable=True)
    # last_name = Column(String(length=50), nullable=True)

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
    google_api_key_encrypted = Column(EncryptedType(Text, get_encryption_key), nullable=True)
    openai_api_key_encrypted = Column(EncryptedType(Text, get_encryption_key), nullable=True)
    # Add other provider keys here as needed

    user = relationship("User", back_populates="user_profile")
