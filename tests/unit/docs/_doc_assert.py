"""Shared assertion helpers for documentation contract tests.

MoonLadderStudios/MoonMind#3964: verbatim prose pins make harmless
documentation rewording expensive. These helpers assert the *semantic*
content of a documentation contract — normalized whitespace/case with
keyword conjunctions — instead of exact cosmetic wording. Machine-readable
tokens (tool names, JSON keys, error codes, CLI commands, URLs, claim IDs,
metadata markers, forbidden-vocabulary bans) must still be asserted exactly
at their owning boundary; only incidental prose goes through these helpers.
"""

from __future__ import annotations

import re
from pathlib import Path


def normalize(text: str) -> str:
    """Collapse all whitespace runs to single spaces and lowercase."""
    return re.sub(r"\s+", " ", text).strip().lower()


def assert_semantic_phrase(text: str, phrase: str, *, path: Path | str | None = None) -> None:
    """Assert ``phrase`` holds semantically, ignoring cosmetic wording.

    Every significant word (length > 2, minus punctuation) of ``phrase`` must
    appear in the normalized document text. Word order, exact punctuation,
    capitalization, and filler words may change without breaking the check,
    but no content word may be dropped.
    """
    haystack = normalize(text)
    words = [w for w in re.sub(r"[^\w\s-]", "", phrase.lower()).split() if len(w) > 2]
    assert words, f"empty semantic phrase for {path}"
    missing = [w for w in words if w not in haystack]
    assert not missing, f"{path or 'doc'} lost semantic content {missing} from {phrase!r}"


def assert_semantic_keywords(
    text: str, keywords: tuple[str, ...], *, path: Path | str | None = None
) -> None:
    """Assert every keyword appears in the normalized document text."""
    haystack = normalize(text)
    missing = [k for k in keywords if k.lower() not in haystack]
    assert not missing, f"{path or 'doc'} missing semantic keywords {missing}"
