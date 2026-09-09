"""Lightweight deployment-gate contracts.

This package intentionally keeps its ``__init__`` free of imports so that
gate modules stay importable from dependency-constrained tooling contexts
(standard library only) without pulling Temporal, database, or settings
dependencies through a heavy package ``__init__`` chain.
"""

from __future__ import annotations

__all__: list[str] = []
