#!/usr/bin/env python3
"""Validate advisory hints or explicitly import observed costs (MoonMind#4629).

No arguments checks hint usability without changing the baseline. Use
--import-reports with --run-id, --revision and --attempt to replace hints from
one complete successful four-shard recording or equivalent unsharded suite.
Costs are the sum of pytest report durations for setup, call and teardown.
Missing/stale/unusable hints never gate ordinary test selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DURATIONS_PATH = REPO_ROOT / "tests" / ".reliability-test-durations.json"
RELIABILITY_PREFIX = "tests/integration/reliability"
RELIABILITY_MARKER_EXPR = (
    "reliability_journey and not provider_verification and not requires_credentials"
)
COST_SEMANTICS = "pytest-report/setup+call+teardown"
VALID, UNUSABLE, MISSING = 0, 2, 3


def valid_duration(value: object) -> bool:
    """Zero/tiny costs are observations; booleans and nonfinite costs are not."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def nodeids_digest(nodeids: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(nodeids), separators=(",", ":")).encode()
    ).hexdigest()


def file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "missing"


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path):
    return json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
    )


def _nodeid(value: object) -> bool:
    return (
        isinstance(value, str)
        and "::" in value
        and value.split("::", 1)[0].endswith(".py")
    )


def validate_hints_file(path: Path = DURATIONS_PATH) -> int:
    """Return VALID (0), UNUSABLE (2), or MISSING (3); never modify hints."""
    try:
        payload = _read_json(path)
    except FileNotFoundError:
        print(f"warning: duration hints file is missing: {path}", file=sys.stderr)
        return MISSING
    except (OSError, ValueError) as exc:
        print(
            f"warning: duration hints unusable ({exc}); using no history",
            file=sys.stderr,
        )
        return UNUSABLE
    if not isinstance(payload, dict) or not payload:
        print("warning: no usable duration hints; using no history", file=sys.stderr)
        return UNUSABLE
    for nodeid, duration in payload.items():
        if not _nodeid(nodeid) or not valid_duration(duration):
            print(
                f"warning: invalid duration hint for {nodeid!r}; using no history",
                file=sys.stderr,
            )
            return UNUSABLE
    return VALID


def durations_pytest_args(path: Path = DURATIONS_PATH) -> list[str]:
    """Use validated hints or an absent path, never the plugin's implicit cache.

    The temporary directory is removed before returning. Its unused file path
    supplies pytest-split's supported FileNotFoundError fallback, even when an
    unrelated .test_durations exists in the runner's working directory.
    """
    if validate_hints_file(path) == VALID:
        return ["--durations-path", str(path)]
    with tempfile.TemporaryDirectory(prefix="moonmind-pytest-no-history-") as directory:
        return ["--durations-path", str(Path(directory) / "durations.json")]


def import_observations(
    reports: list[Path], *, run_id: str, revision: str, attempt: str
) -> dict[str, float]:
    """Accept only a coherent complete observed corpus; perform no writes."""
    if not all((run_id, revision, attempt)):
        raise ValueError("identify the source with --run-id, --revision and --attempt")
    if len({path.resolve() for path in reports}) != len(reports):
        raise ValueError("duplicate report paths")
    hints: dict[str, float] = {}
    seen_groups: set[int] = set()
    common: dict | None = None
    source_identity = {"run_id": run_id, "revision": revision, "attempt": attempt}
    common_fields = (
        "shard_count",
        "universe_count",
        "universe_digest",
        "hints_digest",
        "baseline_digest",
    )
    for path in reports:
        payload = _read_json(path)
        if (
            not isinstance(payload, dict)
            or not _count(payload.get("schema_version"))
            or payload["schema_version"] != 1
        ):
            raise ValueError(f"{path}: unsupported observation schema")
        if (
            payload.get("identity") != "pytest-nodeid"
            or payload.get("cost_semantics") != COST_SEMANTICS
        ):
            raise ValueError(f"{path}: incompatible identity or timing semantics")
        recording, source = payload.get("recording"), payload.get("source")
        if not isinstance(recording, dict) or not isinstance(source, dict):
            raise ValueError(f"{path}: missing source recording")
        for key, value in source_identity.items():
            if recording.get(key) != value or source.get(key) != value:
                raise ValueError(f"{path}: mismatched source {key}")
        if (
            source.get("selected") is not True
            or source.get("test_outcome") != "success"
            or not _count(recording.get("exit_code"))
            or recording["exit_code"] != 0
        ):
            raise ValueError(
                f"{path}: recording is not a complete successful execution"
            )
        count, group = recording.get("shard_count"), recording.get("shard")
        if (
            not _count(count)
            or count not in (1, 4)
            or not _count(group)
            or not 1 <= group <= count
        ):
            raise ValueError(f"{path}: expected four shards or one complete suite")
        if group in seen_groups:
            raise ValueError(f"{path}: duplicate shard report {group}")
        seen_groups.add(group)
        suite, shard = (
            (f"reliability-shard-{group}", str(group))
            if count == 4
            else ("reliability", "")
        )
        if payload.get("suite") != suite or source.get("shard") != shard:
            raise ValueError(f"{path}: mismatched suite/shard identity")
        if (
            recording.get("collection_args") != [RELIABILITY_PREFIX]
            or recording.get("marker_expr") != RELIABILITY_MARKER_EXPR
            or recording.get("keyword_expr") != ""
            or recording.get("deselected_args") != []
            or (count == 4 and recording.get("algorithm") != "least_duration")
        ):
            raise ValueError(
                f"{path}: not the complete provider-free reliability selection"
            )
        for key in ("hints_digest", "baseline_digest"):
            if (
                not isinstance(recording.get(key), str)
                or not recording[key]
                or recording.get(key + "_after") != recording[key]
            ):
                raise ValueError(f"{path}: selection baseline changed during recording")
        if common is None:
            common = {key: recording.get(key) for key in common_fields}
        if any(recording.get(key) != common[key] for key in common_fields):
            raise ValueError(f"{path}: mixed corpus, shard count or selection hints")
        cases = payload.get("cases")
        if not isinstance(cases, list):
            raise ValueError(f"{path}: missing observed cases")
        for key in ("tests", "failures", "errors", "skipped"):
            if not _count(payload.get(key)) or payload[key] != (
                len(cases) if key == "tests" else 0
            ):
                raise ValueError(f"{path}: incomplete or unsuccessful case counts")
        nodes = []
        for case in cases:
            if not isinstance(case, dict):
                raise ValueError(f"{path}: malformed case")
            nodeid, duration, phases = (
                case.get("nodeid"),
                case.get("duration"),
                case.get("phases"),
            )
            if not _nodeid(nodeid) or not nodeid.startswith(RELIABILITY_PREFIX + "/"):
                raise ValueError(f"{path}: missing exact reliability pytest node ID")
            if nodeid in hints:
                raise ValueError(f"{path}: duplicate observed node {nodeid}")
            if (
                case.get("observed") is not True
                or not valid_duration(duration)
                or not isinstance(phases, dict)
                or set(phases) != {"setup", "call", "teardown"}
                or not all(valid_duration(value) for value in phases.values())
            ):
                raise ValueError(f"{path}: invalid or unobserved duration for {nodeid}")
            total = sum(phases.values())
            if not valid_duration(total) or not math.isclose(
                duration, total, rel_tol=1e-12, abs_tol=0.0
            ):
                raise ValueError(f"{path}: incompatible phase total for {nodeid}")
            hints[nodeid] = duration
            nodes.append(nodeid)
        if (
            not _count(recording.get("selected_count"))
            or recording["selected_count"] != len(nodes)
            or recording.get("selected_digest") != nodeids_digest(nodes)
        ):
            raise ValueError(f"{path}: incomplete selected-node observations")
    if common is None or seen_groups != set(range(1, common["shard_count"] + 1)):
        raise ValueError(
            "missing shard reports; supply one complete four-shard attempt or complete suite"
        )
    if (
        not hints
        or not _count(common["universe_count"])
        or common["universe_count"] != len(hints)
        or common["universe_digest"] != nodeids_digest(list(hints))
    ):
        raise ValueError(
            "observations do not cover the complete eligible corpus exactly once"
        )
    return hints


def refresh(
    reports: list[Path],
    *,
    run_id: str,
    revision: str,
    attempt: str,
    durations_path: Path = DURATIONS_PATH,
) -> int:
    """Validate all observations before atomically replacing the hint mapping."""
    if durations_path.resolve() in {path.resolve() for path in reports}:
        raise ValueError("output cannot replace an observation report")
    hints = import_observations(
        reports, run_id=run_id, revision=revision, attempt=attempt
    )
    durations_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=durations_path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(
                json.dumps(hints, indent=2, sort_keys=True, allow_nan=False) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(
            durations_path.stat().st_mode if durations_path.exists() else 0o644
        )
        os.replace(temporary, durations_path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                print(
                    f"warning: temporary hints retained at {temporary}: {exc}",
                    file=sys.stderr,
                )
    print(
        f"refreshed {len(hints)} observed hints from run {run_id}, revision {revision}, attempt {attempt}"
    )
    return len(hints)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="exit 0 usable, 2 unusable, 3 missing; never write",
    )
    mode.add_argument(
        "--pytest-duration-path",
        action="store_true",
        help="print the validated hints path or an absent no-history path; always exit 0",
    )
    mode.add_argument(
        "--import-reports",
        nargs="+",
        type=Path,
        help="complete per-row JSON observations from the existing reporter",
    )
    parser.add_argument("--run-id", default="")
    parser.add_argument("--revision", default="")
    parser.add_argument("--attempt", default="")
    parser.add_argument(
        "--output",
        type=Path,
        default=DURATIONS_PATH,
        help="hint mapping (default: tests/.reliability-test-durations.json)",
    )
    args = parser.parse_args(argv)
    if args.pytest_duration_path:
        print(durations_pytest_args(args.output)[1])
        return 0
    if args.import_reports:
        try:
            refresh(
                args.import_reports,
                run_id=args.run_id,
                revision=args.revision,
                attempt=args.attempt,
                durations_path=args.output,
            )
        except (OSError, ValueError, TypeError) as exc:
            print(
                f"duration refresh rejected: {exc}; baseline unchanged", file=sys.stderr
            )
            return 1
        return 0
    status = validate_hints_file(args.output)
    return status if args.validate_only else 0


if __name__ == "__main__":
    raise SystemExit(main())
