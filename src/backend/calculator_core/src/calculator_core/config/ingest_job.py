"""Ingestion job: reads the object named by INGEST_BUCKET and INGEST_KEY and ingests it.

Outcome: a processed file goes to `processed/`, a rejected one to `rejected/` (and the team is
notified); in both cases a JSON report is written under `reports/`. Keys outside
`incoming/*.csv` and objects that no longer exist are ignored (exit 0). An infrastructure
failure is notified, leaves the object in `incoming/` and exits non-zero. Only the exception
type is logged: messages of SDK or driver errors are not guaranteed to be free of secrets.
"""

import logging
import os

import boto3

from calculator_core.adapters.outbound.s3_object_store import (
    CLIENT_CONFIG,
    S3ObjectStore,
    is_ingest_key,
)
from calculator_core.application.ports.error_notifier import InternalErrorReport
from calculator_core.application.ports.object_store import ObjectNotFoundError, ObjectStore
from calculator_core.application.use_cases.ingest_csv import IngestCsv
from calculator_core.config.container import Container, build_container
from calculator_core.config.observability import configure_logging
from calculator_core.config.settings import load_ingest_settings, load_settings

logger = logging.getLogger(__name__)


def run_ingest(container: Container, store: ObjectStore, key: str) -> int:
    """Ingest one object and return the process exit code (non-zero on infrastructure errors)."""
    if not is_ingest_key(key):
        logger.warning("Ignoring a key outside incoming/*.csv.")
        return 0
    try:
        result = IngestCsv(store, container.repository).execute(key)
        store.put_report(key, result.to_report(key))
        store.move(key, "rejected" if result.rejected else "processed")
    except ObjectNotFoundError:
        logger.warning("The object no longer exists; nothing to do.")
        return 0
    except Exception as error:
        logger.error("Ingestion failed (%s).", type(error).__name__)
        _notify(container, type(error).__name__, "The ingestion job failed; see the task logs.")
        return 1
    if result.rejected:
        _notify(container, "FileRejected", result.rejection or "The file was rejected.")
    logger.info(
        "Ingestion finished: read=%d saved=%d skipped=%d rejected=%s.",
        result.read,
        result.saved,
        len(result.skipped),
        result.rejected,
    )
    return 0


def main() -> int:
    try:
        settings = load_settings(os.environ)
        configure_logging(settings.service_name, settings.environment)
        ingest = load_ingest_settings(os.environ)
        container = build_container(settings)
        store = S3ObjectStore(boto3.client("s3", config=CLIENT_CONFIG), ingest.bucket)
    except Exception as error:
        logger.error("Ingestion job could not start (%s).", type(error).__name__)
        return 1
    return run_ingest(container, store, ingest.key)


def _notify(container: Container, error_type: str, message: str) -> None:
    settings = container.settings
    container.notifier.notify(
        InternalErrorReport(
            service=settings.service_name,
            environment=settings.environment,
            error_type=error_type,
            error_message=message,
            correlation_id=None,
        )
    )
