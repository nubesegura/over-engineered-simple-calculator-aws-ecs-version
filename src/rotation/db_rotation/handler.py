"""Secrets Manager rotation function for the PostgreSQL application credential.

Strategy: alternating users. The application secret holds the credential of one of two database
users, "<base>" and "<base>_clone". Each rotation sets a new password on the inactive user, tests
a login with it and only then makes it the current version, so the current credential stays valid
until the very last step.

Environment variables: MASTER_SECRET_ARN (secret with the database master credential) and the
optional DB_SSL_CA_FILE (PEM bundle used to verify the database certificate; when it is not
set the connection is still TLS but the server certificate is not verified).

Passwords are never logged: database errors are replaced by their class name.
"""

import json
import logging
import os
import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import boto3
import pg8000.native
from botocore.config import Config

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

CLONE_SUFFIX = "_clone"
PASSWORD_LENGTH = 32
EXCLUDED_CHARACTERS = "/@\"'\\`"
DB_TIMEOUT_SECONDS = 10
CLIENT_CONFIG = Config(
    connect_timeout=5,
    read_timeout=15,
    retries={"max_attempts": 5, "mode": "standard"},
)


class RotationError(Exception):
    """Raised when a rotation step cannot be completed."""


class Database(Protocol):
    """The part of a database connection used by the function."""

    def run(self, sql: str, **params: Any) -> list[list[Any]] | None:
        """Run one statement with named parameters (:name) and return the rows, if any."""

    def close(self) -> None:
        """Close the connection."""


def tls_context() -> ssl.SSLContext:
    """Build the TLS context: verified against DB_SSL_CA_FILE when set, else encryption only."""
    ca_file = os.environ.get("DB_SSL_CA_FILE")
    if ca_file:
        return ssl.create_default_context(cafile=ca_file)
    # Accepted gap of the test project (like sslmode=require): the server is not authenticated.
    # The fix is packaging the RDS global CA bundle and setting DB_SSL_CA_FILE.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def connect(secret: dict[str, Any], user: str, password: str) -> Database:
    """Open a TLS connection to the database described by the secret."""
    context = tls_context()
    return pg8000.native.Connection(  # type: ignore[no-any-return]
        user,
        host=secret["host"],
        port=int(secret["port"]),
        database=secret["dbname"],
        password=password,
        ssl_context=context,
        timeout=DB_TIMEOUT_SECONDS,
    )


@contextmanager
def _database(secret: dict[str, Any], user: str, password: str) -> Iterator[Database]:
    """Connect and hide driver error details, which could echo statements or credentials."""
    try:
        db = connect(secret, user, password)
    except Exception as exc:
        raise RotationError(f"Database login failed ({type(exc).__name__})") from None
    try:
        yield db
    except RotationError:
        raise
    except Exception as exc:
        raise RotationError(f"Database operation failed ({type(exc).__name__})") from None
    finally:
        db.close()


def _base_user(username: str) -> str:
    return username.removesuffix(CLONE_SUFFIX)


def _other_user(username: str) -> str:
    base = _base_user(username)
    return base if username.endswith(CLONE_SUFFIX) else base + CLONE_SUFFIX


def _secret_value(client: Any, arn: str, **kwargs: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(
        client.get_secret_value(SecretId=arn, **kwargs)["SecretString"]
    )
    return value


def create_secret(client: Any, arn: str, token: str) -> None:
    """Store a new password for the inactive user as AWSPENDING, once."""
    current = _secret_value(client, arn, VersionStage="AWSCURRENT")
    try:
        client.get_secret_value(SecretId=arn, VersionId=token, VersionStage="AWSPENDING")
        LOGGER.info("createSecret: pending version already exists")
        return
    except client.exceptions.ResourceNotFoundException:
        pass
    password = client.get_random_password(
        PasswordLength=PASSWORD_LENGTH, ExcludeCharacters=EXCLUDED_CHARACTERS
    )["RandomPassword"]
    pending = {**current, "username": _other_user(current["username"]), "password": password}
    client.put_secret_value(
        SecretId=arn,
        ClientRequestToken=token,
        SecretString=json.dumps(pending),
        VersionStages=["AWSPENDING"],
    )
    LOGGER.info("createSecret: stored pending version for the inactive user")


def _ensure_clone(db: Database, user: str) -> None:
    """Create the clone with the group roles of the base user when it does not exist."""
    quoted = pg8000.native.identifier(user)
    if not db.run("SELECT 1 FROM pg_roles WHERE rolname = :name", name=user):
        db.run(f"CREATE ROLE {quoted} WITH LOGIN")  # noqa: S608
        LOGGER.info("setSecret: created database user %s", user)
    groups = db.run(
        "SELECT g.rolname FROM pg_auth_members m"
        " JOIN pg_roles g ON g.oid = m.roleid"
        " JOIN pg_roles u ON u.oid = m.member"
        " WHERE u.rolname = :base",
        base=_base_user(user),
    )
    if not groups:
        raise RotationError("The base database user has no group role to copy")
    for (group,) in groups:
        db.run(f"GRANT {pg8000.native.identifier(group)} TO {quoted}")


def set_secret(client: Any, arn: str, token: str, master_arn: str) -> None:
    """Create the clone when needed and set the pending password on the inactive user."""
    pending = _secret_value(client, arn, VersionId=token, VersionStage="AWSPENDING")
    master = _secret_value(client, master_arn)
    with _database(pending, master["username"], master["password"]) as db:
        if pending["username"].endswith(CLONE_SUFFIX):
            _ensure_clone(db, pending["username"])
        quoted = pg8000.native.identifier(pending["username"])
        password = pg8000.native.literal(pending["password"])
        db.run(f"ALTER ROLE {quoted} WITH PASSWORD {password}")
    LOGGER.info("setSecret: password set for database user %s", pending["username"])


def test_secret(client: Any, arn: str, token: str) -> None:
    """Log in with the pending credential and run a trivial query."""
    pending = _secret_value(client, arn, VersionId=token, VersionStage="AWSPENDING")
    with _database(pending, pending["username"], pending["password"]) as db:
        if db.run("SELECT 1") != [[1]]:
            raise RotationError("Test query returned an unexpected result")
    LOGGER.info("testSecret: login as %s succeeded", pending["username"])


def finish_secret(client: Any, arn: str, token: str) -> None:
    """Make the pending version the current one."""
    metadata = client.describe_secret(SecretId=arn)
    current = None
    for version, stages in metadata["VersionIdsToStages"].items():
        if "AWSCURRENT" in stages:
            if version == token:
                LOGGER.info("finishSecret: version already current")
                return
            current = version
    client.update_secret_version_stage(
        SecretId=arn,
        VersionStage="AWSCURRENT",
        MoveToVersionId=token,
        **({"RemoveFromVersionId": current} if current else {}),
    )
    LOGGER.info("finishSecret: version moved to AWSCURRENT")


def _validate(client: Any, arn: str, token: str) -> bool:
    """Check rotation is enabled and the token is staged; return True when already finished."""
    metadata = client.describe_secret(SecretId=arn)
    if not metadata.get("RotationEnabled"):
        raise RotationError("Secret has rotation disabled")
    versions = metadata.get("VersionIdsToStages", {})
    if token not in versions:
        raise RotationError("Secret version has no stage for this rotation")
    if "AWSCURRENT" in versions[token]:
        LOGGER.info("Version is already AWSCURRENT, nothing to do")
        return True
    if "AWSPENDING" not in versions[token]:
        raise RotationError("Secret version is not staged AWSPENDING")
    return False


def handler(event: dict[str, Any], _context: object) -> None:
    """Entrypoint: Secrets Manager rotation event (SecretId, ClientRequestToken, Step)."""
    arn, token, step = event["SecretId"], event["ClientRequestToken"], event["Step"]
    try:
        client = boto3.client("secretsmanager", config=CLIENT_CONFIG)
        if _validate(client, arn, token):
            return
        if step == "createSecret":
            create_secret(client, arn, token)
        elif step == "setSecret":
            set_secret(client, arn, token, _master_arn())
        elif step == "testSecret":
            test_secret(client, arn, token)
        elif step == "finishSecret":
            finish_secret(client, arn, token)
        else:
            raise RotationError(f"Unknown rotation step {step}")
    except Exception:
        LOGGER.exception("Rotation step %s failed", step)
        raise


def _master_arn() -> str:
    arn = os.environ.get("MASTER_SECRET_ARN")
    if not arn:
        raise RotationError("Missing environment variable MASTER_SECRET_ARN")
    return arn
