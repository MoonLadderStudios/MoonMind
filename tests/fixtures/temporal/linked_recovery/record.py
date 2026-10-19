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

SOURCE_REVISION = "78cbe1682249deeaa33b64791859dedbf7188005"


def recording_destination() -> Path:
    parser = argparse.ArgumentParser(
        description="Record linked-recovery histories from the unmodified pre-retirement workflow."
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[4]
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    if revision != SOURCE_REVISION:
        raise SystemExit(f"Record only from {SOURCE_REVISION}, not {revision}")
    for extra in ([], ["--cached"]):
        subprocess.run(
            ["git", "diff", "--exit-code", *extra, "--", "moonmind", "api_service"],
            cwd=repo,
            check=True,
        )
    from moonmind.workflows.temporal.workflows import run as run_module

    if not Path(run_module.__file__).resolve().is_relative_to(repo):
        raise SystemExit("Imported workflow does not belong to the recording checkout")
    return args.output


async def main():
    destination = recording_destination()
    destination.mkdir(parents=True, exist_ok=True)
    async with await WorkflowEnvironment.start_local(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        await register_deployment_search_attributes(env)
        for state_machine in (False, True):
            kind = "typed-state-machine" if state_machine else "legacy-policy"
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
                assert fake.steps == ["implement", "finish"], fake.steps
                ledger = await handle.query("get_step_ledger")
                assert ledger["steps"][0]["preservedFrom"]["workflowId"] == "mm:source"
                history = await handle.fetch_history()
                assert RUN_LINKED_RECOVERY_RETIREMENT_PATCH not in _patches(history)
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
                    [RESTORE_ACTIVITY]
                    if state_machine
                    else ["step_checkpoint.validate", "workspace.apply_policy"]
                ), effects
                (destination / f"{kind}.json.gz").write_bytes(
                    gzip.compress(history.to_json().encode(), mtime=0)
                )
                print(kind, len(history.events), effects, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
