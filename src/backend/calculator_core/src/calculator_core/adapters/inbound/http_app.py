"""HTTP inbound adapter (Starlette) with the request and response contract of the `sls` backend.

`create_app` mounts only the routes of one service, under both contract prefixes. The load
balancer already verified the bearer token, so this adapter never verifies it: it only
decodes the `sub` claim for the access log and never logs the token itself.

Cross-cutting behavior lives in one ASGI middleware so that it also covers the router's own
404 and 405 and any unexpected failure: request id, CORS preflight (answered without a token),
the allowed-origin header on every response, error translation and the access log.
"""

import base64
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from calculator_core.adapters.inbound.http_errors import (
    RequestValidationError,
    http_status_response,
    input_error_response,
    internal_error_response,
)
from calculator_core.adapters.serialization import calculation_response_to_json, history_to_json
from calculator_core.application.errors import InvalidQueryError
from calculator_core.application.ports.error_notifier import ErrorNotifier, InternalErrorReport
from calculator_core.application.use_cases.calculate import Calculate, CalculateCommand
from calculator_core.application.use_cases.read_history import (
    DEFAULT_LIMIT,
    ReadHistory,
    ReadHistoryQuery,
)
from calculator_core.domain.errors import CalculatorError, InvalidInputError, InvalidOperandError
from calculator_core.domain.operation import Operation

logger = logging.getLogger(__name__)
access_logger = logging.getLogger("calculator_core.access")

# Puts the request id and the caller's `sub` in the log context (injected: adapters cannot
# import the `config` layer where the logging helpers live).
LogContextSetter = Callable[[str | None, str | None], None]

API_PREFIXES = ("/api/v1", "/api/ecs/v1")
HEALTH_PATH = "/health"
MAX_BODY_BYTES = 4096
_MAX_SUBJECT_CHARS = 128
MAX_LOGGED_CHARS = 2000
_TRACE_ROOT = re.compile(r"Root=([0-9A-Za-z-]{1,64})")


@dataclass(frozen=True)
class HttpAppConfig:
    service_name: str
    environment: str
    cors_allowed_origin: str
    backend_name: str
    cors_allowed_methods: str = "GET, POST, OPTIONS"
    cors_allowed_headers: str = "Content-Type, Authorization"
    cors_max_age_seconds: int = 600


@dataclass(frozen=True)
class CalculationRoute:
    operation: Operation
    use_case: Calculate


def create_app(
    config: HttpAppConfig,
    notifier: ErrorNotifier,
    set_log_context: LogContextSetter,
    calculation: CalculationRoute | None = None,
    history: ReadHistory | None = None,
) -> Starlette:
    """Build the ASGI app of one service: its routes, `/health` and the contract middleware."""
    routes = [Route(HEALTH_PATH, _health, methods=["GET"])]
    contract_paths: set[str] = set()
    for prefix in API_PREFIXES:
        if calculation is not None:
            path = f"{prefix}/{calculation.operation.value}"
            routes.append(
                Route(
                    path, _calculation_endpoint(calculation, config.backend_name), methods=["POST"]
                )
            )
            contract_paths.add(path)
        if history is not None:
            path = f"{prefix}/history"
            routes.append(Route(path, _history_endpoint(history), methods=["GET"]))
            contract_paths.add(path)

    async def status_handler(request: Request, error: Exception) -> Response:
        # Registered for 404 and 405 only, which Starlette raises as HTTPException.
        status = error.status_code if isinstance(error, HTTPException) else 500
        headers = (
            dict(error.headers) if isinstance(error, HTTPException) and error.headers else None
        )
        return http_status_response(status, _request_id_of(request.scope), headers)

    # User middleware sits inside Starlette's server-error layer, so unexpected errors reach it
    # first and every response (including the router's own 404 and 405) passes through it.
    middleware = Middleware(
        _ContractMiddleware,
        config=config,
        notifier=notifier,
        set_log_context=set_log_context,
        preflight_paths=frozenset(contract_paths),
    )
    return Starlette(
        routes=routes,
        middleware=[middleware],
        exception_handlers={404: status_handler, 405: status_handler},
    )


def _request_id_of(scope: Scope) -> str | None:
    request_id = (scope.get("state") or {}).get("request_id")
    return request_id if isinstance(request_id, str) else None


async def _health(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


def _calculation_endpoint(
    route: CalculationRoute, backend_name: str
) -> Callable[[Request], Awaitable[Response]]:
    async def endpoint(request: Request) -> Response:
        body = _parse_body(await _read_logged_body(request))
        command = CalculateCommand(
            operation=route.operation,
            operand_a=_operand(body, "a"),
            operand_b=_operand(body, "b"),
            correlation_id=_request_id_of(request.scope),
        )
        calculation = await run_in_threadpool(route.use_case.execute, command)
        return JSONResponse(calculation_response_to_json(calculation, backend_name))

    return endpoint


def _history_endpoint(use_case: ReadHistory) -> Callable[[Request], Awaitable[Response]]:
    async def endpoint(request: Request) -> Response:
        _log_request(request, "query", request.url.query)
        params = request.query_params
        query = ReadHistoryQuery(limit=_limit(params.get("limit")), cursor=params.get("cursor"))
        page = await run_in_threadpool(use_case.execute, query)
        return JSONResponse(history_to_json(page))

    return endpoint


def _clip(text: str) -> str:
    return text if len(text) <= MAX_LOGGED_CHARS else text[:MAX_LOGGED_CHARS] + "...(truncated)"


def _log_request(request: Request, kind: str, received: str) -> None:
    """Log what came in (never headers) and keep it for the warning of a rejected request."""
    clipped = _clip(received)
    request.scope.setdefault("state", {})["received"] = clipped
    logger.info(
        "Request received: method=%s path=%s request_id=%s %s=%r",
        request.method,
        request.url.path,
        _request_id_of(request.scope),
        kind,
        clipped,
    )


async def _read_logged_body(request: Request) -> bytes:
    try:
        raw = await _read_body(request)
    except RequestValidationError:
        _log_request(request, "body", "<not read: too large>")
        raise
    _log_request(request, "body", raw.decode("utf-8", errors="replace"))
    return raw


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise RequestValidationError("Request body is too large.")
    received = bytearray()
    async for chunk in request.stream():
        received += chunk
        if len(received) > MAX_BODY_BYTES:
            raise RequestValidationError("Request body is too large.")
    return bytes(received)


def _parse_body(raw: bytes) -> dict[str, Any]:
    if not raw:
        raise RequestValidationError("Request body is required.")
    try:
        # parse_float=Decimal keeps 0.1 from reaching the domain as 0.1000000000000000055...
        body = json.loads(raw, parse_float=Decimal)
    except (ValueError, RecursionError) as error:
        raise RequestValidationError("Request body must be valid JSON.") from error
    if not isinstance(body, dict):
        raise RequestValidationError("Request body must be a JSON object.")
    return body


def _operand(body: dict[str, Any], name: str) -> Decimal:
    if name not in body:
        raise RequestValidationError(f"Field '{name}' is required.")
    raw = body[name]
    # bool is a subclass of int in Python: true/false are not valid operands.
    if isinstance(raw, bool) or not isinstance(raw, int | float | str | Decimal):
        raise InvalidOperandError(f"Field '{name}' must be a number.")
    try:
        return Decimal(str(raw).strip())
    except InvalidOperation as error:
        raise InvalidOperandError(f"Field '{name}' must be a number.") from error


def _limit(raw: str | None) -> int:
    if raw is None:
        return DEFAULT_LIMIT
    try:
        return int(raw)
    except ValueError as error:
        raise InvalidQueryError("limit must be an integer.") from error


def subject_from_authorization(header: str | None) -> str | None:
    """Return the `sub` claim of a bearer token WITHOUT verifying it (for the access log only)."""
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    parts = token.strip().split(".")
    if scheme.lower() != "bearer" or len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError:
        return None
    subject = payload.get("sub") if isinstance(payload, dict) else None
    return subject[:_MAX_SUBJECT_CHARS] if isinstance(subject, str) and subject else None


def _log_response(status: int, request_id: str, body: bytearray) -> None:
    logger.info(
        "Response sent: status=%d request_id=%s body=%s",
        status,
        request_id,
        _clip(body.decode("utf-8", errors="replace")),
    )


def _new_request_id(headers: Headers) -> str:
    """Reuse the trace root of the load balancer so one id follows the request."""
    match = _TRACE_ROOT.search(headers.get("x-amzn-trace-id", ""))
    return match.group(1) if match else uuid.uuid4().hex


class _ContractMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        config: HttpAppConfig,
        notifier: ErrorNotifier,
        set_log_context: LogContextSetter,
        preflight_paths: frozenset[str],
    ) -> None:
        self._app = app
        self._config = config
        self._notifier = notifier
        self._set_log_context = set_log_context
        self._preflight_paths = preflight_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        request_id = _new_request_id(headers)
        scope.setdefault("state", {})["request_id"] = request_id
        subject = subject_from_authorization(headers.get("authorization"))
        self._set_log_context(request_id, subject)
        started = time.perf_counter()
        status = 500
        logged = scope["path"] != HEALTH_PATH
        body = bytearray()

        async def send_with_cors(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["Access-Control-Allow-Origin"] = (
                    self._config.cors_allowed_origin
                )
            elif message["type"] == "http.response.body" and logged:
                body.extend(message.get("body", b"")[: MAX_LOGGED_CHARS * 4])
                if not message.get("more_body", False):
                    _log_response(status, request_id, body)
            await send(message)

        try:
            await self._dispatch(scope, receive, send_with_cors, request_id)
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            access_logger.info("%s %s %s %sms", scope["method"], scope["path"], status, elapsed_ms)
            self._set_log_context(None, None)

    async def _dispatch(self, scope: Scope, receive: Receive, send: Send, request_id: str) -> None:
        if scope["method"] == "OPTIONS" and scope["path"] in self._preflight_paths:
            await self._preflight()(scope, receive, send)
            return
        response_started = False

        async def track(message: Message) -> None:
            nonlocal response_started
            response_started = response_started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self._app(scope, receive, track)
        except Exception as error:
            if response_started:
                raise
            await self._error_response(error, request_id, scope)(scope, receive, send)

    def _preflight(self) -> Response:
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Methods": self._config.cors_allowed_methods,
                "Access-Control-Allow-Headers": self._config.cors_allowed_headers,
                "Access-Control-Max-Age": str(self._config.cors_max_age_seconds),
            },
        )

    def _error_response(self, error: Exception, request_id: str, scope: Scope) -> Response:
        if isinstance(error, InvalidInputError):
            logger.warning(
                "Request rejected: code=%s reason=%s request_id=%s received=%r",
                error.code,
                error,
                request_id,
                (scope.get("state") or {}).get("received", ""),
            )
            return input_error_response(error, request_id)
        logger.exception(
            "Internal error while processing the request: method=%s path=%s request_id=%s",
            scope["method"],
            scope["path"],
            request_id,
        )
        self._notify(error, request_id)
        return internal_error_response(request_id)

    def _notify(self, error: Exception, request_id: str) -> None:
        # Only our own messages are forwarded: driver and SDK messages may carry sensitive values.
        message = str(error) if isinstance(error, CalculatorError) else "Unexpected error."
        report = InternalErrorReport(
            service=self._config.service_name,
            environment=self._config.environment,
            error_type=type(error).__name__,
            error_message=message,
            correlation_id=request_id,
        )
        try:
            self._notifier.notify(report)
        except Exception:
            logger.exception("The error notification failed")
