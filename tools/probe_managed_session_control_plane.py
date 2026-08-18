#!/usr/bin/env python3
"""Probe control-plane reachability through the production session launcher."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path

from moonmind.schemas.managed_session_models import (
    LaunchCodexManagedSessionRequest,
    TerminateCodexManagedSessionRequest,
)
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    DockerCodexManagedSessionController,
    _managed_session_docker_network,
)


async def _probe(args: argparse.Namespace) -> None:
    workspace_root = Path(
        os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs")
    )
    moonmind_url = str(os.environ.get("MOONMIND_URL") or "http://api:8000").strip()
    network_name = _managed_session_docker_network({"MOONMIND_URL": moonmind_url})
    if network_name != args.expected_network:
        raise RuntimeError(
            "managed-session control-plane network mismatch: "
            f"expected {args.expected_network!r}, resolved {network_name!r}"
        )

    session_root = workspace_root / "control-plane-network-probes" / args.session_id
    controller = DockerCodexManagedSessionController(
        workspace_volume_name=os.environ.get(
            "MOONMIND_AGENT_WORKSPACES_VOLUME_NAME", "agent_workspaces"
        ),
        codex_volume_name=os.environ.get("CODEX_VOLUME_NAME", "codex_auth_volume"),
        workspace_root=str(workspace_root),
        network_name=network_name,
        moonmind_url=moonmind_url,
        docker_binary=os.environ.get("MOONMIND_DOCKER_BINARY", "docker"),
        docker_host=(
            os.environ.get("DOCKER_HOST")
            or os.environ.get("SYSTEM_DOCKER_HOST")
            or "tcp://docker-proxy:2375"
        ),
    )
    request = LaunchCodexManagedSessionRequest(
        agentRunId=f"{args.session_id}-run",
        workflowId=f"{args.session_id}-workflow",
        sessionId=args.session_id,
        threadId=f"{args.session_id}-thread",
        workspacePath=str(session_root / "repo"),
        sessionWorkspacePath=str(session_root / "session"),
        artifactSpoolPath=str(session_root / "artifacts"),
        codexHomePath=str(session_root / "codex-home"),
        imageRef=args.image_ref,
        workloadMode="no-docker",
        environment={"MOONMIND_URL": moonmind_url},
    )

    handle = await controller.launch_session(request)
    locator = TerminateCodexManagedSessionRequest(
        sessionId=handle.session_state.session_id,
        sessionEpoch=handle.session_state.session_epoch,
        containerId=handle.session_state.container_id,
        threadId=handle.session_state.thread_id,
        reason="control-plane network conformance probe complete",
    )
    health_url = moonmind_url.rstrip("/") + "/healthz"
    try:
        await controller._run(  # noqa: SLF001 - live conformance boundary
            (
                "docker",
                "exec",
                handle.session_state.container_id,
                "python3",
                "-c",
                "import sys, urllib.request; "
                "response = urllib.request.urlopen(sys.argv[1], timeout=10); "
                "assert 200 <= response.status < 300",
                health_url,
            )
        )
        print(
            json.dumps(
                {
                    "containerId": handle.session_state.container_id,
                    "healthUrl": health_url,
                    "network": network_name,
                    "sessionId": args.session_id,
                    "status": "passed",
                },
                sort_keys=True,
            )
        )
    finally:
        if not args.keep_running:
            await controller.terminate_session(locator)


def _session_id(value: str) -> str:
    if re.fullmatch(r"[a-z0-9][a-z0-9-]*", value) is None:
        raise argparse.ArgumentTypeError(
            "session id must contain only lowercase letters, digits, and hyphens"
        )
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True, type=_session_id)
    parser.add_argument("--image-ref", required=True)
    parser.add_argument("--expected-network", required=True)
    parser.add_argument("--keep-running", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(_probe(_parse_args()))
