"""S3 implementation of the object store port, limited to the ingestion bucket layout.

Layout: `incoming/<name>.csv` (input), `reports/<name>.json`, `processed/<name>.csv` and
`rejected/<name>.csv`. Only keys under `incoming/` ending in `.csv` are accepted.
"""

from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from calculator_core.application.errors import InfrastructureError
from calculator_core.application.ports.object_store import (
    ObjectNotFoundError,
    ObjectTooLargeError,
    Outcome,
)
from calculator_core.domain.errors import InvalidInputError

INCOMING_PREFIX = "incoming/"
CSV_SUFFIX = ".csv"
_MAX_KEY_CHARS = 1024
_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}

CLIENT_CONFIG = Config(
    connect_timeout=5, read_timeout=20, retries={"max_attempts": 3, "mode": "standard"}
)


class UnacceptedKeyError(InvalidInputError):
    """The key is not an `incoming/*.csv` object."""

    code = "UNACCEPTED_KEY"


def is_ingest_key(key: str) -> bool:
    """True for keys under `incoming/` that end in `.csv` (no dot segments, no control chars)."""
    name = key[len(INCOMING_PREFIX) : -len(CSV_SUFFIX)]
    return (
        key.startswith(INCOMING_PREFIX)
        and key.endswith(CSV_SUFFIX)
        and len(key) <= _MAX_KEY_CHARS
        and bool(name)
        and key.isprintable()
        and not any(part in (".", "..") for part in key.split("/"))
    )


def source_name(key: str) -> str:
    """Key without the `incoming/` prefix and the `.csv` suffix."""
    _require_ingest_key(key)
    return key[len(INCOMING_PREFIX) : -len(CSV_SUFFIX)]


def _require_ingest_key(key: str) -> None:
    if not is_ingest_key(key):
        raise UnacceptedKeyError("Only incoming/*.csv objects are accepted.")


class S3ObjectStore:
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def get(self, key: str, max_bytes: int) -> bytes:
        _require_ingest_key(key)
        try:
            head = self._client.head_object(Bucket=self._bucket, Key=key)
            if head["ContentLength"] > max_bytes:
                raise ObjectTooLargeError()
            body = self._client.get_object(Bucket=self._bucket, Key=key)["Body"]
            data: bytes = body.read(max_bytes + 1)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
                raise ObjectNotFoundError("The object does not exist.") from None
            raise InfrastructureError("The object could not be read.", error) from error
        except BotoCoreError as error:
            raise InfrastructureError("The object could not be read.", error) from error
        if len(data) > max_bytes:
            raise ObjectTooLargeError()
        return data

    def put_report(self, source_key: str, report: str) -> None:
        report_key = f"reports/{source_name(source_key)}.json"
        self._guarded(
            lambda: self._client.put_object(
                Bucket=self._bucket,
                Key=report_key,
                Body=report.encode(),
                ContentType="application/json",
            ),
            "The report could not be written.",
        )

    def move(self, key: str, outcome: Outcome) -> None:
        destination = f"{outcome}/{source_name(key)}{CSV_SUFFIX}"
        self._guarded(
            lambda: self._client.copy_object(
                Bucket=self._bucket,
                Key=destination,
                CopySource={"Bucket": self._bucket, "Key": key},
            ),
            "The object could not be copied.",
        )
        self._guarded(
            lambda: self._client.delete_object(Bucket=self._bucket, Key=key),
            "The source object could not be deleted.",
        )

    @staticmethod
    def _guarded(call: Any, message: str) -> None:
        try:
            call()
        except (ClientError, BotoCoreError) as error:
            raise InfrastructureError(message, error) from error
