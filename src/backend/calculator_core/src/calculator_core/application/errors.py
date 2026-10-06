from calculator_core.domain.errors import CalculatorError, InvalidInputError


class InfrastructureError(CalculatorError):
    """An adapter failed (network, database). Translated to 500 and notified."""

    code = "INTERNAL_ERROR"

    def __init__(self, message: str, original_error: Exception | None = None) -> None:
        super().__init__(message)
        self.original_error = original_error


class InvalidQueryError(InvalidInputError):
    """Invalid query parameters (limit). Translated to 400."""

    code = "INVALID_QUERY"
