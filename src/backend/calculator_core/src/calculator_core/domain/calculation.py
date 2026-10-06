from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from calculator_core.domain.operation import Operation


@dataclass(frozen=True)
class Calculation:
    id: UUID
    operation: Operation
    operand_a: Decimal
    operand_b: Decimal
    result: Decimal
    occurred_at: datetime
    correlation_id: str | None = None
