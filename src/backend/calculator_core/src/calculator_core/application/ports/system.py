from datetime import datetime
from typing import Protocol
from uuid import UUID


class Clock(Protocol):
    def now(self) -> datetime:
        """Current date and time in UTC (timezone-aware)."""


class IdGenerator(Protocol):
    def new_id(self) -> UUID:
        """New unique identifier."""
