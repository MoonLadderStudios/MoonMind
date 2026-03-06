"""DOC-REQ traceability gates for active spec features."""

from __future__ import annotations

import re
from pathlib import Path

_DOC_REQ_PATTERN = re.compile(r"\bDOC-REQ-(\d{3})\b")
_WORKFLOW_TYPE_LIFECYCLE_SPEC = Path("specs/046-workflow-type-lifecycle/spec.md")
_WORKFLOW_TYPE_LIFECYCLE_TRACEABILITY = Path(
    "specs/046-workflow-type-lifecycle/contracts/requirements-traceability.md"
)
_TEMPORAL_ARTIFACT_PRESENTATION_SPEC = Path(
    "specs/047-temporal-artifact-presentation/spec.md"
)
_TEMPORAL_ARTIFACT_PRESENTATION_TRACEABILITY = Path(
    "specs/047-temporal-artifact-presentation/contracts/requirements-traceability.md"
)


def test_workflow_type_lifecycle_doc_req_traceability_contract() -> None:
    _assert_doc_req_traceability_contract(
        spec_path=_WORKFLOW_TYPE_LIFECYCLE_SPEC,
        traceability_path=_WORKFLOW_TYPE_LIFECYCLE_TRACEABILITY,
        feature_label="046 workflow-type-lifecycle",
    )


def test_temporal_artifact_presentation_doc_req_traceability_contract() -> None:
    _assert_doc_req_traceability_contract(
        spec_path=_TEMPORAL_ARTIFACT_PRESENTATION_SPEC,
        traceability_path=_TEMPORAL_ARTIFACT_PRESENTATION_TRACEABILITY,
        feature_label="047 temporal-artifact-presentation",
    )


def _assert_doc_req_traceability_contract(
    *,
    spec_path: Path,
    traceability_path: Path,
    feature_label: str,
) -> None:
    spec_text = spec_path.read_text(encoding="utf-8")
    doc_req_ids = {
        f"DOC-REQ-{match.group(1)}" for match in _DOC_REQ_PATTERN.finditer(spec_text)
    }
    assert doc_req_ids, f"Expected DOC-REQ entries in {feature_label} spec.md"

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
