"""Executable Skill-contract and production-boundary guards for #3964.

``.agents/skills/**/SKILL.md`` files are executable portable contracts, not
narrative documentation: resolution, materialization, and workflow selection
consume their frontmatter (``name``/``description``). This suite validates
them through the production frontmatter loader
(``moonmind.services.skill_resolution._load_skill_frontmatter``) rather than
a test-only parser, so a bug or drift in the shared implementation is caught
by negative cases instead of comparing the implementation to itself.

Production-boundary twins (secret redaction, caller-authored hostId
rejection, no-silent-fallback error codes) prove the semantic guards hold in
code, so deleting a documentation assertion cannot let an authority or
security change pass silently.

Mutation/regression proof for the ``_semantic_docs_3964`` helpers lives here:
reworded prose carrying the same concepts passes, while text with a concept
removed fails.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from _semantic_docs_3964 import (  # noqa: E402
    assert_semantic_absent,
    assert_semantic_present,
    normalize,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"


def _production_skill_loader():
    """Lazy import: production resolution pulls settings; keep collection light."""
    from moonmind.services.skill_resolution import (  # noqa: E402
        _AGENT_SKILL_NAME_RE,
        _load_skill_frontmatter,
    )

    return _AGENT_SKILL_NAME_RE, _load_skill_frontmatter


def _skill_dirs() -> list[Path]:
    return sorted(
        p for p in SKILLS_ROOT.iterdir() if p.is_dir() and (p / "SKILL.md").exists()
    )


def test_skill_snapshot_root_resolves() -> None:
    assert SKILLS_ROOT.is_dir()
    assert _skill_dirs(), "no skills found under .agents/skills"


def test_every_skill_frontmatter_parses_via_production_loader() -> None:
    _AGENT_SKILL_NAME_RE, _load_skill_frontmatter = _production_skill_loader()
    for skill_dir in _skill_dirs():
        if skill_dir.name == "local":
            continue
        frontmatter = _load_skill_frontmatter(skill_dir)
        assert isinstance(frontmatter, dict), skill_dir.name
        assert frontmatter, f"{skill_dir.name}: empty frontmatter"
        assert _AGENT_SKILL_NAME_RE.fullmatch(skill_dir.name), skill_dir.name
        assert frontmatter.get("name") == skill_dir.name, (
            f"{skill_dir.name}: frontmatter name must match directory"
        )
        description = frontmatter.get("description")
        assert isinstance(description, str) and description.strip(), (
            f"{skill_dir.name}: non-empty description required"
        )


def test_every_skill_body_carries_executable_contract_sections() -> None:
    for skill_dir in _skill_dirs():
        if skill_dir.name == "local":
            continue
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert_semantic_present(
            text,
            ("## ",),
            context=f"{skill_dir.name}/SKILL.md headed sections",
        )
        lowered = normalize(text)
        assert any(term in lowered for term in ("workflow", "inputs", "output")), (
            f"{skill_dir.name}/SKILL.md must define workflow/inputs/output semantics"
        )


def test_production_frontmatter_loader_rejects_malformed_input(tmp_path: Path) -> None:
    """Negative case against the shared loader (not a test-only duplicate)."""
    _, _load_skill_frontmatter = _production_skill_loader()
    bad = tmp_path / "bad-skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text(
        "---\nname: [unclosed\n description: x\n---\n\n# Bad\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid YAML frontmatter"):
        _load_skill_frontmatter(bad)

    non_mapping = tmp_path / "list-skill"
    non_mapping.mkdir()
    (non_mapping / "SKILL.md").write_text(
        "---\n- just\n- a\n- list\n---\n\n# Bad\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must be a mapping"):
        _load_skill_frontmatter(non_mapping)

    missing = tmp_path / "missing-skill"
    missing.mkdir()
    with pytest.raises(ValueError, match="failed to read skill frontmatter"):
        _load_skill_frontmatter(missing)


def test_skill_name_pattern_rejects_invalid_names() -> None:
    _AGENT_SKILL_NAME_RE, _ = _production_skill_loader()
    assert _AGENT_SKILL_NAME_RE.fullmatch("fix-ci") is not None
    assert _AGENT_SKILL_NAME_RE.fullmatch("moonspec-verify") is not None
    assert _AGENT_SKILL_NAME_RE.fullmatch("Bad Name!") is None
    assert _AGENT_SKILL_NAME_RE.fullmatch("") is None


# --- Mutation/regression proof for the semantic helpers ---


def test_semantic_helper_survives_harmless_rewording() -> None:
    original = "The shared image never authorizes every installed runtime."
    reworded = "A  shared\nimage   must NEVER grant authorization to all installed runtimes!"
    assert_semantic_present(
        reworded, ("never", "authoriz", "installed", "runtime"), context="reword"
    )
    assert original != reworded  # the wordings genuinely differ


def test_semantic_helper_still_detects_concept_removal() -> None:
    mutated = "The shared image supports several installed runtimes."
    with pytest.raises(AssertionError, match="missing required concept"):
        assert_semantic_present(
            mutated,
            ("never", "authoriz", "installed", "runtime"),
            context="mutated",
        )


def test_semantic_absent_helper_detects_reintroduced_concept() -> None:
    clean = "The directory is an ownership surface."
    assert_semantic_absent(clean, ("bounded context",), context="clean")
    mutated = "The directory is a Bounded Context."
    with pytest.raises(AssertionError, match="forbidden concept"):
        assert_semantic_absent(mutated, ("bounded context",), context="mutated")


# --- Production-boundary twins: security/authority holds in code ---


def test_secret_redaction_boundary_catches_credential_shapes() -> None:
    from moonmind.omnigent.conformance import SECRET_PATTERN

    for sample in (
        "token ghp_abc123def456",
        "key github_pat_xyz789",
        "-----BEGIN RSA PRIVATE KEY-----",
        "password = hunter2",
    ):
        assert SECRET_PATTERN.search(sample) is not None, sample
    assert SECRET_PATTERN.search("ordinary prose about authentication") is None


def test_caller_authored_host_id_rejected_in_production_adapter() -> None:
    from moonmind.workflows.adapters.omnigent_agent_adapter import (
        OmnigentAdapterError,
        OmnigentSessionSelection,
    )

    selection = OmnigentSessionSelection(
        host_type="managed",
        host_id="caller-picks-this",
        workspace=None,
        allow_empty_workspace=True,
    )
    from moonmind.workflows.adapters.omnigent_agent_adapter import _validate_session

    with pytest.raises(OmnigentAdapterError, match="reject caller-provided hostId"):
        _validate_session(selection)


def test_no_silent_fallback_error_classes_exist_in_production() -> None:
    from moonmind.omnigent.failure_classification import (
        OMNIGENT_FAILURE_CLASS_TABLE,
        OmnigentFailureReason,
        classify_omnigent_failure,
    )

    # The production failure taxonomy must cover the fail-closed surface the
    # docs describe (auth, invalid payload, host timeout, ambiguous first
    # message): an explicit selection maps to a failure class, never to a
    # silent substitution.
    assert OmnigentFailureReason.AUTH_FAILURE in OMNIGENT_FAILURE_CLASS_TABLE
    assert OmnigentFailureReason.INVALID_SESSION_PAYLOAD in (
        OMNIGENT_FAILURE_CLASS_TABLE
    )
    assert OmnigentFailureReason.SESSION_HOST_TIMEOUT in OMNIGENT_FAILURE_CLASS_TABLE
    assert (
        classify_omnigent_failure(OmnigentFailureReason.AUTH_FAILURE)
        == "integration_error"
    )
    assert (
        classify_omnigent_failure(OmnigentFailureReason.INVALID_SESSION_PAYLOAD)
        == "user_error"
    )
