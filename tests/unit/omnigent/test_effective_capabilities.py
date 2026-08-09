"""Effective native capability contract tests for MoonLadderStudios/MoonMind#3636."""

from types import SimpleNamespace

from moonmind.omnigent.effective_capabilities import (
    CAPABILITY_NAMES,
    CAPABILITY_SCHEMA_VERSION,
    adapt_provider_capabilities,
    caller_capabilities_for_bridge,
    resolve_bridge_row_capabilities,
    resolve_effective_capabilities,
)


def test_provider_capabilities_are_adapted_to_complete_canonical_namespace():
    adapted = adapt_provider_capabilities(
        {"sendFollowUp": True, "stop": True, "unknownProviderControl": True}
    )
    assert tuple(adapted) == CAPABILITY_NAMES
    assert adapted["sendMessage"] is True
    assert adapted["stopSession"] is True
    assert adapted["queueMessage"] is False
    assert "unknownProviderControl" not in adapted


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


def test_terminal_session_preserves_reads_and_post_terminal_lifecycle():
    result = _resolve(session_status="completed")
    assert result.capabilities["viewTranscript"] is True
    assert result.capabilities["readResources"] is True
    assert result.capabilities["sendMessage"] is False
    assert result.decisions["sendMessage"].reason == "session_terminal"
    assert result.capabilities["harvestEvidence"] is True
    assert result.capabilities["cleanupSession"] is True


def test_missing_source_entry_cannot_be_inferred_or_granted():
    upstream = _all()
    del upstream["changeModel"]
    result = _resolve(upstream_capabilities=upstream)
    assert result.capabilities["changeModel"] is False
    assert result.decisions["changeModel"].reason == "upstream_unsupported"


def test_bridge_row_adapter_uses_only_execution_bound_authority():
    grants = _all()
    row = SimpleNamespace(
        status="active",
        provider_profile_id="provider-1",
        credential_generation=4,
        effective_launch_snapshot_json={
            "executionProfileRef": "agent-profile://p/versions/7",
            "executionProfileDigest": "sha256:agent",
            "launchPolicyRef": "policy://launch/3",
            "snapshotRef": "artifact://launch",
            "policyAuthority": {
                "snapshotRef": "artifact://policy",
                "policyDigest": "sha256:policy",
            },
        },
        metadata_={
            # A contradictory legacy map must not become parallel authority.
            "interventionCapabilities": _all(False),
            "capabilityAuthority": {
                "fresh": True,
                "providerProfileGeneration": 4,
                "upstream": grants,
                "agentProfile": grants,
                "launchPolicy": grants,
                "state": {"sessionEpoch": 2, "capabilities": grants},
            },
        },
    )
    result = resolve_bridge_row_capabilities(row, caller_capabilities=grants)
    assert all(result.capabilities.values())

    stale = resolve_bridge_row_capabilities(
        row, caller_capabilities=grants, expected_session_epoch=3
    )
    assert set(stale.disabled_reasons.values()) == {"session_epoch_stale"}


def test_bridge_row_adapter_fails_closed_without_capability_authority():
    row = SimpleNamespace(
        status="active",
        provider_profile_id="provider-1",
        credential_generation=4,
        effective_launch_snapshot_json={},
        metadata_={"interventionCapabilities": _all()},
    )
    result = resolve_bridge_row_capabilities(row, caller_capabilities=_all())
    assert set(result.disabled_reasons.values()) == {"immutable_authority_missing"}


def test_caller_authority_separates_owner_from_approver_and_viewer():
    owner = SimpleNamespace(id="owner", is_superuser=False)
    row = SimpleNamespace(metadata_={})
    owner_grants = caller_capabilities_for_bridge(row, owner)
    assert owner_grants["sendMessage"] is False
    assert owner_grants["viewTranscript"] is True
    assert owner_grants["resolveElicitation"] is False
    assert owner_grants["cleanupSession"] is False

    row.metadata_ = {
        "callerAuthorities": {
            "viewer": {
                "viewTranscript": True,
                "readResources": True,
            }
        }
    }
    viewer_grants = caller_capabilities_for_bridge(
        row, SimpleNamespace(id="viewer", is_superuser=False)
    )
    assert viewer_grants["viewTranscript"] is True
    assert viewer_grants["sendMessage"] is False
    assert viewer_grants["writeTerminal"] is False

    approver_grants = caller_capabilities_for_bridge(
        SimpleNamespace(metadata_={}),
        SimpleNamespace(id="admin", is_superuser=True),
    )
    assert approver_grants["resolveElicitation"] is True
