import json
from decimal import Decimal
from pathlib import Path

import pytest

from calculator_core.adapters.outbound.in_memory_repository import InMemoryCalculationRepository
from calculator_core.application.errors import InfrastructureError
from calculator_core.application.use_cases.ingest_csv import IngestCsv
from calculator_core.domain.ingestion import MAX_FILE_BYTES, MAX_ROWS
from calculator_core.domain.operation import Operation
from tests.fakes import FailingRepository, InMemoryObjectStore

HEADER = "operation,operand_a,operand_b,occurred_at\n"
KEY = "incoming/file.csv"
SAMPLE = Path(__file__).parents[5] / "sample-data" / "ecs-ingestion-sample.csv"


def run(content: str | bytes) -> tuple[IngestCsv, InMemoryCalculationRepository]:
    data = content.encode() if isinstance(content, str) else content
    repository = InMemoryCalculationRepository()
    return IngestCsv(InMemoryObjectStore({KEY: data}), repository), repository


def test_valid_file_saves_every_row_and_recomputes_the_result() -> None:
    use_case, repository = run(
        HEADER + "add,1.5,2,2026-10-01T10:00:00Z\ndiv,1,4,2026-10-01T10:00:00+00:00\n"
    )

    result = use_case.execute(KEY)

    assert (result.read, result.saved, result.skipped, result.rejection) == (2, 2, (), None)
    rows = {row.operation: row for row in repository.rows.values()}
    assert rows[Operation.ADD].result == Decimal("3.5")
    assert rows[Operation.DIV].result == Decimal("0.25")


def test_mixed_file_skips_invalid_rows_with_line_and_reason() -> None:
    use_case, repository = run(
        HEADER
        + "add,1,2,2026-10-01T10:00:00Z\n"
        + "pow,1,2,2026-10-01T10:00:00Z\n"
        + "\n"
        + "add,abc,2,2026-10-01T10:00:00Z\n"
        + "add,1e16,2,2026-10-01T10:00:00Z\n"
        + "div,1,0,2026-10-01T10:00:00Z\n"
        + "add,1,2,2026-10-01T10:00:00\n"
        + "add,1,2,2026-10-01T10:00:00+02:00\n"
        + "add,1,2\n"
        + "sub,5,3,2026-10-01T10:00:00Z\n"
    )

    result = use_case.execute(KEY)

    assert (result.read, result.saved) == (9, 2)
    assert [row.line for row in result.skipped] == [3, 5, 6, 7, 8, 9, 10]
    assert "Unknown operation" in result.skipped[0].reason
    assert "Division by zero" in result.skipped[3].reason
    assert len(repository.rows) == 2


def test_a_repeated_row_or_file_creates_no_duplicate() -> None:
    row = "mul,2,3,2026-10-01T10:00:00Z\n"
    equivalent = "MUL, 2.0 ,3.00,2026-10-01T12:00:00+00:00\n"  # same content, other spelling
    use_case, repository = run(HEADER + row + row)

    use_case.execute(KEY)
    use_case.execute(KEY)

    assert len(repository.rows) == 1
    other, other_repository = run(HEADER + equivalent)
    other.execute(KEY)
    assert len(other_repository.rows) == 1


def test_the_id_changes_with_the_time() -> None:
    use_case, repository = run(
        HEADER + "add,1,2,2026-10-01T10:00:00Z\nadd,1,2,2026-10-01T10:00:01Z\n"
    )

    use_case.execute(KEY)

    assert len(repository.rows) == 2


@pytest.mark.parametrize(
    "content",
    [
        "operation,a,b,time\nadd,1,2,2026-10-01T10:00:00Z\n",
        "",
        b"\xff\xfe\x00bad",
        HEADER + 'add,"1,2,2026-10-01T10:00:00Z\n',
        HEADER + "add,1,2,2026-10-01T10:00:00Z\x00\n",
        HEADER + 'add,"1"x,2,2026-10-01T10:00:00Z\n',
    ],
)
def test_wrong_header_or_invalid_csv_rejects_the_whole_file(content: str | bytes) -> None:
    use_case, repository = run(content)

    result = use_case.execute(KEY)

    assert result.rejected
    assert (result.saved, repository.rows) == (0, {})


def test_too_many_rows_rejects_the_file_and_saves_nothing() -> None:
    row = "add,1,2,2026-10-01T10:00:00Z\n"
    use_case, repository = run(HEADER + row * (MAX_ROWS + 1))

    result = use_case.execute(KEY)

    assert result.rejected
    assert "data rows" in (result.rejection or "")
    assert repository.rows == {}


def test_oversized_file_is_rejected() -> None:
    use_case, repository = run(b"x" * (MAX_FILE_BYTES + 1))

    result = use_case.execute(KEY)

    assert result.rejected
    assert repository.rows == {}


def test_formula_like_cells_stay_text_and_the_snippet_is_short() -> None:
    long_formula = "=HYPERLINK(" + "x" * 500 + ")"
    use_case, _ = run(
        HEADER
        + "=cmd|' /C calc'!A0,1,2,2026-10-01T10:00:00Z\n"
        + f"add,{long_formula},2,2026-10-01T10:00:00Z\n"
        + "add,@SUM(1),2,2026-10-01T10:00:00Z\n"
    )

    result = use_case.execute(KEY)
    report = json.loads(result.to_report(KEY))

    assert result.saved == 0
    reasons = [row["reason"] for row in report["skipped_rows"]]
    assert "=cmd|" in reasons[0]
    assert all(isinstance(reason, str) and len(reason) < 120 for reason in reasons)
    assert "x" * 30 not in result.to_report(KEY)
    assert report["status"] == "processed"
    assert (report["read"], report["saved"], report["skipped"]) == (3, 0, 3)


def test_huge_exponent_skips_the_row_and_the_job_continues() -> None:
    use_case, repository = run(
        HEADER
        + "add,1e1000000,1,2026-10-01T10:00:00Z\n"
        + "add,1,-1E9999999,2026-10-01T10:00:00Z\n"
        + "add,1,2,2026-10-01T10:00:00Z\n"
    )

    result = use_case.execute(KEY)

    assert (result.saved, len(repository.rows)) == (1, 1)
    assert json.loads(result.to_report(KEY))["skipped"] == 2


def test_store_failure_raises_infrastructure_error_and_leaves_the_object() -> None:
    store = InMemoryObjectStore({KEY: (HEADER + "add,1,2,2026-10-01T10:00:00Z\n").encode()})
    use_case = IngestCsv(store, FailingRepository(RuntimeError("db down")))

    with pytest.raises(InfrastructureError):
        use_case.execute(KEY)

    assert KEY in store.objects
    assert store.moves == []


# The sample folder is git-ignored, so a clean checkout (CI) does not have it.
@pytest.mark.skipif(not SAMPLE.exists(), reason="local sample file is not versioned")
def test_the_sample_file_loads_ten_rows_and_skips_none() -> None:
    repository = InMemoryCalculationRepository()
    store = InMemoryObjectStore({KEY: SAMPLE.read_bytes()})

    result = IngestCsv(store, repository).execute(KEY)

    assert (result.read, result.saved, result.skipped, result.rejection) == (10, 10, (), None)
    assert len(repository.rows) == 10
