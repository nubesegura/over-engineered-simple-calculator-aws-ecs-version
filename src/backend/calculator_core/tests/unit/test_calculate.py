from decimal import Decimal

import pytest

from calculator_core.adapters.outbound.in_memory_repository import InMemoryCalculationRepository
from calculator_core.application.errors import InfrastructureError
from calculator_core.application.use_cases.calculate import Calculate, CalculateCommand
from calculator_core.domain.errors import (
    DivisionByZeroError,
    InvalidInputError,
    InvalidOperandError,
)
from calculator_core.domain.operation import Operation
from tests.fakes import (
    FIXED_ID,
    FIXED_NOW,
    FailingRepository,
    FixedClock,
    FixedIdGenerator,
)


def _use_case(repository: object | None = None) -> tuple[Calculate, InMemoryCalculationRepository]:
    repo = InMemoryCalculationRepository()
    use_case = Calculate(
        repository=repository or repo,  # type: ignore[arg-type]
        clock=FixedClock(),
        id_generator=FixedIdGenerator(),
    )
    return use_case, repo


def _run(operation: Operation, a: str, b: str) -> Decimal:
    use_case, _ = _use_case()
    return use_case.execute(CalculateCommand(operation, Decimal(a), Decimal(b))).result


@pytest.mark.parametrize(
    ("operation", "a", "b", "expected"),
    [
        (Operation.ADD, "5", "3", "8"),
        (Operation.SUB, "5", "3", "2"),
        (Operation.MUL, "5", "3", "15"),
        (Operation.DIV, "6", "3", "2"),
        (Operation.ADD, "0.1", "0.2", "0.3"),
        (Operation.DIV, "1", "3", "0.3333333333333333333333333333"),
        (Operation.ADD, "1e15", "-1e15", "0"),
    ],
)
def test_each_operation_uses_decimal_arithmetic(
    operation: Operation, a: str, b: str, expected: str
) -> None:
    assert _run(operation, a, b) == Decimal(expected)


def test_success_builds_the_calculation_and_saves_it_before_returning() -> None:
    use_case, repo = _use_case()

    calculation = use_case.execute(
        CalculateCommand(Operation.MUL, Decimal("2"), Decimal("4"), correlation_id="req-1")
    )

    assert calculation.id == FIXED_ID
    assert calculation.occurred_at == FIXED_NOW
    assert calculation.correlation_id == "req-1"
    assert repo.rows[FIXED_ID] == calculation


@pytest.mark.parametrize(
    "operand",
    ["NaN", "Infinity", "1000000000000000.1", "-1e16", "0.1234567890123456"],
)
def test_invalid_operands_are_input_errors_and_nothing_is_saved(operand: str) -> None:
    use_case, repo = _use_case()

    with pytest.raises(InvalidOperandError):
        use_case.execute(CalculateCommand(Operation.ADD, Decimal(operand), Decimal("1")))

    assert repo.rows == {}


def test_limits_are_inclusive() -> None:
    assert _run(Operation.ADD, "-1e15", "0.5") == Decimal("-999999999999999.5")


def test_division_by_zero_has_the_sls_code() -> None:
    use_case, repo = _use_case()

    with pytest.raises(DivisionByZeroError) as caught:
        use_case.execute(CalculateCommand(Operation.DIV, Decimal("1"), Decimal("0")))

    assert caught.value.code == "DIVISION_BY_ZERO"
    assert isinstance(caught.value, InvalidInputError)
    assert repo.rows == {}


def test_save_failure_becomes_infrastructure_error_and_returns_nothing() -> None:
    cause = RuntimeError("connection lost")
    use_case, _ = _use_case(FailingRepository(cause))

    with pytest.raises(InfrastructureError) as caught:
        use_case.execute(CalculateCommand(Operation.ADD, Decimal("1"), Decimal("2")))

    assert caught.value.original_error is cause
    assert "connection lost" not in str(caught.value)


def test_an_error_already_in_the_taxonomy_is_not_wrapped_again() -> None:
    error = InfrastructureError("pool exhausted")
    use_case, _ = _use_case(FailingRepository(error))

    with pytest.raises(InfrastructureError) as caught:
        use_case.execute(CalculateCommand(Operation.ADD, Decimal("1"), Decimal("2")))

    assert caught.value is error
