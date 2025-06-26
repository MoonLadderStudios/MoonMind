import uuid

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, Integer, String, Boolean # Added String and Boolean


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
