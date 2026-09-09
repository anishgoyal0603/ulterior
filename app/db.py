from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base
from . import config

# Single source of truth: app/config.py reads and VALIDATES this (it refuses
# to start in production on SQLite, or on a Postgres URL without sslmode).
DATABASE_URL = config.DATABASE_URL

# check_same_thread is a SQLite-only argument; passing it to Postgres errors out.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


# Columns added after someone may already have a database file. SQLAlchemy's
# create_all() creates missing TABLES but never alters existing ones, so a
# developer who ran an older build would get "no such column" on every query
# rather than a working app. This is not a migration framework -- it is the
# smallest thing that stops a schema addition from breaking a local database,
# for a project whose production path is a fresh container anyway.
#
# This is the only DDL in the codebase, and it is the only place that emits a
# statement the ORM did not build for us. Two rules keep that safe, and
# test_schema_backfill_is_bounded_by_the_models enforces both:
#
#   1. The column list is not written here at all. It is read out of
#      Base.metadata, so this can only ever add a column that the models
#      already declare -- there is no string a caller could influence.
#   2. Identifiers and types are rendered by the dialect (its identifier
#      preparer and type compiler), not by string formatting, so a column
#      named like a keyword is quoted correctly rather than producing a
#      syntax error on one backend and something worse on another.
#
# DDL cannot use bound parameters for identifiers on any database, so
# "parameterize it" is not an available answer here; deriving it from the
# models is the equivalent guarantee.


def _add_missing_columns():
    from sqlalchemy import inspect

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    preparer = engine.dialect.identifier_preparer
    type_compiler = engine.dialect.type_compiler_instance

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all() will have made it in full
            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                if not column.nullable and column.server_default is None:
                    # ALTER TABLE ADD COLUMN NOT NULL with no default is
                    # rejected by SQLite (and would leave existing rows
                    # invalid anywhere else). Backfilling it needs a real
                    # migration and a decision about what the old rows should
                    # say, which is not something to guess at import time.
                    raise RuntimeError(
                        f"{table.name}.{column.name} is a new NOT NULL column. "
                        f"Add a server_default, or migrate the database "
                        f"deliberately -- startup will not invent a value for "
                        f"rows that already exist."
                    )
                conn.exec_driver_sql(
                    "ALTER TABLE {} ADD COLUMN {} {}".format(
                        preparer.format_table(table),
                        preparer.format_column(column),
                        type_compiler.process(column.type),
                    )
                )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
