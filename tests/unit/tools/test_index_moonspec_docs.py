"""MM-930 advisory MoonSpec documentation index tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[3] / "tools" / "index_moonspec_docs.py"
TOOLS_PATH = str(MODULE_PATH.parent)
if TOOLS_PATH not in sys.path:
    sys.path.insert(0, TOOLS_PATH)

SPEC = importlib.util.spec_from_file_location("index_moonspec_docs", MODULE_PATH)
assert SPEC is not None
index_moonspec_docs = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = index_moonspec_docs
SPEC.loader.exec_module(index_moonspec_docs)

mod = index_moonspec_docs


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_build_index_emits_documents_claims_and_advisory_warnings(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs" / "Api" / "WidgetContract.md",
        "# Widget Contract\n\n"
        "**Document Class:** Module Contract Specification\n"
        "**Authority:** Widget API payloads\n"
        "**Owning Surface:** moonmind/widgets\n"
        "**Related Docs:** [Architecture](../MoonMindArchitecture.md), docs/Api/Other.md\n"
        "**Related Implementation:** moonmind/widgets/api.py, WidgetApi\n\n"
        "## Payload\n\nThe payload has one field.\n",
    )
    _write(
        tmp_path / ".specify" / "memory" / "constitution.md",
        "# Constitution\n\n## Principle\n\nDo the thing.\n",
    )

    payload = mod.build_index(root=tmp_path)

    assert payload["issue"] == "MM-930"
    assert payload["sourceIssue"] == "MM-927"
    assert payload["advisoryOnly"] is True
    assert payload["documentCount"] == 2

    docs = {entry["path"]: entry for entry in payload["documents"]}
    contract = docs["docs/Api/WidgetContract.md"]
    assert contract["documentClass"] == "Module Contract Specification"
    assert contract["viewpoint"] == "Module Contract Specification"
    assert contract["authority"] == "Widget API payloads"
    assert contract["owningSurface"] == "moonmind/widgets"
    assert "../MoonMindArchitecture.md" in contract["relatedDocs"]
    assert "moonmind/widgets/api.py" in contract["relatedImplementation"]

    claims = {entry["anchor"]: entry for entry in payload["claims"]}
    assert "docs/Api/WidgetContract.md#widget-contract" in claims
    assert claims["docs/Api/WidgetContract.md#payload"]["type"] == "section"
    assert claims["docs/Api/WidgetContract.md#payload"]["digest"].startswith("sha256:")
    assert claims["docs/Api/WidgetContract.md#payload"]["id"].startswith("claim:")


def test_docs_tmp_is_not_treated_as_canonical(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs" / "tmp" / "MigrationPlan.md",
        "# Migration Plan\n\n**Document Class:** Imperative working document\n",
    )
    _write(
        tmp_path / "docs" / "SystemDesign.md",
        "# System Design\n\n"
        "**Document Class:** Canonical declarative\n"
        "**Authority:** System behavior\n"
        "**Owning Surface:** moonmind/system\n",
    )

    payload = mod.build_index(root=tmp_path)

    paths = {entry["path"] for entry in payload["documents"]}
    assert "docs/SystemDesign.md" in paths
    assert "docs/tmp/MigrationPlan.md" not in paths
    assert all("docs/tmp/" not in claim["anchor"] for claim in payload["claims"])


def test_cli_writes_json_under_artifact_path_without_mutating_docs(
    tmp_path: Path, monkeypatch
) -> None:
    _write(
        tmp_path / "docs" / "MoonMindArchitecture.md",
        "# MoonMind Architecture\n\n"
        "**Document Class:** System Architecture View\n"
        "**Authority:** System structure\n"
        "**Owning Surface:** MoonMind\n",
    )
    before = (tmp_path / "docs" / "MoonMindArchitecture.md").read_text(encoding="utf-8")
    output = tmp_path / "artifacts" / "doc-index" / "index.json"

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    rc = mod.main(["--output", str(output)])

    assert rc == 0
    assert output.is_file()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["documents"][0]["path"] == "docs/MoonMindArchitecture.md"
    after = (tmp_path / "docs" / "MoonMindArchitecture.md").read_text(encoding="utf-8")
    assert after == before


def test_default_cli_is_advisory_only_even_with_warnings(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path / "docs" / "Unclassified.md", "# Unclassified\n\nSome prose.\n")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

    rc = mod.main(["--output", str(tmp_path / "artifacts" / "index.json")])

    assert rc == 0
    payload = json.loads((tmp_path / "artifacts" / "index.json").read_text(encoding="utf-8"))
    warning_rules = {warning["rule"] for warning in payload["warnings"]}
    assert "missing-index-document-class" in warning_rules
    assert "missing-index-authority" in warning_rules


def test_heading_extraction_skips_fenced_code_and_accepts_indentation(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "docs" / "Indented.md",
        "# Real Document\n\n"
        "```bash\n"
        "# Not A Heading\n"
        "```\n\n"
        "   ## Indented Section\n\n"
        "~~~python\n"
        "### Also Not A Heading\n"
        "~~~\n",
    )

    payload = mod.build_index(root=tmp_path)

    anchors = {claim["anchor"] for claim in payload["claims"]}
    assert "docs/Indented.md#real-document" in anchors
    assert "docs/Indented.md#indented-section" in anchors
    assert "docs/Indented.md#not-a-heading" not in anchors
    assert "docs/Indented.md#also-not-a-heading" not in anchors


def test_duplicate_headings_get_unique_markdown_style_anchor_suffixes(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "docs" / "Roadmap.md",
        "# Roadmap\n\n## Remaining Work\n\nFirst.\n\n## Remaining Work\n\nSecond.\n",
    )

    payload = mod.build_index(root=tmp_path)

    claims = {claim["anchor"]: claim for claim in payload["claims"]}
    assert "docs/Roadmap.md#remaining-work" in claims
    assert "docs/Roadmap.md#remaining-work-1" in claims
    assert (
        claims["docs/Roadmap.md#remaining-work"]["id"]
        != claims["docs/Roadmap.md#remaining-work-1"]["id"]
    )


def test_explicit_paths_are_normalized_relative_to_repo_root(tmp_path: Path) -> None:
    doc_path = tmp_path / "docs" / "Explicit.md"
    _write(doc_path, "# Explicit\n\nSome prose.\n")

    payload = mod.build_index(
        paths=["./docs/Explicit.md", str(doc_path)],
        root=tmp_path,
    )

    assert payload["documentCount"] == 1
    assert payload["documents"][0]["path"] == "docs/Explicit.md"
