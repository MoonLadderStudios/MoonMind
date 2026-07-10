"""Provider verification for the live managed Codex long-command canary.

This test validates the compact evidence emitted by
``tools/run_codex_conformance_canary.py``. It is skipped unless a credentialed
environment supplies ``MOONMIND_CODEX_CANARY_RESULT_PATH``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from moonmind.codex_conformance.canary import validate_canary_evidence

_RESULT_PATH = os.environ.get("MOONMIND_CODEX_CANARY_RESULT_PATH", "").strip()
_CANDIDATE_DIGEST = os.environ.get("MOONMIND_CODEX_CANARY_CANDIDATE_DIGEST", "").strip()

pytestmark = [
    pytest.mark.provider_verification,
    pytest.mark.codex,
    pytest.mark.requires_credentials,
    pytest.mark.skipif(
        not _RESULT_PATH,
        reason="MOONMIND_CODEX_CANARY_RESULT_PATH not set",
    ),
]


def test_live_codex_long_command_canary_evidence() -> None:
    path = Path(_RESULT_PATH)
    evidence = json.loads(path.read_text(encoding="utf-8"))

    result = validate_canary_evidence(
        evidence,
        expected_candidate_digest=_CANDIDATE_DIGEST or None,
    )

    assert result.passed, result.model_dump(mode="json", by_alias=True)
