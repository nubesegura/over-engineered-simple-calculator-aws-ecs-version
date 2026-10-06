"""Composition root: the only place that wires adapters to use cases."""

from dataclasses import dataclass

import boto3

from calculator_core.adapters.outbound.in_memory_repository import InMemoryCalculationRepository
from calculator_core.adapters.outbound.postgres_repository import (
    PostgresCalculationRepository,
    create_pool,
)
from calculator_core.adapters.outbound.secrets_manager_credentials import (
    SecretsManagerCredentialsProvider,
)
from calculator_core.adapters.outbound.sns_error_notifier import LogErrorNotifier, SnsErrorNotifier
from calculator_core.adapters.outbound.system import SystemClock, UuidGenerator
from calculator_core.application.ports.calculation_repository import CalculationRepository
from calculator_core.application.ports.error_notifier import ErrorNotifier
from calculator_core.application.use_cases.calculate import Calculate
from calculator_core.application.use_cases.read_history import ReadHistory
from calculator_core.config.settings import Settings


@dataclass(frozen=True)
class Container:
    settings: Settings
    repository: CalculationRepository
    notifier: ErrorNotifier
    calculate: Calculate
    read_history: ReadHistory


def build_container(settings: Settings) -> Container:
    repository, notifier = _outbound_adapters(settings)
    return Container(
        settings=settings,
        repository=repository,
        notifier=notifier,
        calculate=Calculate(repository, SystemClock(), UuidGenerator()),
        read_history=ReadHistory(repository),
    )


def _outbound_adapters(settings: Settings) -> tuple[CalculationRepository, ErrorNotifier]:
    if settings.is_local:
        return InMemoryCalculationRepository(), LogErrorNotifier()
    provider = SecretsManagerCredentialsProvider(
        settings.app_secret_arn, boto3.client("secretsmanager")
    )
    pool = create_pool(settings.db_host, settings.db_port, settings.db_name, provider)
    notifier = SnsErrorNotifier(boto3.client("sns"), settings.alert_topic_arn)
    return PostgresCalculationRepository(pool), notifier
