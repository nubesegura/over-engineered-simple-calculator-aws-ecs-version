from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from calculator_core.adapters.outbound.s3_object_store import (
    CLIENT_CONFIG,
    S3ObjectStore,
    UnacceptedKeyError,
    is_ingest_key,
)
from calculator_core.application.errors import InfrastructureError
from calculator_core.application.ports.object_store import ObjectNotFoundError, ObjectTooLargeError

BUCKET = "ingest-test-bucket"
KEY = "incoming/sales/day1.csv"
REPORT = '{"ok": true}'


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-2", config=CLIENT_CONFIG)
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "us-east-2"}
        )
        yield client


def keys(client: Any) -> list[str]:
    listing = client.list_objects_v2(Bucket=BUCKET)
    return sorted(item["Key"] for item in listing.get("Contents", []))


@pytest.mark.parametrize(
    ("key", "accepted"),
    [
        ("incoming/a.csv", True),
        ("incoming/dir/a.csv", True),
        ("processed/a.csv", False),
        ("reports/a.csv", False),
        ("incoming/a.txt", False),
        ("incoming/.csv", False),
        ("incoming/../processed/a.csv", False),
        ("incoming/a\n.csv", False),
        ("x/incoming/a.csv", False),
    ],
)
def test_only_incoming_csv_keys_are_accepted(key: str, accepted: bool) -> None:
    assert is_ingest_key(key) is accepted


def test_get_reads_the_object_and_rejects_foreign_keys(s3: Any) -> None:
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=b"abc")
    s3.put_object(Bucket=BUCKET, Key="processed/x.csv", Body=b"abc")
    store = S3ObjectStore(s3, BUCKET)

    assert store.get(KEY, 10) == b"abc"
    with pytest.raises(UnacceptedKeyError):
        store.get("processed/x.csv", 10)


def test_get_reports_too_large_and_missing_objects(s3: Any) -> None:
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=b"abcdef")
    store = S3ObjectStore(s3, BUCKET)

    with pytest.raises(ObjectTooLargeError):
        store.get(KEY, 5)
    with pytest.raises(ObjectNotFoundError):
        store.get("incoming/missing.csv", 5)


def test_get_never_returns_more_than_the_limit_even_if_the_size_lies(s3: Any) -> None:
    class LyingClient:
        def head_object(self, **kwargs: Any) -> dict[str, int]:
            return {"ContentLength": 1}

        def get_object(self, **kwargs: Any) -> Any:
            return s3.get_object(**kwargs)

    s3.put_object(Bucket=BUCKET, Key=KEY, Body=b"abcdef")

    with pytest.raises(ObjectTooLargeError):
        S3ObjectStore(LyingClient(), BUCKET).get(KEY, 5)


def test_put_report_and_move_use_copy_then_delete(s3: Any) -> None:
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=b"abc")
    store = S3ObjectStore(s3, BUCKET)

    store.put_report(KEY, REPORT)
    store.move(KEY, "processed")

    assert keys(s3) == ["processed/sales/day1.csv", "reports/sales/day1.json"]
    report = s3.get_object(Bucket=BUCKET, Key="reports/sales/day1.json")
    assert report["ContentType"] == "application/json"
    assert report["Body"].read() == REPORT.encode()


def test_move_failures_and_foreign_keys_leave_the_source_in_place(s3: Any) -> None:
    store = S3ObjectStore(s3, BUCKET)
    with pytest.raises(InfrastructureError):
        store.move(KEY, "rejected")  # the source does not exist: the copy fails

    s3.put_object(Bucket=BUCKET, Key=KEY, Body=b"abc")
    with pytest.raises(UnacceptedKeyError):
        store.move("reports/x.csv", "rejected")

    assert keys(s3) == [KEY]


def test_a_failed_write_becomes_an_infrastructure_error(s3: Any) -> None:
    with pytest.raises(InfrastructureError):
        S3ObjectStore(s3, "no-such-bucket-here").put_report(KEY, REPORT)


def test_size_returns_the_content_length_and_reports_missing_and_foreign_keys(s3: Any) -> None:
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=b"abcd")
    store = S3ObjectStore(s3, BUCKET)

    assert store.size(KEY) == 4
    with pytest.raises(ObjectNotFoundError):
        store.size("incoming/missing.csv")
    with pytest.raises(UnacceptedKeyError):
        store.size("processed/x.csv")
