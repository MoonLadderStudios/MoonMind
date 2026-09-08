"""Mutation coverage for the metadata guards replaced in #3964.

Invoke the same shipped-Skill tests against isolated content. Instructions stay
in the portable bundle; this harness adds no second parser or semantic rules.
"""

import re

import pytest

from moonmind.services.skill_resolution import _load_skill_frontmatter
from tests.unit.agents import (
    test_document_author_skill as author,
    test_document_health_remediate_skill as remediate,
    test_document_health_review_skill as review,
)


@pytest.fixture(
    params=[
        (author, author.test_document_author_skill_exists_with_front_matter),
        (
            remediate,
            remediate.test_front_matter_defines_name_description_and_git_capability,
        ),
        (review, review.test_document_health_review_front_matter),
    ]
)
def contract(request, tmp_path, monkeypatch):
    module, check = request.param
    original = module._SKILL_PATH.read_text(encoding="utf-8")
    path = tmp_path / "SKILL.md"
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(module, "_SKILL_PATH", path)
    return path, original, check


@pytest.mark.parametrize("source_quote", ["", "'", '"'])
@pytest.mark.parametrize("target_quote", ["", "'", '"'])
def test_description_rewording_and_yaml_quoting_preserve_contract(
    contract, source_quote, target_quote
):
    path, original, check = contract
    name = _load_skill_frontmatter(path.parent)["name"]
    source = re.sub(
        r"(?m)^name:.*$",
        lambda _: f"name: {source_quote}{name}{source_quote}",
        original,
    )
    path.write_text(source, encoding="utf-8")
    check()

    # Decode the source scalar before rendering it, including already-quoted names.
    name = _load_skill_frontmatter(path.parent)["name"]
    rewritten = re.sub(
        r"(?m)^description:.*$",
        "description: Help maintain repository documents.",
        source,
    )
    rewritten = re.sub(
        r"(?m)^name:.*$",
        lambda _: f"name: {target_quote}{name}{target_quote}",
        rewritten,
    )
    path.write_text(rewritten, encoding="utf-8")
    check()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-frontmatter",
        "unclosed-frontmatter",
        "wrong-name",
        "empty-description",
        "scalar-capabilities",
        "missing-git",
        "invalid-yaml",
    ],
)
def test_metadata_guard_rejects_original_contract_violations(contract, mutation):
    path, original, check = contract
    if mutation == "missing-frontmatter":
        changed = original.split("---", 2)[2]
    elif mutation == "unclosed-frontmatter":
        changed = original.replace("\n---\n", "\n", 1)
    elif mutation == "wrong-name":
        changed = re.sub(r"(?m)^name:.*$", "name: incorrect-skill", original)
    elif mutation == "empty-description":
        changed = re.sub(r"(?m)^description:.*$", 'description: ""', original)
    elif mutation == "scalar-capabilities":
        changed = re.sub(
            r"required-capabilities:\s*\n\s*- git",
            "required-capabilities: git",
            original,
        )
    elif mutation == "missing-git":
        changed = original.replace("- git", "- gh", 1)
    else:
        changed = re.sub(r"(?m)^name:.*$", "name: [", original)
    assert changed != original
    path.write_text(changed, encoding="utf-8")
    with pytest.raises((AssertionError, KeyError, ValueError)):
        check()


def test_portable_authoring_prohibition_is_still_enforced(tmp_path, monkeypatch):
    original = author._SKILL_PATH.read_text(encoding="utf-8")
    changed = original.replace("Do not create `spec.md`", "Create `spec.md`")
    assert changed != original
    path = tmp_path / "SKILL.md"
    path.write_text(changed, encoding="utf-8")
    monkeypatch.setattr(author, "_SKILL_PATH", path)
    with pytest.raises(AssertionError):
        author.test_document_author_never_creates_docs_native_spec()
