#!/usr/bin/env python3
"""Non-mutating post-deployment canary for MoonMind.PRResolver registration."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

import httpx
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter


async def _run(args: argparse.Namespace) -> int:
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        response = await http_client.get(args.readiness_url)
        response.raise_for_status()
        readiness = response.json()
    if not isinstance(readiness, dict) or readiness.get("ready") is not True:
        raise RuntimeError("workflow fleet is not ready")
    workflow_types = {str(item) for item in readiness.get("workflowTypes", [])}
    task_queues = {str(item) for item in readiness.get("taskQueues", [])}
    if "MoonMind.PRResolver" not in workflow_types:
        raise RuntimeError("readiness does not advertise MoonMind.PRResolver")
    if args.task_queue not in task_queues:
        raise RuntimeError(f"readiness does not advertise task queue {args.task_queue}")
    registry_fingerprint = str(
        readiness.get("registryFingerprint")
        or next(iter(readiness.get("registryFingerprints", [])), "")
    )
    build_id = str(
        readiness.get("buildId") or next(iter(readiness.get("buildIds", [])), "")
    )
    if args.expected_registry_fingerprint and (
        registry_fingerprint != args.expected_registry_fingerprint
    ):
        raise RuntimeError("workflow registry fingerprint does not match deployment")
    if args.expected_build_id and build_id != args.expected_build_id:
        raise RuntimeError("workflow worker build does not match deployment")

    client = await Client.connect(
        args.address,
        namespace=args.namespace,
        data_converter=pydantic_data_converter,
    )
    workflow_id = f"canary:pr-resolver:{uuid.uuid4()}"
    result = await client.execute_workflow(
        "MoonMind.PRResolver",
        {
            "workflowType": "MoonMind.PRResolver",
            "parentWorkflowId": workflow_id,
            "principal": "deployment-canary",
            "repository": "canary/no-mutation",
            "prNumber": 1,
            "prUrl": "https://example.invalid/canary/pull/1",
            "stepId": "canary",
            "correlationId": workflow_id,
            "baseAgentRequest": {"canary": True},
            "canaryMode": True,
            "implementationIdentity": {"implementationContract": "pr-resolver-core/v1"},
            "workerCapability": {
                "available": True,
                "workflowType": "MoonMind.PRResolver",
                "taskQueue": args.task_queue,
                "registryFingerprint": registry_fingerprint,
                "buildId": build_id,
            },
        },
        id=workflow_id,
        task_queue=args.task_queue,
    )
    metadata = result.get("metadata") if isinstance(result, dict) else {}
    if not isinstance(metadata, dict) or metadata.get("canary") is not True:
        raise RuntimeError("PR resolver canary returned an invalid terminal result")
    observed_capability = metadata.get("workerCapability")
    if not isinstance(observed_capability, dict) or (
        observed_capability.get("registryFingerprint") != registry_fingerprint
    ):
        raise RuntimeError("PR resolver canary did not preserve worker identity")
    print(
        json.dumps(
            {
                "workflowId": workflow_id,
                "buildId": build_id,
                "registryFingerprint": registry_fingerprint,
                "result": result,
            },
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default="localhost:7233")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--task-queue", default="mm.workflow")
    parser.add_argument(
        "--readiness-url",
        default="http://localhost:8080/readyz",
    )
    parser.add_argument("--expected-registry-fingerprint")
    parser.add_argument("--expected-build-id")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
