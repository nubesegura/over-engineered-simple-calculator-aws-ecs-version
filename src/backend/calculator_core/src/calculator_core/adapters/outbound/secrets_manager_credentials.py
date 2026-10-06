"""Reads the application database credentials from AWS Secrets Manager.

Only the application secret is read here; the master secret is never touched by this code.
"""

import json
import threading
import time
from collections.abc import Callable
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from calculator_core.application.errors import InfrastructureError
from calculator_core.application.ports.credentials_provider import DatabaseCredentials

DEFAULT_TTL_SECONDS = 300.0


class SecretsManagerCredentialsProvider:
    """Caches the secret for a short, bounded time; `refresh` forces a new read."""

    def __init__(
        self,
        secret_id: str,
        client: Any,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._secret_id = secret_id
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._cached: DatabaseCredentials | None = None
        self._fetched_at = 0.0

    def get(self) -> DatabaseCredentials:
        with self._lock:
            if self._cached is not None and self._is_fresh():
                return self._cached
            return self._fetch()

    def refresh(self) -> DatabaseCredentials:
        with self._lock:
            return self._fetch()

    def _is_fresh(self) -> bool:
        return self._monotonic() - self._fetched_at < self._ttl_seconds

    def _fetch(self) -> DatabaseCredentials:
        try:
            response = self._client.get_secret_value(SecretId=self._secret_id)
        except (BotoCoreError, ClientError) as error:
            raise InfrastructureError("The application secret could not be read.", error) from error
        try:
            secret = json.loads(response["SecretString"])
            credentials = DatabaseCredentials(secret["username"], secret["password"])
        except KeyError, TypeError, ValueError:
            # No chaining: the parser or the lookup could echo part of the secret.
            raise InfrastructureError("The application secret has an unexpected format.") from None
        self._cached = credentials
        self._fetched_at = self._monotonic()
        return credentials
