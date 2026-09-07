"""Positive and mutation controls for the meaningful Markdown verifiers."""

from pathlib import Path

import pytest

from tools import check_documentation_architecture as architecture
from tools import check_documentation_links as links

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "field,value",
    [
        ("Document Class", "Canonical declarative"),
        ("Viewpoint", "Module Architecture View"),
        ("Status", "Proposed"),
        ("Authority", "The schema owner"),
    ],
)
def test_metadata_is_independent_of_markdown_decoration(field, value):
    for line in (
        f"**{field}:** {value}",
        f"- **{field}:** {value}",
        f"{field}: {value}",
    ):
        assert (
            architecture.metadata_fields(
                f"# Title\n\n{line}\n\n## Body\nReworded prose"
            )[field.lower()]
            == value
        )


def test_body_metadata_cannot_satisfy_a_missing_header():
    assert "status" not in architecture.metadata_fields(
        "# Title\n\n## Body\nStatus: Accepted"
    )
    assert "status" not in architecture.metadata_fields(
        "# Title\n\n```markdown\nStatus: Accepted\n```"
    )


def test_metadata_and_claim_violations_remain_detectable():
    original = architecture.DocFile(
        "docs/ExampleContract.md",
        "# Example\nDocument Class: Canonical declarative\nAuthority: schema owner\n\n### INV-001 Rule\n",
    )
    assert architecture.run_checks([original]) == []
    mutations = [
        (
            original.text.replace("Canonical declarative", "invented-class"),
            "invalid-document-class",
        ),
        (original.text.replace("Canonical declarative", ""), "invalid-document-class"),
        (
            original.text.replace("Canonical declarative", "not Canonical declarative"),
            "invalid-document-class",
        ),
        (
            original.text.replace(
                "Canonical declarative", "Canonical declarativegarbage"
            ),
            "invalid-document-class",
        ),
        (
            original.text.replace("Document Class: Canonical declarative\n", ""),
            "missing-document-class",
        ),
        (
            original.text.replace("Authority: schema owner\n", ""),
            "contract-missing-authority-statement",
        ),
        (original.text.replace("INV-001", "INV-bad"), "malformed-claim-id"),
        (original.text + "\n### INV-001 Duplicate\n", "duplicate-claim-id"),
    ]
    for text, rule in mutations:
        assert rule in {
            finding.rule
            for finding in architecture.run_checks(
                [architecture.DocFile(original.path, text)]
            )
        }


def test_required_local_link_and_anchor_checks():
    # Call the existing checker, whose CLI remains advisory. In required unit
    # CI, its findings are failures. External URLs are deliberately not fetched.
    docs = links.load_docs(links.all_doc_paths(root=ROOT), root=ROOT)
    findings, _ = links.run_checks(docs, root=ROOT)
    assert findings == [], [(f.path, f.rule, f.message) for f in findings]


@pytest.mark.parametrize(
    "replacement,rule",
    [
        ("missing.md#stable-anchor", "broken-local-link"),
        ("target.md#missing", "broken-local-anchor"),
    ],
)
def test_link_mutations_fail_the_existing_checker(tmp_path, replacement, rule):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "target.md").write_text("# Stable anchor\n", encoding="utf-8")
    text = "# Guide\n[Contract](target.md#stable-anchor)\n"
    original = links.DocFile("docs/Guide.md", text)
    assert links.run_checks([original], root=tmp_path)[0] == []
    mutated = links.DocFile(
        original.path, text.replace("target.md#stable-anchor", replacement)
    )
    assert rule in {f.rule for f in links.run_checks([mutated], root=tmp_path)[0]}
