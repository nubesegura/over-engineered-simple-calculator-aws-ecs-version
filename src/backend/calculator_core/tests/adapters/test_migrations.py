import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from calculator_core.adapters.outbound.migrations import apply_migrations, set_role_password
from tests.adapters.postgres_support import MIGRATIONS_DIR, reset_database

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not set")

APP_PASSWORD = "test-only-app-password"  # noqa: S105 - throwaway value for a throwaway database


@pytest.fixture
def admin() -> Iterator[psycopg.Connection[Any]]:
    assert DATABASE_URL
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        reset_database(connection)
        yield connection


def test_files_are_applied_once_and_the_runner_is_repeatable(
    admin: psycopg.Connection[Any],
) -> None:
    first = apply_migrations(admin, MIGRATIONS_DIR)
    second = apply_migrations(admin, MIGRATIONS_DIR)

    assert first == ["0001_create_calculations", "0002_application_role"]
    assert second == []
    recorded = admin.execute("SELECT count(*) FROM schema_migrations").fetchone()
    assert recorded == (2,)


def test_a_failing_file_is_rolled_back_and_not_recorded(
    admin: psycopg.Connection[Any], tmp_path: Path
) -> None:
    (tmp_path / "0001_broken.sql").write_text(
        "CREATE TABLE half_done (id int); SELECT 1/0;", encoding="utf-8"
    )

    with pytest.raises(psycopg.errors.DivisionByZero):
        apply_migrations(admin, tmp_path)

    assert admin.execute("SELECT to_regclass('half_done')").fetchone() == (None,)
    assert admin.execute("SELECT count(*) FROM schema_migrations").fetchone() == (0,)


def test_application_user_can_only_select_and_insert_on_calculations(
    admin: psycopg.Connection[Any],
) -> None:
    assert DATABASE_URL
    apply_migrations(admin, MIGRATIONS_DIR)
    set_role_password(admin, "calc_app", APP_PASSWORD)
    conninfo = make_conninfo(DATABASE_URL, user="calc_app", password=APP_PASSWORD)

    with psycopg.connect(conninfo, autocommit=True) as app:
        app.execute("SELECT count(*) FROM calculations").fetchone()
        for forbidden in (
            "CREATE TABLE intruder (id int)",
            "CREATE ROLE intruder",
            "DELETE FROM calculations",
            "UPDATE calculations SET result = 0",
            "DROP TABLE calculations",
            "SELECT * FROM schema_migrations",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(forbidden)


def test_group_role_cannot_log_in_but_the_application_user_can(
    admin: psycopg.Connection[Any],
) -> None:
    apply_migrations(admin, MIGRATIONS_DIR)

    flags = admin.execute(
        "SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname IN ('calc_app', 'calc_app_rw') "
        "ORDER BY rolname"
    ).fetchall()
    assert flags == [("calc_app", True), ("calc_app_rw", False)]
