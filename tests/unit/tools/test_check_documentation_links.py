"""MM-3966 bounded local documentation link/anchor verifier tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[3] / "tools" / "check_documentation_links.py"
)
SPEC = importlib.util.spec_from_file_location(
    "check_documentation_links", MODULE_PATH
)
assert SPEC is not None
check_links = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = check_links
SPEC.loader.exec_module(check_links)

mod = check_links
DocFile = mod.DocFile


def _doc(path: str, text: str) -> "mod.DocFile":
    return DocFile(path=path, text=text)


def _rules(findings) -> set[str]:
    return {finding.rule for finding in findings}


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_broken_relative_link_is_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "docs/Exists.md", "# Exists\n")
    docs = [_doc("docs/Index.md", "# Index\n\nSee [gone](./Missing.md).\n")]
    findings, _ = mod.run_checks(docs, root=tmp_path)
    assert _rules(findings) == {"broken-local-link"}
    assert findings[0].severity == mod.SEVERITY_ADVISORY


def test_valid_relative_link_image_and_anchor_pass(tmp_path: Path) -> None:
    _write(tmp_path, "docs/Target.md", "# Real Heading\n")
    _write(tmp_path, "docs/assets/pic.png", "fake-png")
    docs = [
        _doc(
            "docs/Index.md",
            "# Index\n\n"
            "See [target](./Target.md) and "
            "[section](./Target.md#real-heading).\n\n"
            "![pic](./assets/pic.png)\n",
        )
    ]
    findings, _ = mod.run_checks(docs, root=tmp_path)
    assert findings == []


def test_broken_anchor_is_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "docs/Target.md", "# Real Heading\n")
    docs = [_doc("docs/Index.md", "# Index\n\nSee [x](./Target.md#no-such-part).\n")]
    findings, _ = mod.run_checks(docs, root=tmp_path)
    assert _rules(findings) == {"broken-local-anchor"}


def test_anchor_slug_maps_each_space_to_one_hyphen(tmp_path: Path) -> None:
    # GitHub does not collapse space runs: "CLI & API Usage" -> "cli--api-usage".
    _write(tmp_path, "docs/Target.md", "## CLI & API Usage\n")
    docs = [_doc("docs/Index.md", "# Index\n\nSee [x](./Target.md#cli--api-usage).\n")]
    findings, _ = mod.run_checks(docs, root=tmp_path)
    assert findings == []


def test_code_fence_example_paths_are_not_link_checked(tmp_path: Path) -> None:
    # A tmp working doc may legitimately show "../X.md" inside an example
    # header; fences are classified separately, not link-checked.
    docs = [
        _doc(
            "docs/Standard.md",
            "# Standard\n\n```markdown\n"
            "**Canonical Target:** [Doc](../Missing.md)\n"
            "```\n",
        )
    ]
    findings, _ = mod.run_checks(docs, root=tmp_path)
    assert findings == []


def test_external_urls_are_counted_not_fetched(tmp_path: Path) -> None:
    docs = [
        _doc(
            "docs/Index.md",
            "# Index\n\nSee [web](https://example.invalid/x) and [mail](mailto:a@b.c).\n",
        )
    ]
    findings, external = mod.run_checks(docs, root=tmp_path)
    assert findings == []
    assert external == 2


def test_reference_style_defined_passes_undefined_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "docs/Real.md", "# Real\n")
    docs = [
        _doc(
            "docs/Index.md",
            "# Index\n\nSee [ok][good] and [bad][missing].\n\n[good]: ./Real.md\n",
        )
    ]
    findings, _ = mod.run_checks(docs, root=tmp_path)
    assert _rules(findings) == {"undefined-link-reference"}
    assert "missing" in findings[0].message


def test_broken_reference_definition_target_is_flagged(tmp_path: Path) -> None:
    docs = [_doc("docs/Index.md", "# Index\n\nSee [x][gone].\n\n[gone]: ./Missing.md\n")]
    findings, _ = mod.run_checks(docs, root=tmp_path)
    assert _rules(findings) == {"broken-local-link"}


def test_frozen_review_evidence_is_out_of_scope(tmp_path: Path) -> None:
    # docs/DocsReview.md is a frozen evidence snapshot: dispositioned, never
    # repaired in place, and excluded from link findings.
    assert mod.is_link_scope("docs/DocsReview.md") is False
    assert mod.is_link_scope("docs/tmp/historical/Old.md") is False
    assert mod.is_link_scope("docs/MoonMindArchitecture.md") is True
    docs = [_doc("docs/DocsReview.md", "# Review\n\nSee [gone](./Missing.md).\n")]
    findings, _ = mod.run_checks(docs, root=tmp_path)
    assert findings == []


def test_missing_document_target_reports_resolved_path(tmp_path: Path) -> None:
    docs = [_doc("docs/a/B.md", "# B\n\nSee [gone](../Missing.md).\n")]
    findings, _ = mod.run_checks(docs, root=tmp_path)
    assert len(findings) == 1
    assert "docs/Missing.md" in findings[0].detail


def test_explicit_anchor_tag_satisfies_check(tmp_path: Path) -> None:
    _write(
        tmp_path, "docs/Target.md", '# T\n\n<a name="custom-anchor"></a>\nBody.\n'
    )
    docs = [_doc("docs/Index.md", "# I\n\nSee [x](./Target.md#custom-anchor).\n")]
    findings, _ = mod.run_checks(docs, root=tmp_path)
    assert findings == []


def test_checker_is_advisory_only(tmp_path: Path) -> None:
    assert mod.main(["--scope", "all"]) == 0
