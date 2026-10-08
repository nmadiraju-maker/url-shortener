"""Alembic environment: migrations run on a plain psycopg URL passed in by urlshort.adapters.postgres.migrate."""

from alembic import context
from sqlalchemy import create_engine, pool

url = context.config.attributes["url"].replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(url, poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
