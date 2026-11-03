"""procrastinate job queue schema, installed from procrastinate 3.10's own schema.sql

Upgrading procrastinate later means a new revision that runs its migration files.

Revision ID: 507f7ae60c4c
Revises: ff2191592ce9
Create Date: 2026-11-03 10:15:55.233505
"""

from collections.abc import Sequence

from alembic import op
from procrastinate.schema import SchemaManager
from sqlalchemy.util import await_only

revision: str = "507f7ae60c4c"
down_revision: str | Sequence[str] | None = "ff2191592ce9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# procrastinate ships no uninstall script, so the downgrade removes every object it created in
# the current schema: tables first (their row types go with them), then routines, then types.
REMOVE_QUEUE_OBJECTS = """
DO $$
DECLARE
    obj record;
BEGIN
    FOR obj IN
        SELECT tablename FROM pg_tables
        WHERE schemaname = current_schema() AND tablename LIKE 'procrastinate%'
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', obj.tablename);
    END LOOP;
    FOR obj IN
        SELECT p.oid::regprocedure AS signature FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = current_schema() AND p.proname LIKE 'procrastinate%'
    LOOP
        EXECUTE format('DROP ROUTINE IF EXISTS %s CASCADE', obj.signature);
    END LOOP;
    FOR obj IN
        SELECT t.typname FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = current_schema() AND t.typname LIKE 'procrastinate%'
          AND t.typtype IN ('c', 'e')
    LOOP
        EXECUTE format('DROP TYPE IF EXISTS %I CASCADE', obj.typname);
    END LOOP;
END;
$$
"""


def upgrade() -> None:
    # schema.sql holds many statements. asyncpg runs a multi-statement script only through its
    # own execute(), not through the prepared statements op.execute uses.
    asyncpg_connection = op.get_bind().connection.driver_connection
    if asyncpg_connection is None:
        raise RuntimeError("procrastinate schema install needs an open asyncpg connection")
    await_only(asyncpg_connection.execute(SchemaManager.get_schema()))


def downgrade() -> None:
    op.execute(REMOVE_QUEUE_OBJECTS)
