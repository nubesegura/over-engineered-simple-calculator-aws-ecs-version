"""Tests of the rotation steps with moto (Secrets Manager) and a fake database."""

import json
import logging
import re
import ssl
from collections.abc import Iterator
from typing import Any

import boto3
import pg8000.native
import pytest
from moto import mock_aws
from moto.secretsmanager.models import secretsmanager_backends

from db_rotation import handler as h

DEFAULT_ACCOUNT_ID = "123456789012"

TOKEN = "11111111-2222-3333-4444-555555555555"  # noqa: S105
HOST = {"host": "db.internal", "port": 5432, "dbname": "calc"}
MASTER: dict[str, Any] = {"username": "master", "password": "master-pw-0001"}
CURRENT: dict[str, Any] = {"username": "calc_app", "password": "current-pw-0001", **HOST}


class FakeServer:
    """In-memory PostgreSQL: roles with passwords and group memberships."""

    def __init__(self) -> None:
        self.passwords = {"master": MASTER["password"], "calc_app": CURRENT["password"]}
        self.members: dict[str, set[str]] = {"calc_app": {"calc_app_rw"}}
        self.statements: list[str] = []

    def connect(self, secret: dict[str, Any], user: str, password: str) -> FakeConnection:
        assert secret["host"] == HOST["host"]
        if self.passwords.get(user) != password:
            raise ConnectionRefusedError(f"password authentication failed for {user}")
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.closed = False

    def run(self, sql: str, **params: Any) -> list[list[Any]] | None:
        self.server.statements.append(sql)
        if sql == "SELECT 1":
            return [[1]]
        if sql.startswith("SELECT 1 FROM pg_roles"):
            return [[1]] if params["name"] in self.server.passwords else []
        if sql.startswith("SELECT g.rolname"):
            return [[g] for g in sorted(self.server.members.get(params["base"], set()))]
        if match := re.fullmatch(r'CREATE ROLE "(\w+)" WITH LOGIN', sql):
            self.server.passwords[match[1]] = ""
            return None
        if match := re.fullmatch(r'GRANT "(\w+)" TO "(\w+)"', sql):
            self.server.members.setdefault(match[2], set()).add(match[1])
            return None
        if match := re.fullmatch(r"ALTER ROLE \"(\w+)\" WITH PASSWORD '(.*)'", sql):
            self.server.passwords[match[1]] = match[2]
            return None
        raise AssertionError(f"unexpected SQL: {sql}")

    def close(self) -> None:
        self.closed = True


class Env:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.sm: Any = boto3.client("secretsmanager", region_name="us-east-1")
        self.arn: str = self.sm.create_secret(Name="app", SecretString=json.dumps(CURRENT))["ARN"]
        self.master_arn: str = self.sm.create_secret(
            Name="master", SecretString=json.dumps(MASTER)
        )["ARN"]
        secret = self.backend_secret("app")
        secret.rotation_requested = True
        secret.rotation_enabled = True
        secret.auto_rotate_after_days = 3
        self.start_rotation(TOKEN)

    @staticmethod
    def backend_secret(name: str) -> Any:
        """Reach into moto: it cannot enable rotation without invoking a real function."""
        return secretsmanager_backends[DEFAULT_ACCOUNT_ID]["us-east-1"].secrets[name]

    def start_rotation(self, token: str) -> None:
        """Stage an empty AWSPENDING version, as Secrets Manager does when a rotation starts."""
        versions = self.backend_secret("app").versions
        for version in versions.values():
            version["version_stages"] = [s for s in version["version_stages"] if s != "AWSPENDING"]
        versions[token] = {"createdate": 0, "version_id": token, "version_stages": ["AWSPENDING"]}

    def step(self, name: str) -> None:
        h.handler({"SecretId": self.arn, "ClientRequestToken": TOKEN, "Step": name}, None)

    def stages(self) -> dict[str, list[str]]:
        versions: dict[str, list[str]] = self.sm.describe_secret(SecretId=self.arn)[
            "VersionIdsToStages"
        ]
        return versions

    def secret(self, **kwargs: str) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(
            self.sm.get_secret_value(SecretId=self.arn, **kwargs)["SecretString"]
        )
        return value


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    server = FakeServer()
    monkeypatch.setattr(h, "connect", server.connect)
    with mock_aws():
        e = Env(server)
        monkeypatch.setenv("MASTER_SECRET_ARN", e.master_arn)
        yield e


def test_create_secret_stores_pending_for_the_inactive_user_once(env: Env) -> None:
    env.step("createSecret")
    pending = env.secret(VersionId=TOKEN, VersionStage="AWSPENDING")
    assert pending["username"] == "calc_app_clone"
    assert pending["password"] != CURRENT["password"]
    assert {k: pending[k] for k in HOST} == HOST
    env.step("createSecret")
    assert env.secret(VersionId=TOKEN, VersionStage="AWSPENDING") == pending


def test_users_alternate_back_to_the_base_user(env: Env) -> None:
    for name in ("createSecret", "setSecret", "testSecret", "finishSecret"):
        env.step(name)
    second = "99999999-2222-3333-4444-555555555555"
    env.start_rotation(second)
    h.handler({"SecretId": env.arn, "ClientRequestToken": second, "Step": "createSecret"}, None)
    assert env.secret(VersionId=second, VersionStage="AWSPENDING")["username"] == "calc_app"


def test_set_secret_creates_clone_with_group_role_and_password(env: Env) -> None:
    env.step("createSecret")
    env.step("setSecret")
    pending = env.secret(VersionId=TOKEN, VersionStage="AWSPENDING")
    assert env.server.members["calc_app_clone"] == {"calc_app_rw"}
    assert env.server.passwords["calc_app_clone"] == pending["password"]
    assert env.server.passwords["calc_app"] == CURRENT["password"]


def test_set_secret_twice_does_not_recreate_the_clone(env: Env) -> None:
    env.step("createSecret")
    env.step("setSecret")
    env.step("setSecret")
    creates = [s for s in env.server.statements if s.startswith("CREATE ROLE")]
    assert len(creates) == 1


def test_set_secret_quotes_a_password_with_a_quote(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.step("createSecret")
    pending = env.secret(VersionId=TOKEN, VersionStage="AWSPENDING")
    env.sm.put_secret_value(
        SecretId=env.arn,
        ClientRequestToken=TOKEN,
        SecretString=json.dumps({**pending, "password": "a'b"}),
        VersionStages=["AWSPENDING"],
    )
    env.step("setSecret")
    assert any("'a''b'" in s for s in env.server.statements)


def test_test_secret_logs_in_as_the_pending_user(env: Env) -> None:
    env.step("createSecret")
    env.step("setSecret")
    env.step("testSecret")


def test_failed_test_leaves_current_version_valid(env: Env) -> None:
    env.step("createSecret")
    before = env.stages()
    with pytest.raises(h.RotationError, match="login failed"):
        env.step("testSecret")
    with pytest.raises(h.RotationError):
        env.step("testSecret")
    assert env.stages() == before
    assert env.secret(VersionStage="AWSCURRENT") == CURRENT
    assert env.server.passwords["calc_app"] == CURRENT["password"]


def test_finish_moves_stages_after_a_successful_test_and_is_idempotent(env: Env) -> None:
    for name in ("createSecret", "setSecret", "testSecret", "finishSecret"):
        env.step(name)
    current = env.secret(VersionStage="AWSCURRENT")
    assert current["username"] == "calc_app_clone"
    assert "AWSCURRENT" in env.stages()[TOKEN]
    env.step("finishSecret")
    assert env.secret(VersionStage="AWSCURRENT") == current


def test_database_error_is_sanitized(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.step("createSecret")

    def failing(secret: dict[str, Any], user: str, password: str) -> Any:
        raise OSError(f"cannot connect with {password}")

    monkeypatch.setattr(h, "connect", failing)
    with pytest.raises(h.RotationError) as caught:
        env.step("setSecret")
    pending = env.secret(VersionId=TOKEN, VersionStage="AWSPENDING")
    assert pending["password"] not in str(caught.value)
    assert caught.value.__cause__ is None


def test_passwords_never_appear_in_logs(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    for name in ("createSecret", "setSecret", "testSecret", "finishSecret"):
        env.step(name)
    new_password = env.secret(VersionStage="AWSCURRENT")["password"]
    for password in (new_password, CURRENT["password"], MASTER["password"]):
        assert password not in caplog.text


def test_rotation_disabled_and_unknown_token_are_rejected(env: Env) -> None:
    other = env.sm.create_secret(Name="plain", SecretString=json.dumps(CURRENT))["ARN"]
    event: dict[str, Any] = {"SecretId": other, "ClientRequestToken": TOKEN, "Step": "createSecret"}
    with pytest.raises(h.RotationError, match="rotation disabled"):
        h.handler(event, None)
    with pytest.raises(h.RotationError, match="no stage"):
        h.handler({**event, "SecretId": env.arn, "ClientRequestToken": "x" * 36}, None)


def test_unknown_step_and_missing_master_arn_are_rejected(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(h.RotationError, match="Unknown"):
        env.step("noSuchStep")
    env.step("createSecret")
    monkeypatch.delenv("MASTER_SECRET_ARN")
    with pytest.raises(h.RotationError, match="MASTER_SECRET_ARN"):
        env.step("setSecret")


def test_clone_without_base_group_role_is_rejected(env: Env) -> None:
    env.server.members["calc_app"] = set()
    env.step("createSecret")
    with pytest.raises(h.RotationError, match="no group role"):
        env.step("setSecret")


def test_tls_context_verifies_against_the_ca_file_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    real_default = ssl.create_default_context

    def fake_default(cafile: str | None = None) -> ssl.SSLContext:
        seen["cafile"] = cafile
        return real_default()

    monkeypatch.setenv("DB_SSL_CA_FILE", "/opt/rds-ca.pem")
    monkeypatch.setattr(ssl, "create_default_context", fake_default)
    context = h.tls_context()
    assert seen["cafile"] == "/opt/rds-ca.pem"
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname


def test_tls_context_without_ca_file_is_tls_without_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DB_SSL_CA_FILE", raising=False)
    context = h.tls_context()
    assert context.verify_mode == ssl.CERT_NONE
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_connect_always_passes_a_tls_context(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.delenv("DB_SSL_CA_FILE", raising=False)
    monkeypatch.setattr(pg8000.native, "Connection", lambda *_a, **kwargs: captured.update(kwargs))
    h.connect(CURRENT, "calc_app", "x")
    assert isinstance(captured["ssl_context"], ssl.SSLContext)
