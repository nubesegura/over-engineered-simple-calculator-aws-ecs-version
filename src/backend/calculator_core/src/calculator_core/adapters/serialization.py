"""JSON shapes of the HTTP contract (the same as the `sls` backend)."""

from decimal import Decimal
from typing import Any

from calculator_core.application.use_cases.read_history import HistoryPage
from calculator_core.domain.calculation import Calculation

JsonDict = dict[str, Any]


def decimal_to_str(value: Decimal) -> str:
    """Serialize a Decimal in fixed-point notation, without losing precision.

    `str(Decimal("10") / Decimal("0.5"))` produces "2E+1"; this returns "20".
    """
    return format(value, "f")


def calculation_to_json(calculation: Calculation) -> JsonDict:
    return {
        "calculation_id": str(calculation.id),
        "operation": calculation.operation.value,
        "a": decimal_to_str(calculation.operand_a),
        "b": decimal_to_str(calculation.operand_b),
        "result": decimal_to_str(calculation.result),
    }


def calculation_response_to_json(calculation: Calculation, backend: str) -> JsonDict:
    """Body of a successful calculation: the calculation plus the backend that answered."""
    return {**calculation_to_json(calculation), "backend": backend}


def history_to_json(page: HistoryPage) -> JsonDict:
    return {
        "items": [
            {**calculation_to_json(item), "occurred_at": item.occurred_at.isoformat()}
            for item in page.items
        ],
        "next_cursor": page.next_cursor,
    }
