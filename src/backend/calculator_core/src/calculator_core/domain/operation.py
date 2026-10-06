from decimal import Decimal
from enum import Enum

from calculator_core.domain.errors import DivisionByZeroError, InvalidOperandError

# Business limits for the operands. Bounding both the magnitude and the number of decimal
# places keeps every result small: without the second limit a 12-byte operand such as
# 1e-5000000 is printed in fixed notation as a 5-million-digit string.
MAX_ABS_OPERAND = Decimal("1e15")
MAX_DECIMAL_PLACES = 15


def validate_operand(value: Decimal) -> Decimal:
    """Domain rule: an operand must be finite, within range and not overly precise."""
    if not value.is_finite():
        raise InvalidOperandError("Operands must be finite numbers.")
    # copy_abs never signals, unlike abs(), which overflows for exponents such as 1e1000000.
    if value.copy_abs() > MAX_ABS_OPERAND:
        raise InvalidOperandError(
            f"Operands must be between -{MAX_ABS_OPERAND} and {MAX_ABS_OPERAND}."
        )
    # The raw exponent (not the normalized one) is checked on purpose: "1.000...0" with
    # thousands of zeros is mathematically 1, but it would be echoed back verbatim.
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -MAX_DECIMAL_PLACES:
        raise InvalidOperandError(
            f"Operands must have at most {MAX_DECIMAL_PLACES} decimal places."
        )
    return value


class Operation(Enum):
    """Supported operations. Each member knows how to compute itself (Strategy pattern)."""

    ADD = "add"
    SUB = "sub"
    MUL = "mul"
    DIV = "div"

    def calculate(self, a: Decimal, b: Decimal) -> Decimal:
        """Validate both operands and apply the operation with `Decimal` arithmetic.

        Operands within the limits cannot overflow the default `Decimal` context.
        """
        validate_operand(a)
        validate_operand(b)
        match self:
            case Operation.ADD:
                return a + b
            case Operation.SUB:
                return a - b
            case Operation.MUL:
                return a * b
            case Operation.DIV:
                if b == 0:
                    raise DivisionByZeroError()
                return a / b
