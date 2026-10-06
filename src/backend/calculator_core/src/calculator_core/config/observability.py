"""Structured JSON logging with a correlation id and the caller's `sub` claim.

Exceptions are logged by type, cause type and traceback frames (file, line, function) only:
messages of driver or SDK errors are not guaranteed to be free of sensitive values, and
frames hold no values.
"""

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from types import TracebackType

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_subject: ContextVar[str | None] = ContextVar("subject", default=None)


def set_request_context(correlation_id: str | None, subject: str | None = None) -> None:
    _correlation_id.set(correlation_id)
    _subject.set(subject)


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def _exception_fields(
    error: BaseException | None, trace: TracebackType | None
) -> dict[str, object]:
    fields: dict[str, object] = {"exception_type": type(error).__name__}
    cause = getattr(error, "original_error", None) or getattr(error, "__cause__", None)
    if cause is not None:
        fields["cause_type"] = type(cause).__name__
    fields["frames"] = [
        {"file": frame.filename, "line": frame.lineno, "function": frame.name}
        for frame in traceback.extract_tb(trace)
    ]
    return fields


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, environment: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service,
            "environment": self._environment,
        }
        correlation_id, subject = _correlation_id.get(), _subject.get()
        if correlation_id:
            entry["correlation_id"] = correlation_id
        if subject:
            entry["sub"] = subject
        if record.exc_info and record.exc_info[0] is not None:
            entry.update(_exception_fields(record.exc_info[1], record.exc_info[2]))
        return json.dumps(entry)


def configure_logging(service: str, environment: str, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service, environment))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
