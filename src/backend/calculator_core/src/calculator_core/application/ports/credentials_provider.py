from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class DatabaseCredentials:
    username: str
    password: str = field(repr=False)


class CredentialsProvider(Protocol):
    def get(self) -> DatabaseCredentials:
        """Credentials of the application database user; may come from a short cache.

        Must raise `InfrastructureError` (never including the password) when they
        cannot be read.
        """

    def refresh(self) -> DatabaseCredentials:
        """Discard any cache and read the credentials again from the source."""
