"""Replay a Claude turn that ended while its background subagent still ran.

MoonMind settles a generic-host step when the Claude turn ends and then
removes the host. The incident's assessment subagent therefore never reported
back, and the declared verdict artifact was never written. The launched Claude
child must keep subagents and shells in the foreground instead.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.harness_platform.host_classes import (
    DEFAULT_HOST_CLASS_TEMPLATES,
    HostClass,
    get_launch_policy,
)
from moonmind.omnigent.host_ports import HostLaunchSpec
from moonmind.omnigent.host_services.launcher import DockerOmnigentHostLauncher
from moonmind.omnigent.host_services.runtime_scripts import (
    OmnigentRuntimeScriptService,
)
from tests.integration.reliability.helpers import load_replay

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

REPLAY_ID = "claude-native-background-task-outlives-turn"
MANIFEST = load_replay(REPLAY_ID, "manifest.json")
EXPECTED = load_replay(REPLAY_ID, "expected-outcome.json")


def _host_class(harness_id: str) -> HostClass:
    template = next(
        item
        for item in DEFAULT_HOST_CLASS_TEMPLATES
        if harness_id in item.harness_ids and item.runtime_pack_ref is not None
    )
    return HostClass.model_validate(
        {
            "hostClassId": template.host_class_id,
            "version": template.version,
            "imageRef": "ghcr.io/example/shared@sha256:" + "f" * 64,
            "omnigentVersion": "0.12.0",
            "omnigentBuildDigest": "sha256:" + "1" * 64,
            "architectures": list(template.architectures),
            "declaredHarnessImplementations": [
                {
                    "harnessId": harness_id,
                    "implementationRef": "omnigent-harness-implementation:sha256:"
                    + "3" * 64,
                }
            ],
            "integrationModes": list(template.integration_modes),
            "materializerRefs": list(template.materializer_refs),
            "features": {"readOnlyRoot": True},
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
        }
    )


async def _runner_child_environment(host_class: HostClass) -> dict[str, str]:
    """Launch through production builders, then apply Omnigent's runner hop."""

    calls: list[list[str]] = []

    class Backend:
        async def run(self, argv, **_kwargs):
            calls.append(list(argv))
            return (0, "container-id" if argv[1] == "create" else "", "")

    launcher = DockerOmnigentHostLauncher(
        backend=Backend(),
        runtime_scripts=OmnigentRuntimeScriptService(),
        server_url="http://omnigent:8000",
    )
    spec = HostLaunchSpec.model_validate(
        {
            "executionPlanRef": "plan:replay",
            "stepExecutionId": f"{MANIFEST['incidentWorkflowId']}:assessment",
            "runtimeBindingId": "binding-replay",
            "hostLeaseRef": "host-lease:replay",
            "hostLeaseGeneration": 1,
            "hostClassRef": host_class.ref,
            "imageRef": host_class.imageRef,
            "serverEndpointRef": "default",
            "serverUrl": "http://omnigent:8000",
            "networkRef": "moonmind_default",
            "limits": {"cpuMillis": 2000},
            "runtime": {},
            "correlationName": "mm-host-replay",
            "workspaceAttachment": {
                "kind": "bind",
                "sourceRef": "/tmp/work",
                "targetPath": "/workspaces/run",
                "accessMode": "read-write",
            },
            "skillAttachment": {
                "kind": "bind",
                "sourceRef": "/tmp/skills",
                "targetPath": "/opt/moonmind-skills",
                "accessMode": "read-only",
            },
            "stateAttachment": {
                "kind": "volume",
                "sourceRef": "mm-host-state-replay",
                "targetPath": "/home/app/.omnigent",
                "accessMode": "read-write",
            },
            "labels": {},
        }
    )
    await launcher.launch(
        spec=spec,
        host_class=host_class,
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        credential_handles=[],
    )
    create = next(argv for argv in calls if argv[:2] == ["docker", "create"])
    container_environment = dict(
        create[index + 1].split("=", 1)
        for index, value in enumerate(create)
        if value == "--env"
    )
    # Omnigent forwards only the named variables from the host to the runner;
    # the native Claude terminal inherits the runner environment.
    forwarded = container_environment["OMNIGENT_RUNNER_ENV_PASSTHROUGH"].split(",")
    return {
        name: container_environment[name]
        for name in forwarded
        if name in container_environment
    }


async def test_claude_child_cannot_leave_background_work_after_its_turn() -> None:
    assert MANIFEST["transcript"][0]["tool"] == "Agent"
    child = await _runner_child_environment(_host_class(MANIFEST["harnessId"]))

    for name, value in EXPECTED["claudeChildEnvironment"].items():
        assert child.get(name) == value


async def test_other_harness_hosts_keep_their_environment() -> None:
    child = await _runner_child_environment(
        _host_class(EXPECTED["unaffectedHarnessId"])
    )

    assert not set(EXPECTED["claudeChildEnvironment"]) & set(child)
