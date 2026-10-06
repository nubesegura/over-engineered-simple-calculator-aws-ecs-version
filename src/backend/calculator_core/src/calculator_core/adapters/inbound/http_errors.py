"""Error translation of the HTTP adapter: one JSON shape, no internals."""

from starlette.responses import JSONResponse

from calculator_core.domain.errors import InvalidInputError

GENERIC_MESSAGE = "An internal error occurred."
_STATUS_MESSAGES = {404: "Resource not found.", 405: "Method not allowed."}
_STATUS_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


class RequestValidationError(InvalidInputError):
    """The request does not have the expected shape (invalid JSON, missing fields, size)."""

    code = "VALIDATION_ERROR"


def error_response(
    status: int,
    code: str,
    message: str,
    request_id: str | None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "request_id": request_id}}
    return JSONResponse(body, status_code=status, headers=headers)


def http_status_response(
    status: int, request_id: str | None, headers: dict[str, str] | None = None
) -> JSONResponse:
    """Response for the router's own 404 and 405."""
    return error_response(
        status,
        _STATUS_CODES.get(status, "HTTP_ERROR"),
        _STATUS_MESSAGES.get(status, "The request could not be served."),
        request_id,
        headers,
    )


def input_error_response(error: InvalidInputError, request_id: str | None) -> JSONResponse:
    return error_response(400, error.code, str(error), request_id)


def internal_error_response(request_id: str | None) -> JSONResponse:
    return error_response(500, "INTERNAL_ERROR", GENERIC_MESSAGE, request_id)
