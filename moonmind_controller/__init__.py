"""Standalone deployment controller for MoonMind Compose stacks.

Stdlib-only on purpose: normal controller execution must not import MoonMind
application modules nor require its API, DB, Temporal, artifact service,
provider manager, Omnigent, or an LLM (MoonLadderStudios/MoonMind#4500).
Release configuration arrives as data (see :mod:`state`), never by importing
the target application's Python bootstrap.
"""

from __future__ import annotations
