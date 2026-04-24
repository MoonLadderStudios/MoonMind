"""DOC-REQ traceability gates for feature specs that declare document requirements."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOC_REQ_PATTERN = re.compile(r"\bDOC-REQ-(\d{3})\b")
_FEATURE_DIR_PATTERN = re.compile(r"^(?P<prefix>\d{3})-[^/]+$")
_DOC_REQ_COLUMN_NAMES = (
    "DOC-REQ ID",
    "DOC-REQ",
    "DOC Requirement",
    "Source Requirement",
    "Requirement ID",
)
_VALIDATION_COLUMN_NAMES = (
    "Validation Strategy",
    "Validation Evidence",
    "Completed Validation Tasks",
    "Planned Validation",
    "Evidence Artifacts",
)
_EMPTY_VALIDATION_VALUES = {"", "-", "tbd", "todo", "n/a", "na", "none"}

def _doc_req_ids_from_text(spec_path: Path) -> set[str]:
    spec_text = spec_path.read_text(encoding="utf-8")
    return {
        f"DOC-REQ-{match.group(1)}" for match in _DOC_REQ_PATTERN.finditer(spec_text)
    }

def _discover_contract_backed_features() -> list[tuple[str, Path, Path]]:
    features: list[tuple[str, Path, Path]] = []
    for path in sorted(Path("specs").iterdir()):
        if not path.is_dir():
            continue
        if _FEATURE_DIR_PATTERN.fullmatch(path.name) is None:
            continue
        feature_spec = path / "spec.md"
        feature_traceability = path / "contracts" / "requirements-traceability.md"
        if not (feature_spec.exists() and feature_traceability.exists()):
            continue
        if not _doc_req_ids_from_text(feature_spec):
            continue
        if feature_spec.exists() and feature_traceability.exists():
            features.append((path.name, feature_spec, feature_traceability))
    return features

_FEATURES = _discover_contract_backed_features()

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
    doc_req_ids = _doc_req_ids_from_text(feature_spec)
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
        "Traceability table header missing DOC-REQ and validation columns "
        f"in {traceability_path}"
    )

    header = _split_row(lines[table_start])
    doc_req_col = _find_column_index(header, _DOC_REQ_COLUMN_NAMES)
    validation_col = _find_column_index(header, _VALIDATION_COLUMN_NAMES)
    assert doc_req_col is not None and validation_col is not None, (
        "Traceability header must include one DOC-REQ column "
        f"({_DOC_REQ_COLUMN_NAMES}) and one validation column "
        f"({_VALIDATION_COLUMN_NAMES}) in "
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

        assert (
            doc_req_id not in rows
        ), f"Duplicate DOC-REQ traceability row in {traceability_path}: {doc_req_id}"

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
        if (
            _find_column_index(cells, _DOC_REQ_COLUMN_NAMES) is not None
            and _find_column_index(cells, _VALIDATION_COLUMN_NAMES) is not None
        ):
            return index
    return None

def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]

def _find_column_index(header: list[str], names: tuple[str, ...]) -> int | None:
    normalized_to_index = {
        cell.strip("`").strip(): index for index, cell in enumerate(header)
    }
    for name in names:
        if name in normalized_to_index:
            return normalized_to_index[name]
    return None

def _normalize_validation_value(value: str) -> str:
    return value.replace("`", "").strip().lower()

def _is_delimiter_row(cells: list[str]) -> bool:
    return all(cell and set(cell) <= {"-", ":", " "} for cell in cells)
