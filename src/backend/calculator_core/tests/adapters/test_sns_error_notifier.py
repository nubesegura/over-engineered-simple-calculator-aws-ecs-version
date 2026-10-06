import json
import logging
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from calculator_core.adapters.outbound.sns_error_notifier import SnsErrorNotifier
from calculator_core.application.ports.error_notifier import InternalErrorReport

REPORT = InternalErrorReport("calc-add", "dev", "OperationalError", "boom", "corr-1")


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        yield


def test_publishes_the_report_to_the_topic(aws: None) -> None:
    sns = boto3.client("sns", region_name="us-east-2")
    sqs = boto3.client("sqs", region_name="us-east-2")
    topic_arn = sns.create_topic(Name="alerts")["TopicArn"]
    queue_url = sqs.create_queue(QueueName="inbox")["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])[
        "Attributes"
    ]["QueueArn"]
    sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn)

    SnsErrorNotifier(sns, topic_arn).notify(REPORT)

    envelope = json.loads(sqs.receive_message(QueueUrl=queue_url)["Messages"][0]["Body"])
    assert envelope["Subject"] == "[dev] calc-add internal error"
    message = json.loads(envelope["Message"])
    assert message["error_type"] == "OperationalError"
    assert message["correlation_id"] == "corr-1"


def test_a_failed_publish_is_logged_and_never_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Broken:
        def publish(self, **kwargs: Any) -> None:
            raise RuntimeError("topic secret-detail unreachable")

    with caplog.at_level(logging.ERROR):
        SnsErrorNotifier(Broken(), "arn:aws:sns:us-east-2:000000000000:alerts").notify(REPORT)

    assert "RuntimeError" in caplog.text
    assert "secret-detail" not in caplog.text
