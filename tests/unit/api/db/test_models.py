import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import DeclarativeBase
from api_service.db.models import User, Base
from fastapi_users.db import SQLAlchemyBaseUserTable

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
    assert inspector.columns["id"].type.python_type is int
    assert inspector.columns["email"].type.python_type is str
    assert inspector.columns["hashed_password"].type.python_type is str
    assert inspector.columns["is_active"].type.python_type is bool
    assert inspector.columns["is_superuser"].type.python_type is bool
    assert inspector.columns["is_verified"].type.python_type is bool

def test_base_model_inheritance():
    """Test that the Base model inherits from DeclarativeBase."""
    assert issubclass(Base, DeclarativeBase)
