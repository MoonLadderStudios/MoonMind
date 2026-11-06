"""Recorded result of the rotating fault-lab seed sweep.

Source issue: MoonLadderStudios/MoonMind#5223.

The rotating sweep is exploratory: it runs only on the scheduled or directly
dispatched CI / Test Suite, so no PR or publication waits on it. Its value is the
record it leaves. Every executed sweep writes the window it covered, how far it
got within its time budget and which seeds failed, and the job fails when that
record is missing or covers a different window. A sweep that pytest skipped, or
that fell back to the fixed PR corpus, therefore cannot pass.

Failing seeds keep their reproduction-complete bundles beside this record
(:func:`tools.omnigent_faultlab.diagnostics.write_diagnostic_bundle`).
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

RESULT_FILENAME = "rotating-sweep-result.json"
#: Failing seeds named in the record; the full count is always kept.
MAX_RECORDED_SEEDS = 25

_TITLE = "Rotating fault-lab sweep"


class SweepResultError(ValueError):
    """The recorded sweep result is missing, mismatched or not a pass."""


def write_sweep_result(
    directory: Path | str,
    *,
    seeds: range,
    ran: int,
    last_seed: int | None,
    failing_seeds: Sequence[int],
    bundles_written: int,
    budget_seconds: float,
    elapsed_seconds: float,
    error: str | None = None,
) -> Path:
    """Write the sweep's window, progress and outcome to ``<directory>``."""

    if error:
        status = "error"
    elif failing_seeds:
        status = "failed"
    else:
        status = "passed"
    record = {
        "offset": seeds.start,
        "count": len(seeds),
        "ran": ran,
        "lastSeed": last_seed,
        "truncated": ran < len(seeds),
        "failures": len(failing_seeds),
        "failingSeeds": list(failing_seeds[:MAX_RECORDED_SEEDS]),
        "bundlesWritten": bundles_written,
        "budgetSeconds": budget_seconds,
        "elapsedSeconds": round(elapsed_seconds, 3),
        "error": error,
        "status": status,
    }
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / RESULT_FILENAME
    target.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return target


def check_sweep_result(
    directory: Path | str, *, offset: int, count: int
) -> dict[str, Any]:
    """Return the recorded result only if it passed over the requested window."""

    path = Path(directory) / RESULT_FILENAME
    if not path.is_file():
        raise SweepResultError(
            f"the sweep did not record {path}; it was skipped or stopped "
            "before any result"
        )
    record = json.loads(path.read_text(encoding="utf-8"))
    if (record["offset"], record["count"]) != (offset, count):
        raise SweepResultError(
            f"recorded window {record['offset']}+{record['count']} does not match "
            f"the requested window {offset}+{count}"
        )
    if record["error"]:
        raise SweepResultError(
            f"sweep stopped with {record['error']} after seed {record['lastSeed']}"
        )
    if record["failures"]:
        raise SweepResultError(
            f"{record['failures']} seed(s) violated invariants, including "
            f"{record['failingSeeds']}; bundles are under {path.parent}"
        )
    if record["ran"] < 1:
        raise SweepResultError("the sweep executed no seed")
    return record


def _summary(record: dict[str, Any]) -> str:
    end = record["offset"] + record["count"]
    return (
        f"window={record['offset']}..{end} ran={record['ran']} "
        f"failures={record['failures']} truncated={str(record['truncated']).lower()} "
        f"lastSeed={record['lastSeed']} elapsed={record['elapsedSeconds']}s "
        f"budget={record['budgetSeconds']}s"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", required=True, type=Path)
    parser.add_argument("--offset", required=True, type=int)
    parser.add_argument("--count", required=True, type=int)
    args = parser.parse_args(argv)

    path = args.dir / RESULT_FILENAME
    try:
        record = check_sweep_result(args.dir, offset=args.offset, count=args.count)
    except SweepResultError as exc:
        line = f"::error title={_TITLE}::{exc}"
        detail = path.read_text(encoding="utf-8") if path.is_file() else ""
        status = 1
    else:
        line = f"::notice title={_TITLE}::{_summary(record)}"
        detail = ""
        status = 0
        if record["truncated"]:
            print(
                f"::warning title={_TITLE}::time budget stopped the sweep after "
                f"{record['ran']} of {record['count']} seeds"
            )
    print(line)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        body = [f"### {_TITLE}", "", line.split("::", 2)[-1]]
        if detail:
            body += ["", "```json", detail, "```"]
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write("\n".join(body) + "\n")
    return status


if __name__ == "__main__":  # pragma: no cover - exercised via the rotating CI job
    raise SystemExit(main())
