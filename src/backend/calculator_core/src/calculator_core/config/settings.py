"""Settings from environment variables, validated once at cold start.

Outside `ENVIRONMENT=local` a missing or invalid value stops the start with a message that
names the variables (never their values).
"""

from collections.abc import Mapping
from dataclasses import dataclass

# Names the implementation that answers; sent in every calculation response (never configurable).
BACKEND_NAME = "ecs"
ENVIRONMENTS = ("local", "dev", "prod")
_REQUIRED_OUTSIDE_LOCAL = (
    "SERVICE_NAME",
    "APP_SECRET_ARN",
    "DB_HOST",
    "DB_NAME",
    "ALERT_TOPIC_ARN",
    "CORS_ALLOWED_ORIGIN",
)
_DEFAULT_DB_PORT = 5432
_LOCAL_ORIGIN = "http://localhost:3000"


class SettingsError(Exception):
    """The environment does not describe a valid configuration."""


@dataclass(frozen=True)
class Settings:
    environment: str
    service_name: str
    app_secret_arn: str
    db_host: str
    db_port: int
    db_name: str
    alert_topic_arn: str
    cors_allowed_origin: str

    @property
    def is_local(self) -> bool:
        return self.environment == "local"


def load_settings(env: Mapping[str, str]) -> Settings:
    environment = env.get("ENVIRONMENT", "").strip()
    if environment not in ENVIRONMENTS:
        raise SettingsError(f"ENVIRONMENT must be one of: {', '.join(ENVIRONMENTS)}.")
    local = environment == "local"

    def value(name: str, default: str = "") -> str:
        return env.get(name, "").strip() or default

    problems = [
        f"{name} is required" for name in _REQUIRED_OUTSIDE_LOCAL if not local and not value(name)
    ]
    port = _parse_port(value("DB_PORT"), problems)
    origin = value("CORS_ALLOWED_ORIGIN", _LOCAL_ORIGIN if local else "")
    if origin == "*":
        problems.append("CORS_ALLOWED_ORIGIN must be an explicit origin, not '*'")
    if problems:
        raise SettingsError("Invalid configuration: " + "; ".join(problems) + ".")
    return Settings(
        environment=environment,
        service_name=value("SERVICE_NAME", "calculator-local"),
        app_secret_arn=value("APP_SECRET_ARN"),
        db_host=value("DB_HOST"),
        db_port=port,
        db_name=value("DB_NAME"),
        alert_topic_arn=value("ALERT_TOPIC_ARN"),
        cors_allowed_origin=origin,
    )


def _parse_port(raw: str, problems: list[str]) -> int:
    if not raw:
        return _DEFAULT_DB_PORT
    if raw.isdigit() and 0 < int(raw) < 65536:
        return int(raw)
    problems.append("DB_PORT must be a number between 1 and 65535")
    return _DEFAULT_DB_PORT


@dataclass(frozen=True)
class IngestSettings:
    """What only the ingestion job needs: the object named by the EventBridge target."""

    bucket: str
    key: str


def load_ingest_settings(env: Mapping[str, str]) -> IngestSettings:
    missing = [name for name in ("INGEST_BUCKET", "INGEST_KEY") if not env.get(name, "").strip()]
    if missing:
        raise SettingsError(f"Invalid configuration: {', '.join(missing)} required.")
    return IngestSettings(bucket=env["INGEST_BUCKET"].strip(), key=env["INGEST_KEY"].strip())
