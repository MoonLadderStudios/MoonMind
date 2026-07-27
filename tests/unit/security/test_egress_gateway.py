from pathlib import Path

import pytest

from moonmind.security.egress_gateway import (
    DockerEgressGatewayReconciler,
    compile_squid_policy,
)
from moonmind.omnigent.execution_profiles import EGRESS_PROFILES


def _profile():
    return EGRESS_PROFILES["omnigent-provider@1"]


def test_compiled_policy_denies_bypasses_and_is_digest_stable() -> None:
    policy, digest = compile_squid_policy(_profile())
    assert "http_access deny forbidden_v4" in policy
    assert "http_access deny forbidden_v6" in policy
    assert "acl forbidden_v6 dst ipv6" in policy
    assert "positive_dns_ttl 1 seconds" in policy
    assert "acl mm_destination_0_target dstdomain api.openai.com" in policy
    assert "acl mm_destination_0_ports port 443" in policy
    assert policy.rstrip().endswith("cache deny all")
    assert digest == compile_squid_policy(_profile())[1]


@pytest.mark.asyncio
async def test_reconciler_creates_internal_network_and_dual_homed_gateway(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        command = tuple(args)
        commands.append(command)
        if command[:2] in {("container", "inspect"), ("network", "inspect")}:
            if "{{.State.Running}}" in command:
                return 0, b"true\n", b""
            return 1, b"", b"not found"
        return 0, b"", b""

    result = await DockerEgressGatewayReconciler(
        runner=runner,
        state_root=tmp_path,
        backend_ref="system",
        attestation_key=b"k" * 32,
    ).reconcile(profile=_profile(), network_ref="egress-1")

    network_create = next(c for c in commands if c[:2] == ("network", "create"))
    gateway_create = next(c for c in commands if c[:2] == ("container", "create"))
    assert "--internal" in network_create
    assert result.gateway_ref == "egress-1-gateway"
    assert ("network", "connect", "egress-1", "egress-1-gateway") in commands
    assert "--cap-drop" in gateway_create and "ALL" in gateway_create
    assert "NET_ADMIN" not in gateway_create and "NET_RAW" not in gateway_create
    assert "--read-only" in gateway_create


@pytest.mark.asyncio
async def test_reconciler_refuses_to_replace_unowned_state(tmp_path: Path) -> None:
    async def runner(args):
        if tuple(args[:2]) == ("container", "inspect"):
            return 0, b"false\n", b""
        return 1, b"", b"not found"

    reconciler = DockerEgressGatewayReconciler(
        runner=runner,
        state_root=tmp_path,
        backend_ref="system",
        attestation_key=b"k" * 32,
    )
    with pytest.raises(RuntimeError, match="unowned"):
        await reconciler.reconcile(profile=_profile(), network_ref="egress-1")


@pytest.mark.asyncio
async def test_reconciler_adopts_only_an_internal_compose_network(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        command = tuple(args)
        commands.append(command)
        if command[:2] == ("container", "inspect"):
            if "{{.State.Running}}" in command:
                return 0, b"true\n", b""
            return 1, b"", b"not found"
        if command[:2] == ("network", "inspect"):
            return 0, b"true\n", b""
        return 0, b"", b""

    await DockerEgressGatewayReconciler(
        runner=runner,
        state_root=tmp_path,
        backend_ref="system",
        attestation_key=b"k" * 32,
    ).reconcile(profile=_profile(), network_ref="egress-1")

    assert not any(command[:2] == ("network", "create") for command in commands)
