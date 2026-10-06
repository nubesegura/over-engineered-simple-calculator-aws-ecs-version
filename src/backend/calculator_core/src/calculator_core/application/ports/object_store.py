from typing import Literal, Protocol

from calculator_core.domain.errors import InvalidInputError

Outcome = Literal["processed", "rejected"]


class ObjectTooLargeError(InvalidInputError):
    """The object is larger than the limit given to `get`."""

    code = "OBJECT_TOO_LARGE"


class ObjectNotFoundError(InvalidInputError):
    """The object does not exist (for example an event delivered twice)."""

    code = "OBJECT_NOT_FOUND"


class ObjectStore(Protocol):
    def get(self, key: str, max_bytes: int) -> bytes:
        """Read an object without reading more than `max_bytes` (+ 1) bytes.

        Raises `ObjectTooLargeError` above the limit, `ObjectNotFoundError` when it does
        not exist and `InfrastructureError` for any other failure.
        """

    def put_report(self, source_key: str, report: str) -> None:
        """Write the JSON report of `source_key` under `reports/`; overwrites a previous one."""

    def move(self, key: str, outcome: Outcome) -> None:
        """Move the object to `processed/` or `rejected/` (copy, then delete the source)."""
