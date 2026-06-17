"""Shared tokenization for the ``mm_title`` workflow-title search attribute.

Temporal SQL (PostgreSQL) advanced visibility supports neither the ``LIKE``
operator nor substring matching, so workflow titles cannot be filtered by an
arbitrary substring. Instead the title is stored as a ``KeywordList`` search
attribute (``mm_title``) holding the title's word tokens, and operators
word-match it with ``=`` (KeywordList ``=`` tests membership).

Both sides of that contract must tokenize identically:

* the run workflow populates ``mm_title`` from this function, and
* the executions list endpoint tokenizes the operator's filter text the same
  way before emitting ``mm_title = "<token>"`` clauses.

Keep this module dependency-free (standard library only) so it is safe to import
inside the Temporal workflow sandbox.
"""

from __future__ import annotations

import re

# Lowercased alphanumeric runs. Punctuation and whitespace are token
# boundaries, so "MM-823: post-merge" -> ["mm", "823", "post", "merge"].
_TITLE_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Defensive cap so an unexpectedly long title cannot blow past Temporal's
# per-search-attribute size limits. Titles are short in practice.
_MAX_TITLE_TOKENS = 50


def tokenize_title(text: str | None) -> list[str]:
    """Return the deduplicated, lowercased word tokens of ``text``.

    Order is preserved and duplicates are dropped. Returns an empty list for
    blank input or input with no alphanumeric content.
    """
    if not text:
        return []
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _TITLE_TOKEN_RE.findall(str(text).lower()):
        if match in seen:
            continue
        seen.add(match)
        tokens.append(match)
        if len(tokens) >= _MAX_TITLE_TOKENS:
            break
    return tokens
