from sqlalchemy import create_engine
from sqlalchemy.schema import CreateColumn
from sqlalchemy.orm import sessionmaker

from .models import Base
from . import config

# Single source of truth: app/config.py reads and VALIDATES this (it refuses
# to start in production on SQLite, or on a Postgres URL without sslmode).
DATABASE_URL = config.DATABASE_URL

# check_same_thread is a SQLite-only argument; passing it to Postgres errors out.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)

if DATABASE_URL.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        """SQLite is the development database, but since 2.4 it has several
        writers at once: audit workers, a heartbeat thread per job, the
        cancel check between steps, the hourly retention sweep and the API.
        With the defaults (rollback journal, 5 s busy wait) concurrent writes
        fail with "database is locked".

          * WAL lets readers and one writer proceed together;
          * busy_timeout makes a writer wait for the lock instead of failing;
          * foreign_keys enforces ON DELETE CASCADE, which SQLite ignores
            unless asked.
        """
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA foreign_keys=ON")
            if ":memory:" not in DATABASE_URL:
                cur.execute("PRAGMA journal_mode=WAL")
        finally:
            cur.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    _add_missing_indexes()


def _add_missing_indexes():
    """create_all() adds indexes only to tables it creates. Add the ones a
    database made by an older build lacks. A database that already holds
    rows the new unique index forbids keeps working without it (and says so)
    rather than refusing to start."""
    import logging
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            try:
                index.create(bind=engine, checkfirst=True)
            except Exception as exc:          # noqa: BLE001 -- startup must not die here
                logging.getLogger(__name__).warning(
                    "Could not add index %s (%s); existing rows conflict with it.",
                    index.name, type(exc).__name__)


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
                # CreateColumn renders the column exactly as CREATE TABLE
                # would -- name, type, DEFAULT and NOT NULL -- from the model.
                # Building it from the type alone dropped the DEFAULT and the
                # NOT NULL, so a column declared NOT NULL with a default was
                # added as a nullable column with no default.
                ddl = str(CreateColumn(column).compile(dialect=engine.dialect)).strip()
                conn.exec_driver_sql(
                    "ALTER TABLE {} ADD COLUMN {}".format(preparer.format_table(table), ddl)
                )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
