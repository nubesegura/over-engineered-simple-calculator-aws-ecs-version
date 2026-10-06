# ruff: noqa: S105 - throwaway test passwords
import json
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from calculator_core.adapters.outbound.secrets_manager_credentials import (
    SecretsManagerCredentialsProvider,
)
from calculator_core.application.errors import InfrastructureError


class Ticker:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def secrets_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        yield boto3.client("secretsmanager", region_name="us-east-2")


def _create(client: Any, password: str) -> str:
    secret = {"username": "calc_app", "password": password}
    return str(client.create_secret(Name="app", SecretString=json.dumps(secret))["ARN"])


def test_reads_username_and_password_and_caches_for_the_ttl(secrets_client: Any) -> None:
    arn, ticker = _create(secrets_client, "first"), Ticker()
    provider = SecretsManagerCredentialsProvider(arn, secrets_client, 60, ticker)

    assert provider.get().password == "first"
    secrets_client.put_secret_value(SecretId=arn, SecretString='{"username":"u","password":"two"}')
    ticker.now += 59
    assert provider.get().password == "first"
    ticker.now += 2
    credentials = provider.get()
    assert (credentials.username, credentials.password) == ("u", "two")


def test_refresh_ignores_the_cache(secrets_client: Any) -> None:
    arn = _create(secrets_client, "first")
    provider = SecretsManagerCredentialsProvider(arn, secrets_client, 60, Ticker())
    provider.get()
    secrets_client.put_secret_value(SecretId=arn, SecretString='{"username":"u","password":"two"}')

    assert provider.refresh().password == "two"
    assert provider.get().password == "two"


def test_missing_secret_raises_infrastructure_error(secrets_client: Any) -> None:
    provider = SecretsManagerCredentialsProvider("missing", secrets_client, 60, Ticker())

    with pytest.raises(InfrastructureError):
        provider.get()


def test_malformed_secret_raises_error_without_the_content(secrets_client: Any) -> None:
    secrets_client.create_secret(Name="bad", SecretString='{"user":"leaked-value"}')
    provider = SecretsManagerCredentialsProvider("bad", secrets_client, 60, Ticker())

    with pytest.raises(InfrastructureError) as raised:
        provider.get()

    assert "leaked-value" not in str(raised.value)
    assert "leaked-value" not in repr(raised.value.original_error)
