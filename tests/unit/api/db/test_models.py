import pytest
from fastapi_users.db import SQLAlchemyBaseUserTable
from sqlalchemy import inspect
from sqlalchemy.orm import DeclarativeBase

from api_service.db.models import Base, User


def test_user_model_inheritance():
    """Test that the User model inherits from SQLAlchemyBaseUserTable and Base."""
    assert issubclass(User, SQLAlchemyBaseUserTable)
    assert issubclass(User, Base)

def test_user_model_columns():
    """Test that the User model has the expected columns."""
    inspector = inspect(User)
    columns = [column.key for column in inspector.columns]

    # Columns inherited from SQLAlchemyBaseUserTable
    assert "id" in columns
    assert "email" in columns
    assert "hashed_password" in columns
    assert "is_active" in columns
    assert "is_superuser" in columns
    assert "is_verified" in columns

    # Check types (optional, but good for completeness)
    # Note: id is a UUID/GUID type which doesn't implement python_type
    # We can check that it's a UUID-like type
    from sqlalchemy.dialects.postgresql import UUID
    from sqlalchemy.sql.sqltypes import TypeDecorator
    id_type = inspector.columns["id"].type
    id_type_str = str(id_type).upper()

    # Check if it's a UUID, GUID, or has UUID-like characteristics
    is_uuid_like = (
        isinstance(id_type, UUID) or
        'GUID' in id_type_str or
        'UUID' in id_type_str or
        'CHAR(36)' in id_type_str  # GUID often renders as CHAR(36)
    )
    assert is_uuid_like, f"Expected UUID-like type, got {type(id_type)} with string representation '{id_type_str}'"

    assert inspector.columns["email"].type.python_type is str
    assert inspector.columns["hashed_password"].type.python_type is str
    assert inspector.columns["is_active"].type.python_type is bool
    assert inspector.columns["is_superuser"].type.python_type is bool
    assert inspector.columns["is_verified"].type.python_type is bool

def test_base_model_inheritance():
    """Test that the Base model inherits from DeclarativeBase."""
    assert issubclass(Base, DeclarativeBase)
