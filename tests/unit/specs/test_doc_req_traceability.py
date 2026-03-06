"""DOC-REQ traceability gates for feature specs that declare document requirements."""

from __future__ import annotations

import re
from pathlib import Path

_DOC_REQ_PATTERN = re.compile(r"\bDOC-REQ-(\d{3})\b")
_FEATURE_SPECS = tuple(sorted(Path("specs").glob("*/spec.md")))
_DOC_REQ_COLUMN_NAMES = (
    "DOC-REQ ID",
    "DOC-REQ",
    "DOC Requirement",
    "Source Requirement",
)
_VALIDATION_COLUMN_NAMES = (
    "Validation Strategy",
    "Planned Validation",
    "Validation Evidence",
    "Evidence Artifacts",
)
_EMPTY_VALIDATION_VALUES = {"", "-", "tbd", "todo", "n/a", "na", "none"}


def test_doc_req_features_have_traceability_contracts() -> None:
    doc_req_specs = [
        spec_path for spec_path in _FEATURE_SPECS if _doc_req_ids_from_text(spec_path)
    ]
    assert doc_req_specs, "Expected at least one feature spec with DOC-REQ entries"

    failures: list[str] = []
    for spec_path in doc_req_specs:
        try:
            _assert_spec_traceability(spec_path)
        except AssertionError as exc:
            failures.append(f"{spec_path.parent.name}: {exc}")

    assert not failures, "\n".join(failures)


def _assert_spec_traceability(spec_path: Path) -> None:
    doc_req_ids = _doc_req_ids_from_text(spec_path)
    traceability_path = spec_path.parent / "contracts" / "requirements-traceability.md"

    assert traceability_path.exists(), (
        "Missing traceability file for DOC-REQ feature: " f"{traceability_path}"
    )

    traceability_rows = _parse_traceability_rows(traceability_path)
    traceability_ids = set(traceability_rows)

    missing_ids = sorted(doc_req_ids - traceability_ids)
    extra_ids = sorted(traceability_ids - doc_req_ids)

    assert not missing_ids, (
        "Missing DOC-REQ traceability rows in "
        f"{traceability_path}: {', '.join(missing_ids)}"
    )
    assert not extra_ids, (
        "Unexpected DOC-REQ traceability rows in "
        f"{traceability_path}: {', '.join(extra_ids)}"
    )


def _doc_req_ids_from_text(spec_path: Path) -> set[str]:
    spec_text = spec_path.read_text(encoding="utf-8")
    return {f"DOC-REQ-{match.group(1)}" for match in _DOC_REQ_PATTERN.finditer(spec_text)}


def _parse_traceability_rows(traceability_path: Path) -> dict[str, str]:
    lines = traceability_path.read_text(encoding="utf-8").splitlines()

    table_start = _find_header_line(lines)
    assert table_start is not None, (
        "Traceability table header missing DOC-REQ and validation columns "
        f"in {traceability_path}"
    )

    header = _split_row(lines[table_start])
    doc_req_col = _find_column_index(header, _DOC_REQ_COLUMN_NAMES)
    validation_col = _find_column_index(header, _VALIDATION_COLUMN_NAMES)
    assert doc_req_col is not None and validation_col is not None, (
        "Traceability header must include a DOC-REQ column and a validation column "
        f"in {traceability_path}"
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

        doc_req_id = _extract_doc_req_id(cells[doc_req_col])
        if doc_req_id is None:
            continue

        assert doc_req_id not in rows, (
            "Duplicate DOC-REQ traceability row in "
            f"{traceability_path}: {doc_req_id}"
        )

        validation_value = cells[validation_col].strip()
        normalized_validation = _normalize_validation_value(validation_value)
        assert normalized_validation not in _EMPTY_VALIDATION_VALUES, (
            "Validation strategy must be non-empty for "
            f"{doc_req_id} in {traceability_path}"
        )

        rows[doc_req_id] = validation_value

    assert rows, f"No DOC-REQ rows found in {traceability_path}"
    return rows


def _find_header_line(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        cells = _split_row(line)
        if _find_column_index(cells, _DOC_REQ_COLUMN_NAMES) is None:
            continue
        if _find_column_index(cells, _VALIDATION_COLUMN_NAMES) is None:
            continue
        return index
    return None


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _find_column_index(header: list[str], candidates: tuple[str, ...]) -> int | None:
    for candidate in candidates:
        try:
            return header.index(candidate)
        except ValueError:
            continue
    return None


def _extract_doc_req_id(cell: str) -> str | None:
    match = _DOC_REQ_PATTERN.search(cell)
    if match is None:
        return None
    return f"DOC-REQ-{match.group(1)}"


def _normalize_validation_value(value: str) -> str:
    return value.replace("`", "").strip().lower()


def _is_delimiter_row(cells: list[str]) -> bool:
    return all(cell and set(cell) <= {"-", ":", " "} for cell in cells)
