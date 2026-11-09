"""The typed inventory CLI crosses signed MCP and persisted scope admission."""

from __future__ import annotations

import copy
import json
import os
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.security.service_inventory import parse_service_inventory_plan_arguments
from moonmind.security.workload_profiles import AdmittedAssessmentScope
from tests.integration.security import test_admitted_assessment_journeys as journeys
from tests.integration.security.assessment_admission import (
    seed_legacy_assessment_admission,
)
from tests.integration.security.test_admitted_assessment_journeys import (
    NO_GRANTS,
    Session,
)
from tests.unit.security.test_service_inventory_profile import inventory_profile

environment = journeys.environment

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def admission_bundle(
    *, network=None, target=None, image="ghcr.io/x/inventory:test", scenario="complete"
):
    ports = [2525, 2526, 8080]
    if scenario == "uncertain":
        ports += [8081, 8082, 8083]
    route = (
        {"targetNetworkRef": network, "targetContainerRef": target} if network else {}
    )
    native = {
        "targets": [
            {
                "hosts": ["native.example.test"],
                "ports": ports,
                "purpose": "live-target",
                **route,
            }
        ],
        "limits": {
            "maxConnections": 2 if scenario == "budget" else len(ports),
            "maxBytes": 32768,
            "maxConnectionsPerSecond": 10,
            "maxConcurrentConnections": 1,
            "maxDurationSeconds": 45,
        },
    }
    profile = inventory_profile()
    profile.update(egressPolicyRef="native-fixtures", nativeNetwork=native)
    policy = {
        "policyRef": "native-fixtures",
        "allowedWorkflowIds": ["assessment"],
        "allowedImageSourceRefs": ["repository-security"],
        "nativeNetwork": native,
    }
    settings = resolve_container_backend_settings(
        {
            "MOONMIND_CONTAINER_BACKEND_IMAGE_SOURCES": json.dumps(
                [{"sourceRef": "repository-security", "image": image}]
            ),
            "MOONMIND_CONTAINER_BACKEND_WORKLOAD_PROFILES": json.dumps([profile]),
            "MOONMIND_CONTAINER_BACKEND_EGRESS_POLICIES": json.dumps([policy]),
        }
    )
    selected = {
        "profileRef": "service-inventory",
        "mode": "inspect",
        "assessmentRef": "inventory-admission",
        "nativeNetwork": native,
        "serviceInventory": {
            "bannerPorts": [2525, 8082, 8083] if scenario == "uncertain" else [2525],
            "httpHeadPorts": [8080, 8081] if scenario == "uncertain" else [8080],
            "connectTimeoutSeconds": 1,
            "probeTimeoutSeconds": 1,
            "maxBannerBytes": 1024,
        },
    }
    scope = AdmittedAssessmentScope.model_validate(
        {
            "assessmentRef": "inventory-admission",
            "profileRefs": ["service-inventory"],
            "actionClasses": ["service-inventory"],
            "targets": [{"nativeNetwork": native}],
            "networkRefs": ["native-fixtures"],
            "maxJobs": 2,
            "maxJobSeconds": 120,
            "evidenceIntent": "Bounded fixture service observations",
        }
    )
    return settings, selected, scope


async def test_service_inventory_cli_signed_mcp_persists_fixed_source_free_plan(
    environment, tmp_path, monkeypatch
):
    settings, selected, scope = admission_bundle()
    session = Session(environment, monkeypatch, scope=scope)
    started = await session.submit(
        tmp_path,
        "service-inventory",
        selected,
        temporal=AsyncMock(),
        backend_settings=settings,
        authorizer=NO_GRANTS,
    )
    spec = started.request.spec
    assert spec.input_attachments == []
    assert spec.workspace_ref.workspace_id == "assess"
    assert spec.workspace_ref.relative_path == "repo"
    assert spec.entrypoint == ["python"]
    assert spec.command[:3] == [
        "-I",
        "-m",
        "moonmind.security.service_inventory_runner",
    ]
    plan = parse_service_inventory_plan_arguments(spec.command[3:])
    assert plan.native_network == spec.workload_profile.native_network
    assert plan.options == spec.workload_profile.service_inventory
    assert [endpoint.authority for endpoint in plan.endpoints] == [
        "native.example.test:2525",
        "native.example.test:2526",
        "native.example.test:8080",
    ]


@pytest.mark.parametrize(
    "change",
    ["host", "port", "script", "extra-flags", "transport", "scope", "workspace"],
)
async def test_signed_inventory_mcp_rejects_scope_expansion_and_arbitrary_execution(
    environment, monkeypatch, change
):
    from moonmind.container_job_cli import ContainerJobMcpClient

    settings, selected, scope = admission_bundle()
    session = Session(environment, monkeypatch, scope=scope)
    await seed_legacy_assessment_admission(environment, scope)
    temporal = AsyncMock()
    session.service = {
        "temporal": temporal,
        "backend_settings": settings,
        "authorizer": NO_GRANTS,
    }
    selected = copy.deepcopy(selected)
    spec = {
        "workloadProfile": selected,
        "inputAttachments": [],
        "workspaceRef": {
            "kind": "sandbox",
            "workspaceId": "assess",
            "relativePath": "repo",
        },
        "resources": {"cpuMillis": 1000, "memoryMiB": 256, "pids": 64},
        "timeoutSeconds": 60,
    }
    if change == "host":
        selected["nativeNetwork"]["targets"][0]["hosts"] = ["unadmitted.example.test"]
    elif change == "port":
        selected["nativeNetwork"]["targets"][0]["ports"].append(9090)
    elif change == "script":
        selected["serviceInventory"]["script"] = "arbitrary"
    elif change == "extra-flags":
        spec["command"] = ["--script", "arbitrary"]
    elif change == "transport":
        selected["nativeNetwork"]["operation"] = "udp"
    elif change == "workspace":
        spec["workspaceRef"]["workspaceId"] = "another-session"
    else:
        selected["assessmentRef"] = "other-assessment"
    client = ContainerJobMcpClient(
        endpoint="http://api:8000/mcp/container",
        bearer_token=os.environ["MOONMIND_CONTAINER_JOBS_BEARER_TOKEN"],
    )
    async with AsyncClient(
        transport=ASGITransport(app=session.app), base_url="http://api:8000"
    ) as http:
        response = await http.post(
            "/mcp/container/tools/call",
            headers=client._authorization_headers(),
            json={
                "tool": "container.submit",
                "arguments": {
                    "idempotencyKey": "inventory-" + change,
                    "source": {
                        "source": "omnigent",
                        "agentRunId": "run",
                        "workflowId": "assessment",
                        "stepId": "assess",
                        "omnigentConversationId": "session",
                    },
                    "spec": spec,
                },
            },
        )
    assert response.status_code in {400, 403, 422}, response.text
    code = response.json()["detail"]["code"]
    expected = {
        "host": "container_capability_scope_mismatch",
        "port": "container_capability_scope_mismatch",
        "scope": "container_capability_scope_mismatch",
        "workspace": "container_capability_scope_mismatch",
        "extra-flags": "invalid_request",
        "script": "invalid_tool_arguments",
        "transport": "invalid_tool_arguments",
    }
    assert code == expected[change], response.text
    temporal.start_container_job.assert_not_awaited()
    client.close()
