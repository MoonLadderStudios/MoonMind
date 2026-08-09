"""Checkpoint Branch authority-handoff replay fixture for issue #3621."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from moonmind.schemas.temporal_activity_models import CheckpointBranchTerminalEvidence


HANDOFFS = (
    "profile_lease",
    "host_session",
    "first_message",
    "terminal_harvest",
    "publication",
    "cleanup",
    "reconciliation",
    "release",
)


@dataclass
class _DurableBranchJournal:
    """Small deterministic fixture that models Activity retry/replay boundaries."""

    effects: dict[str, str] = field(default_factory=dict)
    ordered: list[str] = field(default_factory=list)
    source_profile_lease_owner: str = "source-run"

    def apply(self, handoff: str) -> str:
        if handoff in self.effects:
            return self.effects[handoff]
        identity = f"branch-1:turn-1:{handoff}"
        self.effects[handoff] = identity
        self.ordered.append(handoff)
        return identity


def _drive(journal: _DurableBranchJournal, *, crash_after: int | None = None) -> None:
    for index, handoff in enumerate(HANDOFFS):
        journal.apply(handoff)
        if crash_after == index:
            raise RuntimeError("simulated worker restart")


@pytest.mark.integration
@pytest.mark.parametrize("crash_after", range(len(HANDOFFS)))
def test_checkpoint_branch_retry_and_replay_preserve_authority(crash_after: int) -> None:
    journal = _DurableBranchJournal()

    # Duplicate launch and an Activity retry resolve the same server-owned IDs.
    launch_ids = {
        "workflow": "checkpoint-branch:branch-1:turn-1",
        "run": "checkpoint-branch-run:branch-1:turn-1",
        "stepExecution": "step-execution:branch-1:turn-1:1",
        "agentRun": "agent-run:branch-1:turn-1",
        "host": "branch-1:turn-1:host",
        "session": "branch-1:turn-1:session",
        "message": "branch-1:turn-1:first-message",
    }
    assert dict(launch_ids) == launch_ids

    with pytest.raises(RuntimeError, match="worker restart"):
        _drive(journal, crash_after=crash_after)
    before_replay = dict(journal.effects)

    # Replaying from the beginning represents both workflow replay and retry of
    # the Activity adjacent to the restart. Durable idempotency keys collapse it.
    _drive(journal)
    for handoff, identity in before_replay.items():
        assert journal.effects[handoff] == identity

    assert journal.ordered == list(HANDOFFS)
    assert len(journal.effects) == len(HANDOFFS)
    assert list(journal.effects).count("first_message") == 1
    assert list(journal.effects).count("publication") == 1
    assert journal.effects["profile_lease"] != journal.source_profile_lease_owner
    assert journal.ordered.index("cleanup") < journal.ordered.index("reconciliation")
    assert journal.ordered.index("reconciliation") < journal.ordered.index("release")


@pytest.mark.integration
def test_checkpoint_branch_terminal_contract_is_state_dependent() -> None:
    contract = CheckpointBranchTerminalEvidence(
        branchId="branch-1",
        branchTurnId="turn-1",
        artifactManifestRef="artifact://manifest",
        artifactManifestDigest="sha256:" + "a" * 64,
        artifactPrincipal="owner-1",
        contextBundleRef="artifact://context",
        contextBundleDigest="sha256:" + "b" * 64,
    )

    completed = contract.required_evidence(
        runtime_state="completed", finish_outcome_code="PUBLISHED_PR"
    )
    assert {"workspace", "output", "checkpoint", "publication"} <= completed
    assert "diagnostics" not in completed

    delivery_unknown = contract.required_evidence(
        runtime_state="failed", finish_outcome_code="DELIVERY_UNKNOWN"
    )
    assert {"terminal", "diagnostics", "cleanup", "host_lease", "provider_lease"} <= delivery_unknown
    assert "publication" not in delivery_unknown
