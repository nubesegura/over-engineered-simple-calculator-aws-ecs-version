from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class InternalErrorReport:
    service: str
    environment: str
    error_type: str
    error_message: str
    correlation_id: str | None


class ErrorNotifier(Protocol):
    def notify(self, report: InternalErrorReport) -> None:
        """Notify the team of an internal error. Must NEVER raise."""
