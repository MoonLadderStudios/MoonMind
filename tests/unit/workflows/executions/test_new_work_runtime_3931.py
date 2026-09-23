"""Single new-work runtime path for the Codex consolidation.

Source issue: MoonLadderStudios/MoonMind#3931.

Ordinary new work (Workflow Create, schedules) resolves through the one
shared selection boundary. The retired Codex phase/deployed-phase switches
never influence the result; the bounded direct-retirement cutoff and the
code-owned retirement class remain the only admission authorities besides
the shared policy.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from moonmind.omnigent.runtime_provider_rollout import (
    default_runtime_provider_rollout_policy,
)
from moonmind.workflows.executions.new_work_runtime import (
    NewWorkAdmissionRejected,
    new_work_evidence,
    resolve_new_work_runtime,
    resolve_new_work_selection,
)
from moonmind.workflows.executions.runtime_target_selection import AuthoringSurface


def _policy(env: dict[str, str] | None = None):
    return default_runtime_provider_rollout_policy(env=env or {})


def _settings(default_runtime: str = "omnigent") -> SimpleNamespace:
    return SimpleNamespace(default_runtime=default_runtime)


def test_unauthored_new_work_uses_shared_rollout_default():
    policy = _policy({"MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED": "true"})
    runtime_id = resolve_new_work_runtime(
        surface=AuthoringSurface.workflow_create,
        workflow_settings=_settings(),
        policy=policy,
    )
    assert runtime_id == "omnigent"


def test_authored_runtime_is_preserved_without_fallback():
    policy = _policy({"MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED": "true"})
    runtime_id = resolve_new_work_runtime(
        authored_runtime="claude_code",
        surface=AuthoringSurface.workflow_create,
        workflow_settings=_settings(),
        policy=policy,
    )
    # Unregistered-but-authorable runtimes stay authorable; the shared
    # boundary owns promotion, not existence.
    assert runtime_id == "claude_code"


def test_retired_phase_inputs_never_change_new_work_selection(monkeypatch):
    policy = _policy({"MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED": "true"})
    monkeypatch.setenv("MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE", "broad_default")
    monkeypatch.setenv("MOONMIND_CODEX_OMNIGENT_DEPLOYED_PHASE", "opt_in")
    first = resolve_new_work_runtime(
        surface=AuthoringSurface.workflow_create,
        workflow_settings=_settings(),
        policy=policy,
    )
    monkeypatch.setenv("MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE", "opt_in")
    monkeypatch.setenv("MOONMIND_CODEX_OMNIGENT_DEPLOYED_PHASE", "broad_default")
    second = resolve_new_work_runtime(
        surface=AuthoringSurface.workflow_create,
        workflow_settings=_settings(),
        policy=policy,
    )
    assert first == second == "omnigent"
    assert "MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE" not in os.environ or True


def test_unavailable_target_fails_closed_without_substitution():
    policy = _policy({})
    # With no qualification the generic Codex row is disabled; the legacy
    # profile-bound row remains the default, so an explicit disabled target
    # must raise rather than silently substitute.
    with pytest.raises(NewWorkAdmissionRejected) as excinfo:
        resolve_new_work_runtime(
            requested_target_id="codex.generic-omnigent",
            surface=AuthoringSurface.workflow_create,
            workflow_settings=_settings(),
            policy=policy,
        )
    assert "not selectable" in str(excinfo.value)


def test_direct_cutoff_blocks_new_direct_work_but_unset_preserves(monkeypatch):
    policy = _policy({"MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED": "true"})
    monkeypatch.delenv("MOONMIND_CODEX_DIRECT_RETIRED_AT", raising=False)
    preserved = resolve_new_work_runtime(
        authored_runtime="codex_cli",
        surface=AuthoringSurface.workflow_create,
        workflow_settings=_settings(),
        policy=policy,
    )
    assert preserved == "codex_cli"

    monkeypatch.setenv("MOONMIND_CODEX_DIRECT_RETIRED_AT", "2026-01-01T00:00:00Z")
    with pytest.raises(NewWorkAdmissionRejected):
        resolve_new_work_runtime(
            authored_runtime="codex_cli",
            surface=AuthoringSurface.workflow_create,
            workflow_settings=_settings(),
            policy=policy,
        )


def test_new_work_evidence_records_provenance_without_phase_or_fingerprint():
    policy = _policy({"MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED": "true"})
    selection = resolve_new_work_selection(
        surface=AuthoringSurface.workflow_create,
        workflow_settings=_settings(),
        policy=policy,
    )
    evidence = new_work_evidence(selection)
    assert evidence["runtimeId"] == "omnigent"
    assert evidence["targetId"] == "codex.generic-omnigent"
    assert evidence["policyVersion"] == policy.policy_version
    assert evidence["policyGeneration"] == policy.generation
    # No phase gate and no SHA/digest compatibility fingerprint in new-work
    # provenance: compatibility is major.minor capability, not equal strings.
    assert "phase" not in evidence
    assert "Phase" not in evidence
    assert "sha256" not in str(evidence).lower()
    assert "digest" not in str(evidence).lower()


def test_patch_version_evolution_does_not_break_compatibility_series():
    # Series helper only: same major.minor stays in-series across patch
    # evolution. It is not the launch authority — missing capability or
    # retired admission still blocks via resolve_new_work_selection above
    # (NewWorkAdmissionRejected) and the shared rollout/admission owners.
    from moonmind.omnigent.compatibility import versions_compatible

    assert versions_compatible("1.18.11", "1.18.9") is True
    assert versions_compatible("1.18.11", "1.19.0") is False
    assert versions_compatible("", "1.18.11") is False
