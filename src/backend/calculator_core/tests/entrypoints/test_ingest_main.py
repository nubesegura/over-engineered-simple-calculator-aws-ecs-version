import json
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from calculator_core.adapters.outbound.s3_object_store import CLIENT_CONFIG
from calculator_core.application.ports.error_notifier import InternalErrorReport
from calculator_core.application.ports.object_store import ObjectNotFoundError
from calculator_core.config import ingest_job
from calculator_core.config.container import Container, build_container
from calculator_core.config.settings import SettingsError, load_ingest_settings, load_settings
from tests.fakes import FailingRepository, InMemoryObjectStore

HEADER = "operation,operand_a,operand_b,occurred_at\n"
GOOD = HEADER + "add,1,2,2026-10-01T10:00:00Z\nbad,1,2,2026-10-01T10:00:00Z\n"
KEY = "incoming/day1.csv"
BUCKET = "ingest-test-bucket"


class RecordingNotifier:
    def __init__(self) -> None:
        self.reports: list[InternalErrorReport] = []

    def notify(self, report: InternalErrorReport) -> None:
        self.reports.append(report)


class MissingObjectStore:
    def get(self, key: str, max_bytes: int) -> bytes:
        raise ObjectNotFoundError()


def make_container(notifier: RecordingNotifier, failing: Exception | None = None) -> Container:
    base = build_container(load_settings({"ENVIRONMENT": "local"}))
    return Container(
        settings=base.settings,
        repository=FailingRepository(failing) if failing else base.repository,
        notifier=notifier,
        calculate=base.calculate,
        read_history=base.read_history,
    )


def test_a_processed_file_is_reported_and_moved_without_notification() -> None:
    notifier = RecordingNotifier()
    container = make_container(notifier)
    store = InMemoryObjectStore({KEY: GOOD.encode()})

    assert ingest_job.run_ingest(container, store, KEY) == 0

    assert store.moves == [(KEY, "processed")]
    report = json.loads(store.reports[KEY])
    assert (report["read"], report["saved"], report["skipped"]) == (2, 1, 1)
    assert notifier.reports == []
    assert len(container.repository.list_after(10, None)) == 1


def test_a_rejected_file_is_moved_to_rejected_and_notified_with_exit_zero() -> None:
    notifier = RecordingNotifier()
    store = InMemoryObjectStore({KEY: b"wrong,header\n"})

    assert ingest_job.run_ingest(make_container(notifier), store, KEY) == 0

    assert store.moves == [(KEY, "rejected")]
    assert json.loads(store.reports[KEY])["status"] == "rejected"
    assert [report.error_type for report in notifier.reports] == ["FileRejected"]


def test_keys_outside_incoming_and_missing_objects_are_ignored() -> None:
    notifier = RecordingNotifier()
    store = InMemoryObjectStore({"processed/a.csv": b"x"})
    container = make_container(notifier)

    assert ingest_job.run_ingest(container, store, "processed/a.csv") == 0
    assert ingest_job.run_ingest(container, MissingObjectStore(), KEY) == 0  # type: ignore[arg-type]

    assert (store.moves, store.reports, notifier.reports) == ([], {}, [])


def test_an_infrastructure_failure_notifies_exits_non_zero_and_keeps_the_object() -> None:
    notifier = RecordingNotifier()
    container = make_container(notifier, RuntimeError("db password=hunter2"))
    store = InMemoryObjectStore({KEY: GOOD.encode()})

    assert ingest_job.run_ingest(container, store, KEY) == 1

    assert KEY in store.objects
    assert store.reports == {}
    assert [report.error_type for report in notifier.reports] == ["InfrastructureError"]
    assert "hunter2" not in notifier.reports[0].error_message


def test_ingest_settings_name_the_missing_variables() -> None:
    with pytest.raises(SettingsError, match="INGEST_BUCKET, INGEST_KEY"):
        load_ingest_settings({})

    settings = load_ingest_settings({"INGEST_BUCKET": " b ", "INGEST_KEY": "incoming/a.csv"})

    assert (settings.bucket, settings.key) == ("b", "incoming/a.csv")


@pytest.fixture
def s3_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "us-east-2",
        "ENVIRONMENT": "local",
        "INGEST_BUCKET": BUCKET,
    }.items():
        monkeypatch.setenv(name, value)
    with mock_aws():
        client = boto3.client("s3", config=CLIENT_CONFIG)
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "us-east-2"}
        )
        yield client


def test_main_runs_end_to_end_against_s3(s3_env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    s3_env.put_object(Bucket=BUCKET, Key=KEY, Body=GOOD.encode())
    monkeypatch.setenv("INGEST_KEY", KEY)

    assert ingest_job.main() == 0

    listing = sorted(item["Key"] for item in s3_env.list_objects_v2(Bucket=BUCKET)["Contents"])
    assert listing == ["processed/day1.csv", "reports/day1.json"]


def test_main_exits_non_zero_on_bad_configuration_and_on_s3_errors(
    s3_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert ingest_job.main() == 1  # INGEST_KEY is missing

    monkeypatch.setenv("INGEST_KEY", KEY)
    monkeypatch.setenv("INGEST_BUCKET", "no-such-bucket-here")
    assert ingest_job.main() == 1  # reading from a missing bucket is an infrastructure error
