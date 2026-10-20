"""Manual fixture recorder; copy this and the test module into the source checkout."""

import argparse
import asyncio
import gzip
import subprocess
from contextlib import AsyncExitStack
from pathlib import Path

from tests.integration.temporal.test_linked_recovery_retained_history import (
    MOONMIND_TEMPORAL_DATA_CONVERTER,
    RESTORE_ACTIVITY,
    RUN_LINKED_RECOVERY_RETIREMENT_PATCH,
    FakeActivities,
    WorkflowEnvironment,
    _activity_names,
    _activity_workers,
    _parameters,
    _patches,
    _start,
    _workflow_worker,
    register_deployment_search_attributes,
)

SOURCE_REVISIONS = {
    "pre-retirement": "78cbe1682249deeaa33b64791859dedbf7188005",
    "retirement-v1": "edc57563594680f0b0e11e47f5681510d80d0788",
}


def recording_destination() -> tuple[Path, str]:
    parser = argparse.ArgumentParser(
        description="Record linked-recovery histories from an unmodified source workflow."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generation", choices=SOURCE_REVISIONS, default="pre-retirement")
    args = parser.parse_args()
    source_revision = SOURCE_REVISIONS[args.generation]
    repo = Path(__file__).resolve().parents[4]
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    if revision != source_revision:
        raise SystemExit(f"Record only from {source_revision}, not {revision}")
    for extra in ([], ["--cached"]):
        subprocess.run(
            ["git", "diff", "--exit-code", *extra, "--", "moonmind", "api_service"],
            cwd=repo,
            check=True,
        )
    from moonmind.workflows.temporal.workflows import run as run_module

    if not Path(run_module.__file__).resolve().is_relative_to(repo):
        raise SystemExit("Imported workflow does not belong to the recording checkout")
    return args.output, args.generation


async def main():
    destination, generation = recording_destination()
    retired = generation == "retirement-v1"
    destination.mkdir(parents=True, exist_ok=True)
    async with await WorkflowEnvironment.start_local(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        await register_deployment_search_attributes(env)
        for state_machine in (False, True):
            kind = (
                "retirement-v1-" + ("typed" if state_machine else "legacy") if retired
                else "typed-state-machine" if state_machine else "legacy-policy"
            )
            fake = FakeActivities()
            queue = f"pre-retirement-{kind}"
            async with AsyncExitStack() as stack:
                await _activity_workers(stack, env.client, fake)
                await stack.enter_async_context(_workflow_worker(env.client, queue))
                handle = await _start(
                    env, queue, _parameters(state_machine=state_machine)
                )
                result = await asyncio.wait_for(handle.result(), 30)
                assert result["status"] == "success", result
                assert fake.steps == (["prepare", "implement", "finish"] if retired else ["implement", "finish"]), fake.steps
                ledger = await handle.query("get_step_ledger")
                assert bool(ledger["steps"][0].get("preservedFrom")) is not retired
                history = await handle.fetch_history()
                assert (RUN_LINKED_RECOVERY_RETIREMENT_PATCH in _patches(history)) is retired
                effects = [
                    name
                    for name in _activity_names(history)
                    if name
                    in {
                        "step_checkpoint.validate",
                        "workspace.apply_policy",
                        RESTORE_ACTIVITY,
                    }
                ]
                assert effects == (
                    [] if retired else (
                        [RESTORE_ACTIVITY] if state_machine
                        else ["step_checkpoint.validate", "workspace.apply_policy"]
                    )
                ), effects
                (destination / f"{kind}.json.gz").write_bytes(
                    gzip.compress(history.to_json().encode(), mtime=0)
                )
                print(kind, len(history.events), effects, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
