"""Ordered SQL migrations. Run by the migration job only; the services never import this.

Each `NNNN_name.sql` file is applied once, in its own transaction, and recorded in
`schema_migrations`. The files are trusted repository content (not user input).
"""

import logging
from pathlib import Path
from typing import Any, LiteralString

from psycopg import Connection, sql

logger = logging.getLogger(__name__)

_LOCK_KEY = 7_302_002
_CREATE_TABLE: LiteralString = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
)


def apply_migrations(connection: Connection[Any], directory: Path) -> list[str]:
    """Apply the pending files of `directory`; return the versions applied by this call."""
    with connection.transaction():
        connection.execute(_CREATE_TABLE)
    applied: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        with connection.transaction():
            # Serializes concurrent migration jobs until their transaction ends.
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
            found = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = %s", (path.stem,)
            ).fetchone()
            if found is not None:
                continue
            connection.execute(path.read_text(encoding="utf-8"))
            connection.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
            applied.append(path.stem)
            logger.info("Applied migration %s.", path.stem)
    return applied


def set_role_password(connection: Connection[Any], role: str, password: str) -> None:
    """Set the password of a login role from a SCRAM verifier, never the clear text.

    The server may log a failed statement, so the clear password must not travel inside it.
    """
    verifier = connection.pgconn.encrypt_password(
        password.encode(), role.encode(), b"scram-sha-256"
    ).decode()
    statement = sql.SQL("ALTER ROLE {} PASSWORD {}").format(
        sql.Identifier(role), sql.Literal(verifier)
    )
    with connection.transaction():
        connection.execute(statement)
