"""Serialized native authority must fit the installed process transport."""

import base64
import json
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from moonmind.security.egress_policy import ScopedEgressPolicy
from moonmind.security.native_network import NativeNetworkSelection
from moonmind.security.scoped_egress import ScopedEgressLease
from tests.unit.security.test_native_network import native


def expanded(hosts, ports):
    data = native().model_dump(by_alias=True)
    data["targets"][0].update(hosts=hosts, ports=ports)
    return data


def test_long_host_endpoint_product_is_denied_before_lease_admission():
    # These remain within the declared host/port/endpoint counts but the
    # repeated authorities previously produced one >128 KiB argv entry.
    data = expanded(
        [f"n{i:02d}" + "a" * 48 + ".example.test" for i in range(16)],
        list(range(10000, 10064)),
    )
    with pytest.raises(ValueError, match="transport"):
        NativeNetworkSelection.model_validate(data)


def test_large_exclusion_declaration_is_denied_before_snapshot_transport():
    data = expanded(["one.example.test"], [587])
    exclusions = [
        f"n{i:03d}." + ".".join(["a" * 60, "b" * 60, "c" * 60]) + ".test"
        for i in range(256)
    ]
    data["targets"] = [
        {**data["targets"][0], "hosts": [host], "exclusions": exclusions}
        for host in ("one.example.test", "two.example.test")
    ]
    with pytest.raises(ValueError, match="transport"):
        NativeNetworkSelection.model_validate(data)


def test_ordinary_1024_endpoints_still_launch_through_real_process_argv():
    selection = NativeNetworkSelection.model_validate(
        expanded([f"h{i}.example.com" for i in range(16)], list(range(100, 164)))
    )
    policy = ScopedEgressPolicy(
        policyRef="native",
        allowedWorkflowIds=["one"],
        allowedImageSourceRefs=["client"],
        nativeNetwork=selection,
    )
    lease = ScopedEgressLease(
        AsyncMock(),
        policy,
        ownership="owned",
        job_id="job",
        workload_ref="client",
        native_selection=selection,
    )
    body = lease._native_config(
        {
            "nativeSelection": selection.model_dump(mode="json", by_alias=True),
            "addresses": {
                host: ["93.184.216.34"] for host, _ in selection.targets[0].endpoints()
            },
            "expiresAt": 2000000000.0,
            "proxyAddress": "10.8.0.2",
            "clientAddress": "10.8.0.3",
        }
    )
    assert len(json.loads(body)["endpoints"]) == 1024
    argument = "MOONMIND_EGRESS_CONFIG=" + base64.b64encode(body).decode()
    result = subprocess.run(
        [sys.executable, "-c", "import sys; print(len(sys.argv[1]))", argument],
        check=True,
        capture_output=True,
        text=True,
    )
    assert int(result.stdout) == len(argument)
