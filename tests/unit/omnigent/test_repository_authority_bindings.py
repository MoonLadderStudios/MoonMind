"""Versioned execution bindings for model and repository authority.

MoonLadderStudios/MoonMind#4009 (plan slice 2 of
docs/RepositoryAccessAndWorkspaceDesign.md CONTRACT-008): the existing
credential-binding envelope carries independently admitted model and
repository authority without giving repository credentials
model-capacity semantics or rewriting retained plans.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from moonmind.omnigent.harness_platform import credential_bindings as cb
from moonmind.omnigent.harness_platform.credential_bindings import (
    CredentialBinding,
    CredentialBindingSet,
    ModelAuthorityBinding,
    RepositoryAuthorityBinding,
    compute_binding_set_digest,
    create_binding_set,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError


V1_MODEL_SLOT = {
    "providerProfileRef": "opencode-go-default",
    "materializerRef": "opencode-auth-json@1",
}

REPO_SNAPSHOT = "repository-access-snapshot:sha256:" + "c" * 64


def _v2_repo_slot(**overrides):
    payload = {
        "authorityKind": "repository_connection",
        "connectionRef": "repo-conn-main",
        "repositoryAccessSnapshotRef": REPO_SNAPSHOT,
        "materializerRef": "repository-broker@1",
        "repositoryRole": "source_read",
    }
    payload.update(overrides)
    return payload


def test_historical_v1_digest_algorithm_is_frozen():
    """REQ-02/ACC-03: the original v1 decoder/hash verifies historical bytes."""
    bindings = {"primary-model": CredentialBinding.model_validate(V1_MODEL_SLOT)}
    payload = {
        "bindingSetId": "opencode-go-primary",
        "version": 3,
        "bindings": {
            "primary-model": bindings["primary-model"].model_dump(
                by_alias=True, mode="json"
            )
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    expected = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    assert compute_binding_set_digest("opencode-go-primary", 3, bindings) == expected
    # Fixed golden fixture: this exact digest must never change.
    assert expected == (
        "sha256:87c61754bdd0733864b53d0c33d8921c0d0e42b0c538146a7f26abd7f66215ed"
    )
    golden = cb.decode_historical_v1_binding_set(
        {
            "schemaVersion": "moonmind.omnigent-credential-bindings.v1",
            "bindingSetId": "opencode-go-primary",
            "version": 3,
            "digest": expected,
            "bindings": {
                "primary-model": dict(V1_MODEL_SLOT),
            },
        }
    )
    assert golden.digest == expected


def test_v1_writer_rejects_repository_authority():
    """REQ-02: a new writer must not emit repository authority under v1."""
    with pytest.raises(HarnessPlatformError):
        create_binding_set(
            bindingSetId="mixed",
            version=1,
            bindings={"src": _v2_repo_slot()},
            schema_version="moonmind.omnigent-credential-bindings.v1",
        )


def test_v2_digest_is_domain_separated_from_v1():
    """REQ-02/ACC-03: v2 digest input carries schema domain + discriminators."""
    model_v2 = {
        "authorityKind": "provider_profile",
        "providerProfileRef": "opencode-go-default",
        "materializerRef": "opencode-auth-json@1",
    }
    v1 = create_binding_set(
        bindingSetId="bs",
        version=1,
        bindings={"primary-model": dict(V1_MODEL_SLOT)},
    )
    v2 = create_binding_set(
        bindingSetId="bs",
        version=1,
        bindings={
            "primary-model": dict(model_v2),
            "src": _v2_repo_slot(),
        },
        schema_version="moonmind.omnigent-credential-bindings.v2",
    )
    assert v1.digest != v2.digest
    assert v2.digest.startswith("sha256:")
    # Old v1-only readers reject the union payload instead of
    # misinterpreting repository authority as model authority.
    with pytest.raises(Exception):
        CredentialBinding.model_validate(_v2_repo_slot())


def test_unknown_kind_and_unsupported_version_rejected_before_acquisition():
    """REQ-01/ACC-02: closed union; explicit supported-version dispatch."""
    with pytest.raises(HarnessPlatformError):
        create_binding_set(
            bindingSetId="bs",
            version=1,
            bindings={
                "weird": {
                    "authorityKind": "superuser",
                    "providerProfileRef": "x",
                    "materializerRef": "none@1",
                }
            },
            schema_version="moonmind.omnigent-credential-bindings.v2",
        )
    with pytest.raises(HarnessPlatformError):
        create_binding_set(
            bindingSetId="bs",
            version=1,
            bindings={"primary-model": dict(V1_MODEL_SLOT)},
            schema_version="moonmind.omnigent-credential-bindings.v99",
        )


def test_conflicting_slot_aliases_rejected():
    """REQ-01: mixed conflicting aliases fail before acquisition."""
    with pytest.raises(HarnessPlatformError):
        cb.create_binding_set_raw(
            {
                "schemaVersion": "moonmind.omnigent-credential-bindings.v2",
                "bindingSetId": "bs",
                "version": 1,
                "bindings": {
                    "Src": _v2_repo_slot(),
                    "src": _v2_repo_slot(repositoryRole="destination_write"),
                },
            }
        )


def test_undeclared_slots_still_rejected_and_kind_checked_per_slot():
    """REQ-03/ACC-02: undeclared-slot protection preserved; kind/materializer/role checked."""
    binding_set = create_binding_set(
        bindingSetId="bs",
        version=1,
        bindings={
            "primary-model": {
                "authorityKind": "provider_profile",
                "providerProfileRef": "opencode-go-default",
                "materializerRef": "opencode-auth-json@1",
            },
            "src": _v2_repo_slot(),
        },
        schema_version="moonmind.omnigent-credential-bindings.v2",
    )
    # Undeclared extra slot still fails.
    with pytest.raises(HarnessPlatformError):
        cb.validate_binding_set_for_plan(
            binding_set=binding_set,
            required_slots=["primary-model"],
            declared_slots=["primary-model"],
        )
    # Wrong authority kind at a declared slot fails (no relabeling treasury).
    with pytest.raises(HarnessPlatformError):
        cb.validate_binding_set_for_plan(
            binding_set=binding_set,
            required_slots=["primary-model"],
            declared_slots=["primary-model", "src"],
            declared_repository_slots={},
        )
    # Source read grant cannot be relabeled as publication authority.
    with pytest.raises(HarnessPlatformError):
        cb.validate_binding_set_for_plan(
            binding_set=binding_set,
            required_slots=["primary-model"],
            declared_slots=["primary-model"],
            declared_repository_slots={
                "src": {
                    "allowedRoles": ("destination_write",),
                },
            },
        )
    # Correct declaration passes.
    cb.validate_binding_set_for_plan(
        binding_set=binding_set,
        required_slots=["primary-model"],
        declared_slots=["primary-model"],
        declared_repository_slots={
            "src": {
                "allowedRoles": ("source_read",),
                "allowedMaterializers": ("repository-broker@1",),
            },
        },
    )


def test_only_model_bindings_enter_capacity_accounting():
    """REQ-04/ACC-04: one model profile plus two repository roles is not multi-model."""
    binding_set = create_binding_set(
        bindingSetId="bs",
        version=1,
        bindings={
            "primary-model": {
                "authorityKind": "provider_profile",
                "providerProfileRef": "opencode-go-default",
                "materializerRef": "opencode-auth-json@1",
            },
            "src": _v2_repo_slot(),
            "dst": _v2_repo_slot(
                repositoryRole="destination_write",
                repositoryAccessSnapshotRef=(
                    "repository-access-snapshot:sha256:" + "d" * 64
                ),
            ),
        },
        schema_version="moonmind.omnigent-credential-bindings.v2",
    )
    assert cb.model_profile_refs(binding_set) == ["opencode-go-default"]
    assert sorted(cb.repository_authority_bindings(binding_set).keys()) == [
        "dst",
        "src",
    ]
    # Credentialless model profile survives alongside anonymous repository state.
    assert isinstance(
        binding_set.bindings["primary-model"],
        (CredentialBinding, ModelAuthorityBinding),
    )
    assert isinstance(
        binding_set.bindings["src"], RepositoryAuthorityBinding
    )


def test_scratch_and_anonymous_workspace_source_rules():
    """REQ-06: scratch emits no repository binding; anonymous carries snapshot, no secret slot."""
    model_only = create_binding_set(
        bindingSetId="bs",
        version=1,
        bindings={"primary-model": dict(V1_MODEL_SLOT)},
    )
    cb.validate_workspace_source_bindings("scratch", model_only)
    with_snapshot = create_binding_set(
        bindingSetId="bs",
        version=1,
        bindings={
            "primary-model": {
                "authorityKind": "provider_profile",
                "providerProfileRef": "opencode-zen-free",
                "materializerRef": "none@1",
            },
        },
        schema_version="moonmind.omnigent-credential-bindings.v2",
    )
    cb.validate_workspace_source_bindings(
        "anonymous", with_snapshot, access_snapshot_ref=REPO_SNAPSHOT
    )
    # Anonymous without the explicit permitted snapshot fails.
    with pytest.raises(HarnessPlatformError):
        cb.validate_workspace_source_bindings("anonymous", with_snapshot)
    # Scratch with a repository binding fails.
    repo_set = create_binding_set(
        bindingSetId="bs",
        version=1,
        bindings={
            "primary-model": {
                "authorityKind": "provider_profile",
                "providerProfileRef": "opencode-go-default",
                "materializerRef": "opencode-auth-json@1",
            },
            "src": _v2_repo_slot(),
        },
        schema_version="moonmind.omnigent-credential-bindings.v2",
    )
    with pytest.raises(HarnessPlatformError):
        cb.validate_workspace_source_bindings("scratch", repo_set)
    # Save-only work gets no hypothetical destination credentials.
    save_only_set = create_binding_set(
        bindingSetId="bs",
        version=1,
        bindings={
            "primary-model": {
                "authorityKind": "provider_profile",
                "providerProfileRef": "opencode-go-default",
                "materializerRef": "opencode-auth-json@1",
            },
            "dst": _v2_repo_slot(
                repositoryRole="destination_write",
                repositoryAccessSnapshotRef=(
                    "repository-access-snapshot:sha256:" + "d" * 64
                ),
            ),
        },
        schema_version="moonmind.omnigent-credential-bindings.v2",
    )
    with pytest.raises(HarnessPlatformError):
        cb.validate_workspace_source_bindings("save_only", save_only_set)


def test_child_inheritance_requires_own_attenuated_snapshot():
    """REQ-07/ACC-05: parent visibility alone is not permission; no raw credential copy."""
    parent = RepositoryAuthorityBinding.model_validate(_v2_repo_slot())
    # Parent snapshot ref alone (no child-scoped snapshot) is rejected.
    with pytest.raises(HarnessPlatformError):
        cb.attenuate_repository_binding_for_child(
            parent_binding=parent,
            child_target_ref="workflow:child-1",
            child_attempt_ref="attempt:child-1",
            child_snapshot_ref=None,
        )
    # Copying the parent snapshot as the child snapshot is not attenuation.
    with pytest.raises(HarnessPlatformError):
        cb.attenuate_repository_binding_for_child(
            parent_binding=parent,
            child_target_ref="workflow:child-1",
            child_attempt_ref="attempt:child-1",
            child_snapshot_ref=REPO_SNAPSHOT,
        )
    child_snapshot = "repository-access-snapshot:sha256:" + "e" * 64
    grant = cb.attenuate_repository_binding_for_child(
        parent_binding=parent,
        child_target_ref="workflow:child-1",
        child_attempt_ref="attempt:child-1",
        child_snapshot_ref=child_snapshot,
    )
    assert grant.child_target_ref == "workflow:child-1"
    assert grant.binding.repositoryAccessSnapshotRef == child_snapshot
    # Raw credential material smuggled through refs is rejected.
    with pytest.raises(HarnessPlatformError):
        cb.attenuate_repository_binding_for_child(
            parent_binding=parent,
            child_target_ref="workflow:child-1",
            child_attempt_ref="attempt:child-1",
            child_snapshot_ref="repository-access-snapshot:ghp_secrettoken123",
        )


def test_old_worker_rejects_new_authority_without_fallback():
    """REQ-08: incompatible old workers reject new authority, no global fallback."""
    repo_set = create_binding_set(
        bindingSetId="bs",
        version=1,
        bindings={
            "primary-model": {
                "authorityKind": "provider_profile",
                "providerProfileRef": "opencode-go-default",
                "materializerRef": "opencode-auth-json@1",
            },
            "src": _v2_repo_slot(),
        },
        schema_version="moonmind.omnigent-credential-bindings.v2",
    )
    with pytest.raises(HarnessPlatformError):
        cb.assert_worker_supports_binding_set(("model-only",), repo_set)
    cb.assert_worker_supports_binding_set(("model", "repository"), repo_set)


def test_secret_canary_absent_from_durable_binding_bytes():
    """ACC-07: raw values never enter durable binding objects."""
    binding_set = create_binding_set(
        bindingSetId="bs",
        version=1,
        bindings={
            "primary-model": {
                "authorityKind": "provider_profile",
                "providerProfileRef": "opencode-go-default",
                "materializerRef": "opencode-auth-json@1",
            },
            "src": _v2_repo_slot(),
        },
        schema_version="moonmind.omnigent-credential-bindings.v2",
    )
    raw = binding_set.model_dump(by_alias=True, mode="json")
    blob = json.dumps(raw)
    for canary in ("ghp_", "sk-", "-----BEGIN", "password", "token="):
        assert canary not in blob


def test_credential_binding_set_model_still_importable():
    assert CredentialBindingSet is not None


# --------------------------------------------------------------------------
# Traversal: planner, plan envelope, runtime binding (ACC-01, ACC-03, ACC-06)
# --------------------------------------------------------------------------

def test_planner_splits_consumption_by_authority_type():
    from datetime import UTC, datetime

    from moonmind.omnigent.harness_platform.catalog import (
        HarnessImplementationIdentity,
        TrustState,
        classify_harness_trust,
        create_catalog_snapshot,
    )
    from moonmind.omnigent.harness_platform.host_classes import (
        HOST_CLASSES,
        register_host_class,
    )
    from moonmind.omnigent.harness_platform.planner import compile_execution_plan
    from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet

    original = dict(HOST_CLASSES)
    HOST_CLASSES.clear()
    try:
        impl = HarnessImplementationIdentity.model_validate(
            {
                "sourceKind": "core",
                "package": "omnigent",
                "version": "1.0.0",
                "digest": "sha256:" + "a" * 64,
                "pluginEntryPoint": None,
            }
        )
        register_host_class(
            {
                "omnigentVersion": "1.0.0",
                "omnigentBuildDigest": "sha256:" + "b" * 64,
                "architectures": ["linux/amd64"],
                "integrationModes": ["native-server"],
                "features": {
                    "workspaceBind": True,
                    "readOnlyRoot": True,
                    "restrictedEgress": True,
                },
                "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
                "hostClassId": "repo-test-standard",
                "version": 1,
                "imageRef": "ghcr.io/example/host@sha256:" + "a" * 64,
                "declaredHarnessImplementations": [
                    {
                        "harnessId": "opencode-native",
                        "implementationRef": impl.implementation_ref(),
                        "runtimeDependencies": [],
                    }
                ],
                "materializerRefs": ["opencode-auth-json@1", "none@1"],
            }
        )
        catalog = create_catalog_snapshot(
            endpointRef="default",
            omnigentVersion="1.0.0",
            omnigentBuildDigest="sha256:" + "b" * 64,
            sourceDigest="sha256:" + "c" * 64,
            harnesses=[
                {
                    "id": "opencode-native",
                    "aliases": [],
                    "label": "OpenCode",
                    "implementation": {
                        "sourceKind": "core",
                        "package": "omnigent",
                        "version": "1.0.0",
                        "digest": "sha256:" + "a" * 64,
                        "pluginEntryPoint": None,
                    },
                    "runtimeRequirements": {},
                    "capabilities": {"integrationMode": "native-server"},
                    "setupSteps": [],
                }
            ],
            observedAt=datetime.now(UTC),
        )
        profile = {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": "default",
            "source": {
                "kind": "upstream",
                "upstreamId": "opencode-native-ui",
                "upstreamVersion": "1.0.0",
                "upstreamSnapshotDigest": "sha256:" + "d" * 64,
            },
            "harness": {
                "id": "opencode-native",
                "catalogRef": catalog.catalogRef,
                "implementationRef": impl.implementation_ref(),
            },
            "requirements": {
                "harness": {"required": [], "preferred": []},
                "moonmind": {"required": []},
                "host": {"required": []},
            },
            "credentialSlots": [
                {
                    "id": "primary-model",
                    "optional": False,
                    "acceptedAuthModels": ["own-auth"],
                    "acceptedProviderIds": ["opencode"],
                }
            ],
            "model": {},
            "workspace": {"mutation": "read_only"},
            "skills": [],
            "tools": [],
            "capture": {"stream": False, "evidence": False},
            "continuations": {},
            "publish": {},
            "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
        }
        skills = ResolvedSkillSet.model_validate(
            {
                "resolvedSkillSetRef": "artifact:test",
                "resolvedSkillSetDigest": "sha256:" + "a" * 64,
                "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
            }
        )
        trust = classify_harness_trust(
            harnessId="opencode-native",
            implementation=impl,
            trustState=TrustState.core_trusted,
        )
        binding_set = create_binding_set(
            bindingSetId="mixed",
            version=1,
            bindings={
                "primary-model": {
                    "authorityKind": "provider_profile",
                    "providerProfileRef": "opencode-go-default",
                    "materializerRef": "opencode-auth-json@1",
                },
                "src": _v2_repo_slot(),
                "dst": _v2_repo_slot(
                    repositoryRole="destination_write",
                    repositoryAccessSnapshotRef=(
                        "repository-access-snapshot:sha256:" + "d" * 64
                    ),
                ),
            },
            schema_version="moonmind.omnigent-credential-bindings.v2",
        )

        def _compile(**overrides):
            args = {
                "agent_profile": profile,
                "harness_catalog": catalog,
                "trust_record": trust,
                "resolved_skills": skills,
                "credential_binding_set": binding_set,
                "host_class_ref": "repo-test-standard@1",
                "launch_policy_ref": "omnigent-on-demand@1",
                "model_qualified_id": "opencode/test-model",
                "model_effort": None,
                "model_route_ref": "opencode-go",
                "model_normalized_options": {},
            }
            args.update(overrides)
            return compile_execution_plan(**args)

        # Repository bindings without admitted declarations fail closed.
        with pytest.raises(HarnessPlatformError):
            _compile()
        # Correct declarations traverse real planner wiring.
        envelope = _compile(
            repository_slot_requirements={
                "src": {
                    "allowedRoles": ("source_read",),
                    "allowedMaterializers": ("repository-broker@1",),
                },
                "dst": {
                    "allowedRoles": ("destination_write",),
                    "allowedMaterializers": ("repository-broker@1",),
                },
            }
        )
        payload = envelope.payload
        # One model profile plus two repository roles is not multi-model:
        # the model support/combination identity sees one profile only.
        assert cb.model_profile_refs(binding_set) == ["opencode-go-default"]
        assert payload.repositoryAuthorityRefs == {
            "src": REPO_SNAPSHOT,
            "dst": "repository-access-snapshot:sha256:" + "d" * 64,
        }
        assert "repository-broker@1" not in json.dumps(
            payload.supportIdentity.model_dump(by_alias=True, mode="json")
        )
        blob = json.dumps(payload.model_dump(by_alias=True, mode="json"))
        assert "credentialGeneration" not in blob
        assert "providerLeaseRef" not in blob
    finally:
        HOST_CLASSES.clear()
        HOST_CLASSES.update(original)


def test_plan_envelope_rejects_mismatched_repository_refs():
    from moonmind.omnigent.harness_platform.execution_plan import (
        compute_model_config_digest,
        create_execution_plan_envelope,
    )

    def _plan_dict(**overrides):
        digest = compute_model_config_digest(
            qualifiedId="opencode/model",
            effort=None,
            routeRef="opencode-go",
            normalizedOptions={},
        )
        plan = {
            "endpointRef": "default",
            "agentProfileSnapshotRef": "omnigent-agent-profile:sha256:" + "1" * 64,
            "harnessCatalogRef": "omnigent-harness-catalog:sha256:" + "2" * 64,
            "harnessId": "opencode-native",
            "harnessImplementationRef": "omnigent-harness-implementation:sha256:"
            + "3" * 64,
            "agentSource": {
                "kind": "upstream",
                "upstreamId": "opencode-native-ui",
                "upstreamVersion": "1",
                "upstreamSnapshotDigest": "sha256:" + "4" * 64,
            },
            "credentialBindingSetRef": (
                "omnigent-credential-bindings:primary@1#sha256:" + "5" * 64
            ),
            "credentialBindings": {
                "primary-model": {
                    "providerProfileRef": "opencode-go-primary",
                    "materializerRef": "opencode-auth-json@1",
                }
            },
            "hostClassRef": "omnigent-opencode@1",
            "launchPolicyRef": "omnigent-on-demand@1",
            "executionRealizerRef": "generic-omnigent-host@1",
            "model": {
                "qualifiedId": "opencode/model",
                "effort": None,
                "routeRef": "opencode-go",
                "normalizedOptions": {},
                "modelConfigDigest": digest,
            },
            "resolvedSkills": {
                "resolvedSkillSetRef": "artifact:skills",
                "resolvedSkillSetDigest": "sha256:" + "6" * 64,
                "skillDeliveryRef": "skill-delivery:sha256:" + "7" * 64,
            },
            "classAdmissionDecision": {
                "allowed": True,
                "requiredSatisfied": [],
                "preferredSatisfied": [],
                "preferredMissing": [],
                "reasons": [],
            },
            "runtimeValidationRequirements": ["live-model-option"],
            "workspaceIntentRef": "workspace-intent:sha256:" + "8" * 64,
            "workspaceMutation": "read_only",
            "capturePolicy": {"stream": False, "evidence": False},
            "policySnapshotRef": "omnigent-policy:sha256:" + "9" * 64,
            "supportCombinationKey": (
                "omnigent-support-combination:sha256:" + "0" * 64
            ),
        }
        plan.update(overrides)
        return plan

    # Model-only plans keep their historical shape (no repository refs field).
    envelope = create_execution_plan_envelope(_plan_dict())
    assert envelope.payload.repositoryAuthorityRefs is None

    bindings = {
        "primary-model": {
            "authorityKind": "provider_profile",
            "providerProfileRef": "opencode-go-primary",
            "materializerRef": "opencode-auth-json@1",
        },
        "src": _v2_repo_slot(),
    }
    envelope = create_execution_plan_envelope(
        _plan_dict(
            credentialBindings=bindings,
            repositoryAuthorityRefs={"src": REPO_SNAPSHOT},
        )
    )
    assert envelope.payload.repositoryAuthorityRefs == {"src": REPO_SNAPSHOT}
    # A ref for a non-repository slot is rejected before any side effect.
    with pytest.raises(Exception):
        create_execution_plan_envelope(
            _plan_dict(
                credentialBindings=bindings,
                repositoryAuthorityRefs={"primary-model": REPO_SNAPSHOT},
            )
        )
    # A ref conflicting with the admitted binding snapshot is rejected.
    with pytest.raises(Exception):
        create_execution_plan_envelope(
            _plan_dict(
                credentialBindings=bindings,
                repositoryAuthorityRefs={
                    "src": "repository-access-snapshot:sha256:" + "f" * 64
                },
            )
        )


def test_runtime_issuance_release_is_ownership_scoped():
    from moonmind.omnigent.harness_platform.runtime_binding import (
        create_runtime_binding,
        release_unused_repository_issuance,
    )

    plan_ref = "omnigent-execution-plan:sha256:" + "a" * 64
    leases = {
        "primary-model": {
            "providerProfileRef": "opencode-go-default",
            "providerLeaseRef": "lease-1",
            "credentialGeneration": 7,
            "credentialRuntimeRef": "credential-runtime:lease-1:7",
        }
    }
    issuance = {
        "src": {
            "connectionRef": "repo-conn-main",
            "issuanceRef": "issuance-1",
            "snapshotRef": REPO_SNAPSHOT,
            "credentialRevision": "rev-1",
            "useOwner": "attempt:1",
        },
        "dst": {
            "connectionRef": "repo-conn-main",
            "issuanceRef": "issuance-2",
            "snapshotRef": "repository-access-snapshot:sha256:" + "d" * 64,
            "credentialRevision": "rev-1",
            "useOwner": "attempt:1",
        },
    }
    binding = create_runtime_binding(
        executionPlanRef=plan_ref,
        providerLeases=leases,
        repositoryIssuance=issuance,
    )
    model_only = create_runtime_binding(
        executionPlanRef=plan_ref, providerLeases=leases
    )
    # Model-only history keeps its exact ref: an empty issuance mapping
    # digests exactly like a binding written before the field existed.
    from moonmind.omnigent.harness_platform.runtime_binding import (
        compute_runtime_binding_ref,
    )

    legacy_raw = {
        k: v
        for k, v in model_only.model_dump(by_alias=True, mode="json").items()
        if k not in ("runtimeBindingRef", "repositoryIssuance")
    }
    assert compute_runtime_binding_ref(legacy_raw) == model_only.runtimeBindingRef
    narrowed, released = release_unused_repository_issuance(binding, ["src"])
    assert released == ["issuance-1"]
    # The failed acquisition releases only its own issuance: the model
    # lease and the sibling issuance are untouched.
    assert narrowed.providerLeases["primary-model"].credentialGeneration == 7
    assert list(narrowed.repositoryIssuance.keys()) == ["dst"]


def test_v2_authority_rejected_by_retained_v1_plan_reader_without_fallback():
    """REQ-09/ACC-03: a v1-only plan reader rejects v2 authority; no fallback.

    Exercises the actual retained reader fixture (the historical plan
    payload contract), not a re-implementation of its Pydantic shape: v1
    model-only bindings are still admitted while any repository-authority
    entry fails closed instead of being reinterpreted as model authority.
    """
    import importlib.util
    import sys
    from pathlib import Path

    from pydantic import TypeAdapter, ValidationError

    fixture = (
        Path(__file__).parents[2]
        / "integration"
        / "reliability"
        / "replays"
        / "omnigent-plan-reader-skew"
        / "retained_execution_plan.py"
    )
    assert fixture.is_file()
    spec = importlib.util.spec_from_file_location(
        "retained_plan_reader_v2_skew", fixture
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        annotation = module.OmnigentExecutionPlanPayload.model_fields[
            "credentialBindings"
        ].annotation
        adapter = TypeAdapter(annotation)
        # Historical v1 model-only bindings remain readable.
        adapter.validate_python({"primary-model": dict(V1_MODEL_SLOT)})
        # v2 repository authority is rejected, never reinterpreted as
        # model authority and never silently dropped.
        with pytest.raises(ValidationError):
            adapter.validate_python(
                {"primary-model": dict(V1_MODEL_SLOT), "src": _v2_repo_slot()}
            )
        # The retained payload contract has no repositoryAuthorityRefs
        # field, so a v2 payload carrying it fails closed via
        # extra="forbid" instead of losing the new authority silently.
        assert (
            "repositoryAuthorityRefs"
            not in module.OmnigentExecutionPlanPayload.model_fields
        )
    finally:
        sys.modules.pop(spec.name, None)
