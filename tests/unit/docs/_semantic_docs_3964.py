"""Shared semantic-assertion helpers for issue #3964.

Classification (inventory of ``tests/unit/docs`` assertions):

- Incidental prose (loosened by callers): exact long sentences whose wording
  can change without changing the contract, e.g. em-dash phrasing, "promote
  or supersede ...", "never authorizes every installed runtime", duplicated
  fail-closed invariant paragraphs. Replaced with normalized term
  co-occurrence checks below.
- Structural metadata/schema (kept, exact): frontmatter keys
  (``**Document Class:**``), stable claim-ID headings (``DOC-REQ-001``),
  ``schemaVersion`` counts, JSON example validity, template header fields.
- Internal links/anchors (kept): ``tools/check_documentation_links.py`` plus
  per-suite anchor resolution tests.
- Generated references / registry drift (kept): ``#3959`` catalog checks and
  the ``#3960`` production-derived reconciliation suite.
- Executable examples (kept): JSON examples validated against production
  models (``AgentExecutionRequest``), fenced code blocks parsed as JSON/YAML.
- Skill frontmatter/instructions (kept, strengthened): see
  ``test_skill_contracts_3964.py``, which validates ``SKILL.md`` through the
  production frontmatter loader.
- Guards standing in for production behavior (kept AND duplicated at the
  owning boundary): secret-shape scans, caller-authored hostId rejection,
  no-silent-fallback. The production-boundary twins live in
  ``test_skill_contracts_3964.py`` so deleting a doc assertion cannot hide a
  semantic change.

Helpers normalize whitespace and case so harmless rewording and line-wrap
changes pass, while removal of any required concept still fails. Each helper
has mutation/regression coverage in ``test_skill_contracts_3964.py`` (reworded
text passes, concept-removed text fails).
"""

from __future__ import annotations

import re

_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Collapse whitespace and lowercase for wording-tolerant comparison."""
    return _WS_RE.sub(" ", text).strip().lower()


def assert_semantic_present(
    text: str, required_terms: tuple[str, ...], *, context: str = ""
) -> None:
    """Assert every required concept term survives in ``text``.

    Terms are short normalized keywords, not sentences, so rewording the
    surrounding prose does not break the check. Removing the concept (all
    occurrences of any term) fails with an actionable message naming the
    missing term and the owning context.
    """
    normalized = normalize(text)
    for term in required_terms:
        needle = normalize(term)
        assert needle in normalized, (
            f"missing required concept {term!r}"
            + (f" in {context}" if context else "")
            + "; the owning document must still state this contract"
        )


def assert_semantic_absent(
    text: str, forbidden_terms: tuple[str, ...], *, context: str = ""
) -> None:
    """Assert no forbidden concept term appears in ``text`` (normalized)."""
    normalized = normalize(text)
    for term in forbidden_terms:
        needle = normalize(term)
        assert needle not in normalized, (
            f"forbidden concept {term!r}"
            + (f" in {context}" if context else "")
            + " must stay absent"
        )
