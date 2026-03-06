"""DOC-REQ traceability gate for the active top-level feature spec."""

from __future__ import annotations

import re
from pathlib import Path

_DOC_REQ_PATTERN = re.compile(r"\bDOC-REQ-(\d{3})\b")
_FEATURE_DIR_PATTERN = re.compile(r"^(?P<prefix>\d{3})-[a-z0-9-]+$")


def test_active_doc_req_feature_traceability_contract() -> None:
    feature_spec = _find_active_feature_spec()
    assert feature_spec is not None, "Expected at least one top-level feature spec"

    feature_traceability = (
        feature_spec.parent / "contracts" / "requirements-traceability.md"
    )
    spec_text = feature_spec.read_text(encoding="utf-8")
    doc_req_ids = {
        f"DOC-REQ-{match.group(1)}" for match in _DOC_REQ_PATTERN.finditer(spec_text)
    }
    assert doc_req_ids, f"Expected DOC-REQ entries in the newest feature spec {feature_spec}"

    assert feature_traceability.exists(), (
        "Missing traceability file for DOC-REQ feature: " f"{feature_traceability}"
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


def test_find_active_feature_spec_selects_newest_prefix_even_without_doc_req(
    tmp_path: Path,
) -> None:
    specs_root = tmp_path / "specs"
    older_feature = specs_root / "047-older-feature"
    newer_feature = specs_root / "048-newer-feature"
    older_feature.mkdir(parents=True)
    newer_feature.mkdir(parents=True)
    (older_feature / "spec.md").write_text("# Spec\nDOC-REQ-001\n", encoding="utf-8")
    (newer_feature / "spec.md").write_text("# Spec\nNo requirement IDs yet.\n", encoding="utf-8")

    assert _find_active_feature_spec(specs_root) == newer_feature / "spec.md"


def _find_active_feature_spec(specs_root: Path = Path("specs")) -> Path | None:
    candidates: list[tuple[int, Path]] = []
    for child in specs_root.iterdir():
        if not child.is_dir():
            continue

        match = _FEATURE_DIR_PATTERN.fullmatch(child.name)
        if match is None:
            continue

        spec_path = child / "spec.md"
        if not spec_path.exists():
            continue

        candidates.append((int(match.group("prefix")), spec_path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


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
