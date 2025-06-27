import os
import sys

# Add project root to sys.path, assuming env.py is in api_service/migrations
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

import pathlib # For path manipulation

# add your model's MetaData object here
# for 'autogenerate' support
from api_service.db.models import Base  # Import Base
from moonmind.config.settings import AppSettings # Import AppSettings class

# Determine project root from env.py's location: api_service/migrations/env.py -> app/
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DOTENV_PATH = PROJECT_ROOT / ".env"

# Instantiate settings locally for Alembic, ensuring .env is loaded correctly
local_settings = AppSettings(_env_file=DOTENV_PATH)

target_metadata = Base.metadata  # Use Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    # url = config.get_main_option("sqlalchemy.url") # Comment out original url
    url = local_settings.database.DATABASE_URL # Use DATABASE_URL from local_settings
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Use DATABASE_URL from Pydantic settings instead of alembic.ini
    configuration = config.get_section(config.config_ini_section)
    # Explicitly set the DB URL for Alembic to ensure 'postgres' hostname
    # This overrides potentially problematic environment/Pydantic loading for Alembic
    # Changed 'postgres' to 'localhost' for environments where 'postgres' service name isn't resolvable
    db_url_alembic = "postgresql+psycopg2://moonmind_user:moonmind_password@localhost:5432/moonmind_db"
    configuration["sqlalchemy.url"] = db_url_alembic

    connectable = engine_from_config(
        configuration, # Use modified configuration
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
