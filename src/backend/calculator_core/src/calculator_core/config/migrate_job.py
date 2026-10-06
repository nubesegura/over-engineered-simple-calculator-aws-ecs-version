"""Migration job: schema migrations with the master credential, then the application password.

Both secrets are read from Secrets Manager at run time. Neither password is printed or logged;
a failure is reported by its type only and the process exits non-zero.
"""

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import psycopg

from calculator_core.adapters.outbound.migrations import apply_migrations, set_role_password
from calculator_core.config.observability import configure_logging

logger = logging.getLogger(__name__)

DEFAULT_MIGRATIONS_DIRECTORY = "/app/migrations"
_REQUIRED = ("MASTER_SECRET_ARN", "APP_SECRET_ARN", "DB_HOST", "DB_NAME")


class MigrateConfigError(Exception):
    """The environment of the job is incomplete."""


@dataclass(frozen=True)
class MigrateSettings:
    master_secret_arn: str
    app_secret_arn: str
    db_host: str
    db_port: int
    db_name: str
    migrations_directory: Path


def load_migrate_settings(env: Mapping[str, str]) -> MigrateSettings:
    missing = [name for name in _REQUIRED if not env.get(name, "").strip()]
    if missing:
        raise MigrateConfigError(f"Missing environment variables: {', '.join(missing)}.")
    port = env.get("DB_PORT", "").strip() or "5432"
    if not (port.isdigit() and 0 < int(port) < 65536):
        raise MigrateConfigError("DB_PORT must be a number between 1 and 65535.")
    return MigrateSettings(
        master_secret_arn=env["MASTER_SECRET_ARN"].strip(),
        app_secret_arn=env["APP_SECRET_ARN"].strip(),
        db_host=env["DB_HOST"].strip(),
        db_port=int(port),
        db_name=env["DB_NAME"].strip(),
        migrations_directory=Path(env.get("MIGRATIONS_DIRECTORY", DEFAULT_MIGRATIONS_DIRECTORY)),
    )


def run_migrations(settings: MigrateSettings, secrets_client: Any) -> list[str]:
    """Apply the pending files and set the application login password; return versions applied."""
    master = _read_secret(secrets_client, settings.master_secret_arn)
    application = _read_secret(secrets_client, settings.app_secret_arn)
    with psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=master["username"],
        password=master["password"],
        sslmode="require",
        autocommit=True,
    ) as connection:
        applied = apply_migrations(connection, settings.migrations_directory)
        set_role_password(connection, application["username"], application["password"])
    return applied


def main() -> int:
    configure_logging("migrate", os.environ.get("ENVIRONMENT", "local"))
    try:
        settings = load_migrate_settings(os.environ)
        applied = run_migrations(settings, boto3.client("secretsmanager"))
    except Exception as error:
        # Type only: driver and SDK messages are not guaranteed to be free of secrets.
        logger.error("Migration job failed (%s).", type(error).__name__)
        return 1
    logger.info("Migration job finished; %d file(s) applied.", len(applied))
    return 0


def _read_secret(client: Any, secret_id: str) -> dict[str, str]:
    secret = json.loads(client.get_secret_value(SecretId=secret_id)["SecretString"])
    return {"username": secret["username"], "password": secret["password"]}
