"""Helpers for the tests that need a real PostgreSQL (`TEST_DATABASE_URL`)."""

from pathlib import Path
from typing import Any

from psycopg import Connection

# tests/adapters -> tests -> calculator_core -> backend
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


def reset_database(admin: Connection[Any]) -> None:
    """Remove what the migrations create so every test starts from an empty database."""
    admin.execute("DROP TABLE IF EXISTS calculations, schema_migrations")
    admin.execute("DROP ROLE IF EXISTS calc_app")
    admin.execute("DROP ROLE IF EXISTS calc_app_rw")
