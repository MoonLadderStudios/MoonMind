from __future__ import annotations

import pytest

from moonmind.workflows.temporal.runtime.strategies.codex_cli import (
    append_managed_codex_runtime_note,
)
from tests.integration.reliability.helpers import load_replay


pytestmark = [
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


def test_managed_container_instruction_overrides_direct_docker_replay() -> None:
    """Replay both Tactics failures at the managed prompt authority boundary."""

    replay_id = "managed-session-direct-docker-verification"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")

    rendered = append_managed_codex_runtime_note(
        manifest["verificationInstruction"],
        env_source={},
    )

    assert len(manifest["incidentWorkflowIds"]) == 2
    assert manifest["workloadMode"] == "container-jobs"
    assert "container-job boundary" in expected["invariant"]
    assert manifest["verificationInstruction"] in rendered
    for fragment in expected["requiredPromptFragments"]:
        assert fragment in rendered
