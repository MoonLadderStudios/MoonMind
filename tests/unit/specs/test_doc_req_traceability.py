"""DOC-REQ traceability gate for the active feature spec."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOC_REQ_PATTERN = re.compile(r"\bDOC-REQ-(\d{3})\b")
_FEATURE_DIR_PATTERN = re.compile(r"^(?P<prefix>\d{3})-[^/]+$")
_DOC_REQ_COLUMN_NAMES = (
    "DOC-REQ ID",
    "DOC-REQ",
    "Source Requirement",
    "Requirement ID",
)
_VALIDATION_COLUMN_NAME = "Validation Strategy"


def test_active_feature_doc_req_traceability_contract() -> None:
    feature_dir = _find_active_feature_dir()
    feature_spec = feature_dir / "spec.md"
    feature_traceability = feature_dir / "contracts" / "requirements-traceability.md"

    spec_text = feature_spec.read_text(encoding="utf-8")
    doc_req_ids = {
        f"DOC-REQ-{match.group(1)}" for match in _DOC_REQ_PATTERN.finditer(spec_text)
    }
    if not doc_req_ids:
        pytest.skip(f"No DOC-REQ entries found in active feature spec {feature_spec}")

    assert feature_traceability.exists(), (
        "Missing traceability file for DOC-REQ feature: "
        f"{feature_traceability}"
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
    doc_req_col = _find_column_index(header, _DOC_REQ_COLUMN_NAMES)
    validation_col = _find_column_index(header, (_VALIDATION_COLUMN_NAME,))
    assert doc_req_col is not None and validation_col is not None, (
        "Traceability header must include one DOC-REQ column "
        f"({_DOC_REQ_COLUMN_NAMES}) and '{_VALIDATION_COLUMN_NAME}' in "
        f"{traceability_path}"
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

        doc_req_match = _DOC_REQ_PATTERN.search(cells[doc_req_col])
        if doc_req_match is None:
            continue
        doc_req_id = f"DOC-REQ-{doc_req_match.group(1)}"

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
        if (
            _find_column_index(cells, _DOC_REQ_COLUMN_NAMES) is not None
            and _find_column_index(cells, (_VALIDATION_COLUMN_NAME,)) is not None
        ):
            return index
    return None


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _find_column_index(header: list[str], names: tuple[str, ...]) -> int | None:
    normalized_to_index = {cell.strip("`").strip(): index for index, cell in enumerate(header)}
    for name in names:
        if name in normalized_to_index:
            return normalized_to_index[name]
    return None


def _is_delimiter_row(cells: list[str]) -> bool:
    return all(cell and set(cell) <= {"-", ":", " "} for cell in cells)


def _find_active_feature_dir() -> Path:
    feature_dirs: list[tuple[int, Path]] = []
    for path in Path("specs").iterdir():
        if not path.is_dir():
            continue
        match = _FEATURE_DIR_PATTERN.fullmatch(path.name)
        if match is None:
            continue
        if not (path / "spec.md").exists():
            continue
        feature_dirs.append((int(match.group("prefix")), path))

    assert feature_dirs, "No numbered feature specs found under specs/"
    return max(feature_dirs, key=lambda item: item[0])[1]
