"""Effective native capability contract tests for MoonLadderStudios/MoonMind#3636."""

from moonmind.omnigent.effective_capabilities import (
    CAPABILITY_NAMES,
    CAPABILITY_SCHEMA_VERSION,
    resolve_effective_capabilities,
)


def _authority(**overrides):
    value = {
        "agentProfileRef": "agent-profile://p/versions/7",
        "agentProfileDigest": "sha256:agent",
        "providerProfileId": "provider-1",
        "providerProfileGeneration": 4,
        "launchPolicyRef": "policy://launch/3",
        "policySnapshotRef": "artifact://policy",
        "policyDigest": "sha256:policy",
        "effectiveLaunchSnapshotRef": "artifact://launch",
        "sessionEpoch": 2,
        "authorityFresh": True,
    }
    value.update(overrides)
    return value


def _all(value=True):
    return {name: value for name in CAPABILITY_NAMES}


def _resolve(**overrides):
    values = dict(
        authority=_authority(),
        upstream_capabilities=_all(),
        profile_capabilities=_all(),
        launch_capabilities=_all(),
        state_capabilities=_all(),
        caller_capabilities=_all(),
        session_status="active",
    )
    values.update(overrides)
    return resolve_effective_capabilities(**values)


def test_versioned_resolver_covers_complete_operation_matrix():
    result = _resolve()
    assert result.schema_version == CAPABILITY_SCHEMA_VERSION
    assert tuple(result.decisions) == CAPABILITY_NAMES
    assert all(result.capabilities.values())
    assert len(result.authority_digest) == 64


def test_each_authority_independently_removes_capability_with_stable_reason():
    callers = _all()
    callers["resolveElicitation"] = False
    decision = _resolve(caller_capabilities=callers).decisions["resolveElicitation"]
    assert decision.allowed is False
    assert decision.reason == "caller_not_authorized"
    assert decision.upstream_supported is True


def test_missing_and_stale_immutable_authority_fail_closed():
    missing = _resolve(authority=_authority(agentProfileDigest=None))
    assert set(missing.capabilities.values()) == {False}
    assert set(missing.disabled_reasons.values()) == {"immutable_authority_missing"}
    stale = _resolve(authority=_authority(expectedProviderProfileGeneration=5))
    assert set(stale.disabled_reasons.values()) == {"provider_generation_stale"}


def test_terminal_session_preserves_reads_but_denies_mutations():
    result = _resolve(session_status="completed")
    assert result.capabilities["viewTranscript"] is True
    assert result.capabilities["readResources"] is True
    assert result.capabilities["sendMessage"] is False
    assert result.decisions["sendMessage"].reason == "session_terminal"


def test_missing_source_entry_cannot_be_inferred_or_granted():
    upstream = _all()
    del upstream["changeModel"]
    result = _resolve(upstream_capabilities=upstream)
    assert result.capabilities["changeModel"] is False
    assert result.decisions["changeModel"].reason == "upstream_unsupported"
