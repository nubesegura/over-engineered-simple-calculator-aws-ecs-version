from dataclasses import dataclass
from decimal import Decimal

from calculator_core.application.errors import InfrastructureError
from calculator_core.application.ports.calculation_repository import CalculationRepository
from calculator_core.application.ports.system import Clock, IdGenerator
from calculator_core.domain.calculation import Calculation
from calculator_core.domain.errors import CalculatorError
from calculator_core.domain.operation import Operation


@dataclass(frozen=True)
class CalculateCommand:
    operation: Operation
    operand_a: Decimal
    operand_b: Decimal
    correlation_id: str | None = None


class Calculate:
    def __init__(
        self, repository: CalculationRepository, clock: Clock, id_generator: IdGenerator
    ) -> None:
        self.repository = repository
        self.clock = clock
        self.id_generator = id_generator

    def execute(self, command: CalculateCommand) -> Calculation:
        result = command.operation.calculate(command.operand_a, command.operand_b)
        calculation = Calculation(
            id=self.id_generator.new_id(),
            operation=command.operation,
            operand_a=command.operand_a,
            operand_b=command.operand_b,
            result=result,
            occurred_at=self.clock.now(),
            correlation_id=command.correlation_id,
        )
        self._save(calculation)
        return calculation

    def _save(self, calculation: Calculation) -> None:
        # The result is returned only after the row is stored: a 500 the client can retry
        # is preferred over a 200 whose calculation never reaches the history.
        try:
            self.repository.save(calculation)
        except CalculatorError:
            raise
        except Exception as error:
            raise InfrastructureError("The calculation could not be saved.", error) from error
