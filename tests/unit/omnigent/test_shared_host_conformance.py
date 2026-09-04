"""Exact-artifact conformance for the shared host image (#3832).

Covers the versioned required-row catalog, shared-image inventory,
one-harness admission, credential isolation, ownership-aware cleanup,
runtime/auth readiness, lifecycle ordering, and failure isolation. All
checks are deterministic and credentialless; protected-live qualification of
the exact digest stays with the provider-verification runner.
"""

import pytest

from moonmind.omnigent.harness_platform import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.shared_host_conformance import (
    GENERIC_REALIZER_REF,
    REQUIRED_ROWS,
    SHARED_HOST_CONFORMANCE_CATALOG_VERSION,
    assert_credential_isolation,
    assert_failure_isolation,
    assert_lifecycle_order,
    assert_one_harness_admission,
    assert_ownership_cleanup,
    assert_runtime_readiness,
    build_conformance_evidence,
    build_shared_host_image_inventory,
    get_required_row,
    required_row_for_harness,
)

_IMAGE = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "e" * 64
_SERVER = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "b" * 64
_BUILD = "sha256:" + "c" * 64
_KEY = "omnigent-support:sha256:" + "d" * 64


def _admission_kwargs(row_id: str, **overrides):
    row = get_required_row(row_id)
    params = {
        "row": row,
        "plan_harness_id": row.harnessId,
        "plan_realizer_ref": GENERIC_REALIZER_REF,
        "host_class_ref": row.hostClassRef,
        "declared_harness_ids": (row.harnessId,),
        "runtime_pack_ref": row.runtimePackRef,
        "materializer_refs": (row.materializerRef,),
        "support_combination_key": _KEY,
        "expected_support_combination_key": _KEY,
    }
    params.update(overrides)
    return params


def test_required_row_catalog_covers_supported_trio_on_generic_realizer():
    assert SHARED_HOST_CONFORMANCE_CATALOG_VERSION.startswith("moonmind.")
    by_harness = {row.harnessId: row for row in REQUIRED_ROWS}
    assert set(by_harness) == {"opencode-native", "codex-native", "claude-native"}
    assert by_harness["opencode-native"].materializerRef == "opencode-auth-json@1"
    assert by_harness["opencode-native"].ownershipClass == "run_owned"
    assert by_harness["codex-native"].materializerRef == "codex-oauth-home@1"
    assert by_harness["codex-native"].ownershipClass == "profile_owned"
    assert by_harness["claude-native"].materializerRef == "claude-oauth-home@1"
    assert by_harness["claude-native"].ownershipClass == "profile_owned"
    for row in REQUIRED_ROWS:
        assert row.executionRealizerRef == GENERIC_REALIZER_REF
        assert row.liveEvidence == "pending"
    assert required_row_for_harness("codex-native").rowId == "codex-shared-generic-v1"
    with pytest.raises(HarnessPlatformError):
        required_row_for_harness("not-a-harness")


def test_legacy_realizer_cannot_enter_required_catalog():
    from moonmind.omnigent.harness_platform.shared_host_conformance import RequiredRow

    with pytest.raises(ValueError):
        RequiredRow.model_validate(
            {
                "rowId": "legacy-comparison-only",
                "harnessId": "codex-native",
                "materializerRef": "codex-oauth-home@1",
                "ownershipClass": "profile_owned",
                "executionRealizerRef": "codex-profile-bound@1",
                "hostClassRef": "omnigent-codex@1",
                "runtimePackRef": "codex-native-pack@1",
                "providerCompatibilityClass": "omnigent-provider-binding-set@1",
                "agentProfileVersion": "moonmind.omnigent-agent-profile.v2",
                "hostMode": "on-demand",
                "launchPolicyRef": "codex-on-demand@1",
                "architectures": ["linux/amd64"],
            }
        )


def test_shared_image_inventory_proves_contents_only():
    inventory = build_shared_host_image_inventory(
        image_ref=_IMAGE,
        architecture="linux/amd64",
        omnigent_build_digest=_BUILD,
        sbom_ref="sbom:example-ref",
        provenance_ref="provenance:example-ref",
    )
    assert [item["name"] for item in inventory.runtimeBinaries] == [
        "claude",
        "codex",
        "opencode",
    ]
    assert inventory.uid == 1000 and inventory.gid == 1000
    assert inventory.home == "/home/app"
    assert set(inventory.harnessImplementations) == {
        "codex-native",
        "claude-native",
        "opencode-native",
    }
    with pytest.raises(HarnessPlatformError):
        build_conformance_evidence(
            moonmind_commit="abc",
            server_image_ref=_SERVER,
            shared_host_image_ref="ghcr.io/example/host:latest",
            architecture="linux/amd64",
            row_results=[],
        )


def test_one_harness_admission_and_substitution_rejected_before_provider_work():
    assert_one_harness_admission(**_admission_kwargs("codex-shared-generic-v1"))
    # Another installed harness cannot be substituted via materializers.
    with pytest.raises(HarnessPlatformError) as exc:
        assert_one_harness_admission(
            **_admission_kwargs(
                "codex-shared-generic-v1",
                materializer_refs=("codex-oauth-home@1", "claude-oauth-home@1"),
            )
        )
    assert (
        exc.value.code
        == HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE
    )
    # Wrong pack, wrong class, wrong support key all fail.
    with pytest.raises(HarnessPlatformError):
        assert_one_harness_admission(
            **_admission_kwargs(
                "codex-shared-generic-v1",
                runtime_pack_ref="claude-native-pack@1",
            )
        )
    with pytest.raises(HarnessPlatformError):
        assert_one_harness_admission(
            **_admission_kwargs(
                "codex-shared-generic-v1",
                host_class_ref="omnigent-claude@1",
            )
        )
    with pytest.raises(HarnessPlatformError):
        assert_one_harness_admission(
            **_admission_kwargs(
                "codex-shared-generic-v1",
                support_combination_key="omnigent-support:sha256:" + "0" * 64,
            )
        )


def test_credential_isolation_per_row_names_only():
    codex = get_required_row("codex-shared-generic-v1")
    assert_credential_isolation(
        row=codex,
        present_paths=("/home/app/.codex",),
        present_env_keys=(),
    )
    with pytest.raises(HarnessPlatformError):
        assert_credential_isolation(
            row=codex,
            present_paths=("/home/app/.codex", "/home/app/.claude"),
            present_env_keys=(),
        )
    with pytest.raises(HarnessPlatformError):
        assert_credential_isolation(
            row=codex,
            present_paths=("/home/app/.codex",),
            present_env_keys=("ANTHROPIC_API_KEY",),
        )
    with pytest.raises(HarnessPlatformError):
        assert_credential_isolation(
            row=codex,
            present_paths=(
                "/home/app/.codex",
                "/home/app/.local/share/opencode/auth.json",
            ),
            present_env_keys=(),
        )
    claude = get_required_row("claude-shared-generic-v1")
    assert_credential_isolation(
        row=claude, present_paths=("/home/app/.claude",), present_env_keys=()
    )
    with pytest.raises(HarnessPlatformError):
        assert_credential_isolation(
            row=claude,
            present_paths=("/home/app/.claude", "/home/app/.codex"),
            present_env_keys=(),
        )
    with pytest.raises(HarnessPlatformError):
        assert_credential_isolation(
            row=claude,
            present_paths=("/home/app/.claude",),
            present_env_keys=("OPENAI_API_KEY",),
        )
    opencode = get_required_row("opencode-shared-generic-v1")
    assert_credential_isolation(
        row=opencode,
        present_paths=("/home/app/.local/share/opencode/auth.json",),
        present_env_keys=(),
    )
    with pytest.raises(HarnessPlatformError):
        assert_credential_isolation(
            row=opencode,
            present_paths=(
                "/home/app/.local/share/opencode/auth.json",
                "/home/app/.codex",
            ),
            present_env_keys=(),
        )
    with pytest.raises(HarnessPlatformError):
        assert_credential_isolation(
            row=opencode,
            present_paths=("/home/app/.local/share/opencode/auth.json",),
            present_env_keys=("OPENAI_API_KEY",),
        )


def test_ownership_cleanup_rules():
    assert_ownership_cleanup(
        ownership="run_owned",
        run_state_present_after_cleanup=False,
        enrollment_state_present_after_cleanup=False,
        host_state_copied_or_deleted=False,
        stale_cleanup_affected_replacement=False,
        provider_profile_released_last=True,
        image_layers_present_after_cleanup=True,
    )
    assert_ownership_cleanup(
        ownership="profile_owned",
        run_state_present_after_cleanup=False,
        enrollment_state_present_after_cleanup=True,
        host_state_copied_or_deleted=False,
        stale_cleanup_affected_replacement=False,
        provider_profile_released_last=True,
        image_layers_present_after_cleanup=True,
    )
    with pytest.raises(HarnessPlatformError):
        assert_ownership_cleanup(
            ownership="run_owned",
            run_state_present_after_cleanup=True,
            enrollment_state_present_after_cleanup=False,
            host_state_copied_or_deleted=False,
            stale_cleanup_affected_replacement=False,
            provider_profile_released_last=True,
            image_layers_present_after_cleanup=True,
        )
    with pytest.raises(HarnessPlatformError):
        assert_ownership_cleanup(
            ownership="profile_owned",
            run_state_present_after_cleanup=False,
            enrollment_state_present_after_cleanup=False,
            host_state_copied_or_deleted=False,
            stale_cleanup_affected_replacement=False,
            provider_profile_released_last=True,
            image_layers_present_after_cleanup=True,
        )
    with pytest.raises(HarnessPlatformError):
        assert_ownership_cleanup(
            ownership="host_owned",
            run_state_present_after_cleanup=False,
            enrollment_state_present_after_cleanup=False,
            host_state_copied_or_deleted=True,
            stale_cleanup_affected_replacement=False,
            provider_profile_released_last=True,
            image_layers_present_after_cleanup=True,
        )
    with pytest.raises(HarnessPlatformError):
        assert_ownership_cleanup(
            ownership="run_owned",
            run_state_present_after_cleanup=False,
            enrollment_state_present_after_cleanup=False,
            host_state_copied_or_deleted=False,
            stale_cleanup_affected_replacement=False,
            provider_profile_released_last=False,
            image_layers_present_after_cleanup=True,
        )


def test_runtime_readiness_uses_pack_selected_probes():
    row = get_required_row("codex-shared-generic-v1")
    assert_runtime_readiness(
        row=row,
        observed_vendor_version="0.104.0",
        probe_kinds_passed=("vendor-version",),
        required_env_present=(),
        required_env_expected=(),
        unselected_credential_state_absent=True,
        credential_path_mode_ok=True,
    )
    with pytest.raises(HarnessPlatformError) as exc:
        assert_runtime_readiness(
            row=row,
            observed_vendor_version="0.200.0",
            probe_kinds_passed=("vendor-version",),
            required_env_present=(),
            required_env_expected=(),
            unselected_credential_state_absent=True,
            credential_path_mode_ok=True,
        )
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH
    with pytest.raises(HarnessPlatformError):
        assert_runtime_readiness(
            row=row,
            observed_vendor_version="0.104.0",
            probe_kinds_passed=(),
            required_env_present=(),
            required_env_expected=(),
            unselected_credential_state_absent=True,
            credential_path_mode_ok=True,
        )
    with pytest.raises(HarnessPlatformError):
        assert_runtime_readiness(
            row=row,
            observed_vendor_version="0.104.0",
            probe_kinds_passed=("vendor-version",),
            required_env_present=(),
            required_env_expected=(),
            unselected_credential_state_absent=False,
            credential_path_mode_ok=True,
        )


def test_lifecycle_order_and_failure_isolation():
    assert_lifecycle_order(
        (
            "plan_persisted",
            "provider_lease_acquired",
            "host_realized",
            "session_started",
            "first_turn",
            "terminal_harvest",
            "cleanup",
            "provider_released",
        )
    )
    with pytest.raises(HarnessPlatformError):
        assert_lifecycle_order(("provider_lease_acquired", "plan_persisted"))
    assert_failure_isolation(
        failed_harness_id="codex-native",
        other_harness_id="claude-native",
        other_state_before="idle",
        other_state_after="idle",
    )
    with pytest.raises(HarnessPlatformError):
        assert_failure_isolation(
            failed_harness_id="codex-native",
            other_harness_id="claude-native",
            other_state_before="idle",
            other_state_after="degraded",
        )


def test_conformance_evidence_covers_exactly_required_rows():
    rows = [
        {
            "rowId": row.rowId,
            "status": "exact-artifact-passed",
            "liveEvidence": "pending",
        }
        for row in REQUIRED_ROWS
    ]
    evidence = build_conformance_evidence(
        moonmind_commit="3304dba5c",
        server_image_ref=_SERVER,
        shared_host_image_ref=_IMAGE,
        architecture="linux/amd64",
        row_results=rows,
    )
    assert evidence["evidenceDigest"].startswith("sha256:")
    with pytest.raises(HarnessPlatformError):
        build_conformance_evidence(
            moonmind_commit="3304dba5c",
            server_image_ref=_SERVER,
            shared_host_image_ref=_IMAGE,
            architecture="linux/amd64",
            row_results=rows[:2],
        )
