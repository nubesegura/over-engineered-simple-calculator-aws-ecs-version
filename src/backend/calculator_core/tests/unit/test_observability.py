import json
import logging

from calculator_core.application.errors import InfrastructureError
from calculator_core.config.observability import JsonFormatter, set_request_context


def _format(record: logging.LogRecord) -> dict[str, object]:
    result: dict[str, object] = json.loads(JsonFormatter("calc-add", "dev").format(record))
    return result


def _record(message: str, exc_info: tuple[type[BaseException], BaseException, None] | None = None):  # type: ignore[no-untyped-def]
    return logging.LogRecord("test", logging.ERROR, __file__, 1, message, None, exc_info)


def test_entry_carries_correlation_id_and_sub_when_set() -> None:
    set_request_context("corr-1", "user-sub")

    entry = _format(_record("hello"))

    assert entry["correlation_id"] == "corr-1"
    assert entry["sub"] == "user-sub"
    assert entry["service"] == "calc-add"
    set_request_context(None)


def _raise_wrapped() -> BaseException:
    def inner() -> None:
        raise ConnectionError("password=hunter2")

    try:
        try:
            inner()
        except ConnectionError as cause:
            raise InfrastructureError("token=abc123", cause) from cause
    except InfrastructureError as error:
        return error
    raise AssertionError("unreachable")


def test_entry_has_cause_type_and_frames_but_no_message_or_value() -> None:
    error = _raise_wrapped()

    entry = _format(_record("failed", (type(error), error, error.__traceback__)))  # type: ignore[arg-type]

    assert entry["exception_type"] == "InfrastructureError"
    assert entry["cause_type"] == "ConnectionError"
    frames = entry["frames"]
    assert isinstance(frames, list)
    assert set(frames[-1]) == {"file", "line", "function"}
    assert frames[-1]["function"] == "_raise_wrapped"
    assert frames[-1]["file"].endswith("test_observability.py")
    dumped = json.dumps(entry)
    assert "hunter2" not in dumped
    assert "abc123" not in dumped
    assert "raise " not in dumped


def test_entry_omits_context_when_absent_and_logs_exception_type_only() -> None:
    set_request_context(None)
    error = ValueError("password=hunter2")

    entry = _format(_record("failed", (ValueError, error, None)))

    assert "sub" not in entry
    assert "correlation_id" not in entry
    assert entry["exception_type"] == "ValueError"
    assert "hunter2" not in json.dumps(entry)
