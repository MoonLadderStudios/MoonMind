"""DOC-REQ traceability gates for contract-backed feature specs."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOC_REQ_PATTERN = re.compile(r"\bDOC-REQ-(\d{3})\b")
_FEATURES = (
    (
        "046-workflow-type-lifecycle",
        Path("specs/046-workflow-type-lifecycle/spec.md"),
        Path(
            "specs/046-workflow-type-lifecycle/contracts/requirements-traceability.md"
        ),
    ),
    (
        "047-activity-worker-topology",
        Path("specs/047-activity-worker-topology/spec.md"),
        Path(
            "specs/047-activity-worker-topology/contracts/requirements-traceability.md"
        ),
    ),
    (
        "047-integrations-monitoring",
        Path("specs/047-integrations-monitoring/spec.md"),
        Path(
            "specs/047-integrations-monitoring/contracts/requirements-traceability.md"
        ),
    ),
    (
        "048-run-history-rerun",
        Path("specs/048-run-history-rerun/spec.md"),
        Path("specs/048-run-history-rerun/contracts/requirements-traceability.md"),
    ),
)


@pytest.mark.parametrize(
    ("feature_name", "feature_spec", "feature_traceability"),
    _FEATURES,
    ids=[feature_name for feature_name, *_ in _FEATURES],
)
def test_doc_req_traceability_contract(
    feature_name: str,
    feature_spec: Path,
    feature_traceability: Path,
) -> None:
    spec_text = feature_spec.read_text(encoding="utf-8")
    doc_req_ids = {
        f"DOC-REQ-{match.group(1)}" for match in _DOC_REQ_PATTERN.finditer(spec_text)
    }
    assert doc_req_ids, f"Expected DOC-REQ entries in {feature_name} spec.md"

    assert feature_traceability.exists(), (
        "Missing traceability file for DOC-REQ feature: "
        f"{feature_traceability} ({feature_name})"
    )

    traceability_rows = _parse_traceability_rows(feature_traceability)
    traceability_ids = set(traceability_rows)

    missing_ids = sorted(doc_req_ids - traceability_ids)
    extra_ids = sorted(traceability_ids - doc_req_ids)

    assert not missing_ids, (
        "Missing DOC-REQ traceability rows in "
        f"{feature_traceability}: {', '.join(missing_ids)}"
    )
    assert not extra_ids, (
        "Unexpected DOC-REQ traceability rows in "
        f"{feature_traceability}: {', '.join(extra_ids)}"
    )


def _parse_traceability_rows(traceability_path: Path) -> dict[str, str]:
    lines = traceability_path.read_text(encoding="utf-8").splitlines()

    table_start = _find_header_line(lines)
    assert table_start is not None, (
        "Traceability table header missing required columns " f"in {traceability_path}"
    )

    header = _split_row(lines[table_start])
    doc_req_col = _find_column_index(header, "DOC-REQ ID")
    validation_col = _find_column_index(header, "Validation Strategy")
    assert doc_req_col is not None and validation_col is not None, (
        "Traceability header must include 'DOC-REQ ID' and 'Validation Strategy' "
        f"columns in {traceability_path}"
    )

    rows: dict[str, str] = {}
    for line in lines[table_start + 1 :]:
        if not line.strip().startswith("|"):
            if rows:
                break
            continue

        cells = _split_row(line)
        if not cells or _is_delimiter_row(cells):
            continue
        if max(doc_req_col, validation_col) >= len(cells):
            continue

        doc_req_id = cells[doc_req_col].strip().strip("`")
        if not _DOC_REQ_PATTERN.fullmatch(doc_req_id):
            continue

        validation_strategy = cells[validation_col].strip()
        normalized_validation = validation_strategy.strip("`").strip().lower()
        assert normalized_validation not in {"", "-", "tbd", "todo", "n/a", "na"}, (
            "Validation strategy must be non-empty for "
            f"{doc_req_id} in {traceability_path}"
        )

        rows[doc_req_id] = validation_strategy

    assert rows, f"No DOC-REQ rows found in {traceability_path}"
    return rows


def _find_header_line(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if "DOC-REQ ID" in cells and "Validation Strategy" in cells:
            return index
    return None


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _find_column_index(header: list[str], name: str) -> int | None:
    try:
        return header.index(name)
    except ValueError:
        return None


def _is_delimiter_row(cells: list[str]) -> bool:
    return all(cell and set(cell) <= {"-", ":", " "} for cell in cells)
