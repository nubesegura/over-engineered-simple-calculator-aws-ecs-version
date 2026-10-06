import json
import logging
from typing import Any

from calculator_core.application.ports.error_notifier import InternalErrorReport

logger = logging.getLogger(__name__)

_MAX_MESSAGE_CHARS = 1000
_MAX_SUBJECT_CHARS = 100


class SnsErrorNotifier:
    """Publishes internal errors to the alert topic. `notify` never raises."""

    def __init__(self, client: Any, topic_arn: str) -> None:
        self._client = client
        self._topic_arn = topic_arn

    def notify(self, report: InternalErrorReport) -> None:
        try:
            subject = f"[{report.environment}] {report.service} internal error"
            body = {
                "service": report.service,
                "environment": report.environment,
                "error_type": report.error_type,
                "error_message": report.error_message[:_MAX_MESSAGE_CHARS],
                "correlation_id": report.correlation_id,
            }
            self._client.publish(
                TopicArn=self._topic_arn,
                Subject=subject[:_MAX_SUBJECT_CHARS],
                Message=json.dumps(body),
            )
        except Exception as error:  # a failed alert must never fail the request
            logger.error("Could not publish the error notification (%s).", type(error).__name__)


class LogErrorNotifier:
    """Local-mode notifier: only logs the report."""

    def notify(self, report: InternalErrorReport) -> None:
        logger.error("Internal error in %s: %s", report.service, report.error_type)
