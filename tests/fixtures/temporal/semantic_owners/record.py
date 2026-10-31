"""Record once from the pre-extraction checkout, never from the candidate."""

import argparse
import asyncio
import gzip
import hashlib
import json
from pathlib import Path

from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tests.integration.temporal.test_semantic_owner_replay import (
    SemanticOwnerBoundary,
    boundary_input,
)


@activity.defn(name="semantic_owner.capture")
async def capture(decision):
    return decision


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.source.read_text())
    root = Path(__file__).resolve().parents[4]
    for path, digest in source["files"].items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == digest, path
    source["histories"] = {}
    args.output.mkdir(parents=True, exist_ok=True)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with (
            Worker(env.client, task_queue="semantic-effects", activities=[capture]),
            Worker(
                env.client, task_queue="semantic-boundary", workflows=[SemanticOwnerBoundary],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            for name in ("canonical", "retained"):
                handle = await env.client.start_workflow(
                    SemanticOwnerBoundary.run, boundary_input(retained=name == "retained"),
                    id=f"pre-extraction-{name}", task_queue="semantic-boundary",
                )
                result = await asyncio.wait_for(handle.result(), timeout=30)
                assert result["gate"]["recommendedNextAction"] == "blocked"
                assert result["publication"]["reason"] == "publication_unauthorized"
                assert result["request"]["executionBudget"]["consumedSeconds"] == 7
                history = await handle.fetch_history()
                raw = gzip.compress(history.to_json().encode(), mtime=0)
                (args.output / f"{name}.json.gz").write_bytes(raw)
                source["histories"][name] = hashlib.sha256(raw).hexdigest()
                print(name, len(history.events), source["histories"][name], flush=True)
    (args.output / "provenance.json").write_text(json.dumps(source, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
