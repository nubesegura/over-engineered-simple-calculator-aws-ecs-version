import base64
import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from starlette.testclient import TestClient

from calculator_core.adapters.inbound.http_app import (
    CalculationRoute,
    HttpAppConfig,
    create_app,
    subject_from_authorization,
)
from calculator_core.adapters.outbound.in_memory_repository import InMemoryCalculationRepository
from calculator_core.application.ports.error_notifier import InternalErrorReport
from calculator_core.application.use_cases.calculate import Calculate
from calculator_core.application.use_cases.read_history import ReadHistory
from calculator_core.config.observability import JsonFormatter, set_request_context
from calculator_core.domain.operation import Operation
from tests.fakes import (
    FailingRepository,
    FixedClock,
)

ORIGIN = "https://app.example.test"
PREFIXES = ("/api/v1", "/api/ecs/v1")
BACKEND = "ecs"


class RecordingNotifier:
    def __init__(self, fail: bool = False) -> None:
        self.reports: list[InternalErrorReport] = []
        self.fail = fail

    def notify(self, report: InternalErrorReport) -> None:
        self.reports.append(report)
        if self.fail:
            raise RuntimeError("notifier down")


class SequentialIds:
    def __init__(self) -> None:
        self.count = 0

    def new_id(self) -> Any:
        from uuid import UUID

        self.count += 1
        return UUID(int=self.count)


def make_client(
    operation: Operation | None = None,
    history: bool = False,
    repository: Any = None,
    notifier: RecordingNotifier | None = None,
) -> TestClient:
    repository = repository or InMemoryCalculationRepository()
    calculation = (
        CalculationRoute(operation, Calculate(repository, FixedClock(), SequentialIds()))
        if operation
        else None
    )
    app = create_app(
        HttpAppConfig("calc-test", "dev", ORIGIN, BACKEND),
        notifier or RecordingNotifier(),
        set_request_context,
        calculation=calculation,
        history=ReadHistory(repository) if history else None,
    )
    return TestClient(app, raise_server_exceptions=False)


def error_of(response: Any) -> dict[str, Any]:
    error = response.json()["error"]
    assert set(error) == {"code", "message", "request_id"}
    assert error["request_id"]
    return error  # type: ignore[no-any-return]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize(
    ("operation", "a", "b", "result"),
    [
        (Operation.ADD, "0.1", "0.2", "0.3"),
        (Operation.SUB, 5, 7, "-2"),
        (Operation.MUL, "1.5", 2, "3.0"),
        (Operation.DIV, 10, "0.5", "20"),
    ],
)
def test_every_operation_answers_under_both_prefixes(
    prefix: str, operation: Operation, a: object, b: object, result: str
) -> None:
    client = make_client(operation)

    response = client.post(f"{prefix}/{operation.value}", json={"a": a, "b": b})

    assert response.status_code == 200
    body = response.json()
    assert body["result"] == result
    assert body["operation"] == operation.value
    assert set(body) == {"calculation_id", "operation", "a", "b", "result", "backend"}
    assert body["backend"] == BACKEND


def test_float_operands_keep_their_decimal_value() -> None:
    client = make_client(Operation.ADD)

    response = client.post("/api/v1/add", content=b'{"a": 0.1, "b": 0.2}')

    assert response.json()["result"] == "0.3"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"", "VALIDATION_ERROR"),
        (b"not json", "VALIDATION_ERROR"),
        (b"[1, 2]", "VALIDATION_ERROR"),
        (b"[" * 3000, "VALIDATION_ERROR"),
        (b'{"a": 1}', "VALIDATION_ERROR"),
        (b'{"a": true, "b": 1}', "INVALID_OPERAND"),
        (b'{"a": "abc", "b": 1}', "INVALID_OPERAND"),
        (b'{"a": 1e999, "b": 1}', "INVALID_OPERAND"),
        (b'{"a": 1e1000000, "b": 1}', "INVALID_OPERAND"),
    ],
)
def test_malformed_requests_are_400_with_the_sls_codes(payload: bytes, code: str) -> None:
    response = make_client(Operation.ADD).post("/api/v1/add", content=payload)

    assert response.status_code == 400
    assert error_of(response)["code"] == code


def test_division_by_zero_is_400_with_its_domain_code() -> None:
    response = make_client(Operation.DIV).post("/api/v1/div", json={"a": 1, "b": 0})

    assert response.status_code == 400
    assert error_of(response)["code"] == "DIVISION_BY_ZERO"


def test_oversized_body_is_rejected_before_any_processing() -> None:
    repository = InMemoryCalculationRepository()
    client = make_client(Operation.ADD, repository=repository)
    padding = "1" * 5000

    response = client.post("/api/v1/add", content=f'{{"a": 1, "b": 2, "x": "{padding}"}}')

    assert response.status_code == 400
    assert error_of(response)["code"] == "VALIDATION_ERROR"
    assert repository.rows == {}


def test_each_service_mounts_only_its_own_routes() -> None:
    add_client = make_client(Operation.ADD)
    history_client = make_client(history=True)

    assert add_client.post("/api/v1/sub", json={"a": 1, "b": 1}).status_code == 404
    assert add_client.get("/api/v1/history").status_code == 404
    assert history_client.post("/api/ecs/v1/add", json={"a": 1, "b": 1}).status_code == 404
    assert history_client.get("/api/ecs/v1/history").status_code == 200


def test_404_and_405_use_the_error_shape_and_no_internals() -> None:
    client = make_client(Operation.ADD)

    not_found = client.get("/nowhere")
    wrong_method = client.get("/api/v1/add")

    assert not_found.status_code == 404
    assert error_of(not_found)["code"] == "NOT_FOUND"
    assert wrong_method.status_code == 405
    assert error_of(wrong_method)["code"] == "METHOD_NOT_ALLOWED"
    assert wrong_method.headers["allow"]


def test_history_round_trip_with_pagination_and_query_errors() -> None:
    repository = InMemoryCalculationRepository()
    calculate = make_client(Operation.ADD, repository=repository)
    for _ in range(3):
        calculate.post("/api/v1/add", json={"a": 1, "b": 2})
    client = make_client(history=True, repository=repository)

    first = client.get("/api/v1/history", params={"limit": 2}).json()
    second = client.get("/api/v1/history", params={"cursor": first["next_cursor"]}).json()

    assert len(first["items"]) == 2
    assert first["next_cursor"]
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
    assert "backend" not in first["items"][0]
    assert set(first["items"][0]) == {
        "calculation_id",
        "operation",
        "a",
        "b",
        "result",
        "occurred_at",
    }
    for params in ({"limit": "x"}, {"limit": "0"}, {"cursor": "garbage"}):
        response = client.get("/api/v1/history", params=params)
        assert response.status_code == 400
        assert error_of(response)["code"] == "INVALID_QUERY"


def test_health_needs_no_token_and_lives_outside_the_contract_prefixes() -> None:
    for client in (make_client(Operation.MUL), make_client(history=True)):
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_preflight_is_answered_without_a_token_with_the_configured_cors() -> None:
    client = make_client(Operation.ADD)

    response = client.options("/api/ecs/v1/add", headers={"Origin": ORIGIN})

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "Authorization" in response.headers["access-control-allow-headers"]


def test_allowed_origin_is_on_success_4xx_and_5xx() -> None:
    client = make_client(Operation.ADD, repository=FailingRepository(RuntimeError("db down")))

    responses = [
        client.get("/health"),
        client.post("/api/v1/add", content=b"nope"),
        client.get("/nowhere"),
        client.get("/api/v1/add"),
        client.post("/api/v1/add", json={"a": 1, "b": 2}),
        client.options("/nowhere"),
    ]

    assert [r.status_code for r in responses] == [200, 400, 404, 405, 500, 404]
    assert {r.headers["access-control-allow-origin"] for r in responses} == {ORIGIN}


def test_unexpected_error_is_generic_500_notified_once_without_internals() -> None:
    notifier = RecordingNotifier()
    client = make_client(
        Operation.ADD,
        repository=FailingRepository(RuntimeError("password=hunter2")),
        notifier=notifier,
    )

    response = client.post("/api/v1/add", json={"a": 1, "b": 2})

    assert response.status_code == 500
    assert error_of(response)["code"] == "INTERNAL_ERROR"
    assert "hunter2" not in response.text
    assert [r.error_type for r in notifier.reports] == ["InfrastructureError"]
    assert notifier.reports[0].correlation_id == error_of(response)["request_id"]
    assert "hunter2" not in notifier.reports[0].error_message


def test_a_failing_notifier_never_breaks_the_response() -> None:
    client = make_client(
        Operation.ADD,
        repository=FailingRepository(RuntimeError("x")),
        notifier=RecordingNotifier(fail=True),
    )

    response = client.post("/api/v1/add", json={"a": 1, "b": 2})

    assert response.status_code == 500
    assert error_of(response)["message"] == "An internal error occurred."


def jwt_with(payload: dict[str, object]) -> str:
    def part(data: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part(payload)}.c2lnbmF0dXJl"


@pytest.fixture
def access_log() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("calc-test", "dev"))
    logger = logging.getLogger("calculator_core.access")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield stream
    logger.removeHandler(handler)


def test_access_log_has_the_unverified_sub_and_never_the_token(access_log: io.StringIO) -> None:
    token = jwt_with({"sub": "user-123", "aud": "whatever"})
    client = make_client(Operation.ADD)

    client.post("/api/v1/add", json={"a": 1, "b": 2}, headers={"Authorization": f"Bearer {token}"})
    client.get("/health")

    first, second = (json.loads(line) for line in access_log.getvalue().splitlines())
    assert first["sub"] == "user-123"
    assert "POST /api/v1/add 200" in first["message"]
    assert "sub" not in second
    assert token not in access_log.getvalue()
    assert token.split(".")[1] not in access_log.getvalue()


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Basic abc",
        "Bearer notatoken",
        "Bearer a.b.c",
        f"Bearer {jwt_with({'no': 'sub'})}",
        f"Bearer {jwt_with({'sub': 42})}",
    ],
)
def test_sub_is_none_when_the_header_does_not_carry_one(header: str | None) -> None:
    assert subject_from_authorization(header) is None


def test_sub_is_truncated() -> None:
    header = f"bearer {jwt_with({'sub': 's' * 500})}"

    assert len(subject_from_authorization(header) or "") == 128


def _messages(caplog: pytest.LogCaptureFixture, prefix: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith(prefix)]


def test_request_and_response_are_logged_without_headers_or_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    token = jwt_with({"sub": "user-123", "email": "someone@example.test"})
    client = make_client(Operation.ADD)

    response = client.post(
        "/api/v1/add", json={"a": 1, "b": 2}, headers={"Authorization": f"Bearer {token}"}
    )

    [received] = _messages(caplog, "Request received")
    [sent] = _messages(caplog, "Response sent")
    assert received.levelno == logging.INFO
    assert "method=POST path=/api/v1/add" in received.getMessage()
    assert 'body=\'{"a":1,"b":2}\'' in received.getMessage()
    assert "request_id=" in received.getMessage()
    assert "status=200" in sent.getMessage()
    assert response.json()["calculation_id"] in sent.getMessage()
    assert '"backend":"ecs"' in sent.getMessage()
    everything = " ".join(r.getMessage() for r in caplog.records)
    for secret in (token, token.split(".")[1], "Bearer", "someone@example.test", "authorization"):
        assert secret not in everything


def test_history_request_logs_the_query_and_health_is_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    client = make_client(history=True)

    client.get("/health")
    client.get("/api/v1/history", params={"limit": 2})

    [received] = _messages(caplog, "Request received")
    assert "method=GET path=/api/v1/history" in received.getMessage()
    assert "query='limit=2'" in received.getMessage()
    assert len(_messages(caplog, "Response sent")) == 1


@pytest.mark.parametrize(
    ("operation", "payload", "reason"),
    [
        (Operation.ADD, b'{"a": "abc", "b": 1}', "Field 'a' must be a number."),
        (Operation.ADD, b'{"a": 1}', "Field 'b' is required."),
        (Operation.ADD, b"not json", "Request body must be valid JSON."),
        (Operation.DIV, b'{"a": 1, "b": 0}', "DIVISION_BY_ZERO"),
    ],
)
def test_validation_failures_are_warnings_with_reason_and_received_fields(
    caplog: pytest.LogCaptureFixture, operation: Operation, payload: bytes, reason: str
) -> None:
    caplog.set_level(logging.INFO)

    response = make_client(operation).post(f"/api/v1/{operation.value}", content=payload)

    assert response.status_code == 400
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning.getMessage().startswith("Request rejected")
    assert reason in warning.getMessage()
    assert payload.decode() in warning.getMessage()
    assert "status=400" in _messages(caplog, "Response sent")[0].getMessage()


def test_oversized_body_is_a_warning_and_the_body_is_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    padding = "9" * 5000

    make_client(Operation.ADD).post("/api/v1/add", content=f'{{"a": 1, "x": "{padding}"}}')

    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "too large" in warning.getMessage()
    assert padding not in " ".join(r.getMessage() for r in caplog.records)


def test_formatted_error_log_has_frames_but_never_the_exception_message() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("calc-test", "dev"))
    logger = logging.getLogger("calculator_core.adapters.inbound.http_app")
    logger.addHandler(handler)
    try:
        client = make_client(
            Operation.ADD, repository=FailingRepository(RuntimeError("db password=hunter2"))
        )
        client.post("/api/v1/add", json={"a": 1, "b": 2})
    finally:
        logger.removeHandler(handler)

    errors = [json.loads(line) for line in stream.getvalue().splitlines()]
    [error] = [entry for entry in errors if entry["level"] == "ERROR"]
    assert error["frames"]
    assert "hunter2" not in stream.getvalue()
    assert "password" not in stream.getvalue()


def test_newlines_in_the_body_are_escaped_in_the_request_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    make_client(Operation.ADD).post("/api/v1/add", content=b'{"a": 1,\n"b": "x\nfake"}')

    [received] = _messages(caplog, "Request received")
    assert "\n" not in received.getMessage()
    assert "\\n" in received.getMessage()


def test_unexpected_error_is_logged_as_error_with_the_stack_trace(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    client = make_client(Operation.ADD, repository=FailingRepository(RuntimeError("db down")))

    response = client.post("/api/v1/add", json={"a": 1, "b": 2})

    assert response.status_code == 500
    [error] = [r for r in caplog.records if r.levelno == logging.ERROR and r.exc_info]
    assert error.exc_info is not None
    assert error.exc_info[2] is not None
    assert "status=500" in _messages(caplog, "Response sent")[0].getMessage()
    assert "Traceback" not in response.text
