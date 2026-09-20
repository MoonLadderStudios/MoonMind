#!/usr/bin/env python3
"""Per-job pytest evidence summary for the backend matrix.

MoonLadderStudios/MoonMind#4370, #4371: each backend-matrix row streams its pytest
output through ``tee`` to a text log, keeps its JUnit report, and then calls
this small reporting hook to derive the slowest-test report, a per-shard
duration-hints snapshot, and a ``$GITHUB_STEP_SUMMARY`` section from standard
pytest/JUnit output. No new metrics service is introduced.

Contract guarantees (checked by unit tests, not by live Actions runs):

- A missing JUnit file after process termination is reported as
  ``unavailable``/``interrupted`` -- never as zero tests or a passing suite.
- No progress-percentage parsing is used; counts come from the JUnit
  ``testsuite`` attributes only.
- A parsing problem never fails the job: ``main()`` always exits 0 and
  records the error in the summary instead.
- The committed partition input is never modified: duration hints are
  written only to the caller-supplied per-shard snapshot path, which must
  be a separate artifact file.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


SLOWEST_LIMIT = 25
SUMMARY_SLOWEST_SHOWN = 10


@dataclass(frozen=True)
class CaseTiming:
    nodeid: str
    classname: str
    time: float


@dataclass(frozen=True)
class JUnitSummary:
    tests: int
    failures: int
    errors: int
    skipped: int
    time: float
    cases: tuple[CaseTiming, ...]


def parse_junit(path: Path) -> JUnitSummary:
    """Parse a JUnit XML file produced by pytest --junitxml.

    Raises FileNotFoundError when the report is absent and ValueError when
    the XML cannot be interpreted. Counts come from testsuite attributes;
    individual <testcase> entries supply per-case timings.
    """
    tree = ET.parse(path)
    root = tree.getroot()
    suites: list[ET.Element] = []
    if root.tag == "testsuites":
        suites = list(root.iter("testsuite"))
    elif root.tag == "testsuite":
        suites = [root]
    else:
        raise ValueError(f"unexpected JUnit root tag: {root.tag!r}")

    tests = failures = errors = skipped = 0
    total_time = 0.0
    cases: list[CaseTiming] = []
    for suite in suites:
        tests += int(float(suite.get("tests", "0")))
        failures += int(float(suite.get("failures", "0")))
        errors += int(float(suite.get("errors", "0")))
        skipped += int(float(suite.get("skipped", "0")))
        total_time += float(suite.get("time", "0") or 0)
        for case in suite.iter("testcase"):
            classname = case.get("classname", "")
            name = case.get("name", "")
            nodeid = f"{classname}::{name}" if classname else name
            try:
                duration = float(case.get("time", "0") or 0)
            except ValueError:
                duration = 0.0
            cases.append(CaseTiming(nodeid=nodeid, classname=classname, time=duration))
    cases.sort(key=lambda c: c.time, reverse=True)
    return JUnitSummary(
        tests=tests,
        failures=failures,
        errors=errors,
        skipped=skipped,
        time=total_time,
        cases=tuple(cases),
    )


def write_slowest_report(summary: JUnitSummary, path: Path, limit: int = SLOWEST_LIMIT) -> None:
    """Write the slowest-test text report derived from JUnit timings."""
    lines = [
        f"# Slowest tests ({min(limit, len(summary.cases))} of {len(summary.cases)} cases)",
        f"# suite time: {summary.time:.2f}s tests={summary.tests} "
        f"failures={summary.failures} errors={summary.errors} skipped={summary.skipped}",
    ]
    for case in summary.cases[:limit]:
        lines.append(f"{case.time:.2f}s {case.nodeid}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_durations_snapshot(suite: str, summary: JUnitSummary, path: Path) -> None:
    """Write the per-shard duration-hints snapshot (separate artifact).

    This file is the #4366 maintenance input for the current shard only. It
    must never overwrite a shared/committed selection-hints baseline, and a
    partial failed-shard result must never replace a complete baseline: the
    caller uploads this snapshot path as its own artifact.
    """
    payload = {
        "suite": suite,
        "tests": summary.tests,
        "failures": summary.failures,
        "errors": summary.errors,
        "skipped": summary.skipped,
        "time": summary.time,
        "cases": [
            {"nodeid": c.nodeid, "classname": c.classname, "duration": c.time}
            for c in summary.cases
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def classify_outcome(selected: bool, junit_exists: bool, test_outcome: str) -> str:
    """Map the row state to a human-readable outcome label."""
    normalized = (test_outcome or "unknown").strip().lower()
    if not selected:
        return "intentionally unselected"
    if normalized == "skipped":
        return "intentionally unselected"
    if not junit_exists:
        if normalized == "cancelled":
            return "canceled (interrupted -- no final JUnit report)"
        if normalized == "success":
            return "passed (interrupted -- no final JUnit report)"
        if normalized == "failure":
            return "failed (interrupted -- no final JUnit report)"
        return "unavailable (no final JUnit report)"
    if normalized == "cancelled":
        return "canceled"
    if normalized == "success":
        return "passed"
    if normalized == "failure":
        return "failed"
    return "unavailable"


def render_summary(
    *,
    suite: str,
    shard: str,
    revision: str,
    run_id: str,
    attempt: str,
    selected: bool,
    test_outcome: str,
    junit: JUnitSummary | None,
    junit_path: str,
    junit_available: bool,
    log_path: str,
    log_available: bool,
    log_bytes: int | None,
    slowest_path: str,
    slowest_available: bool,
    durations_path: str,
    durations_available: bool,
    test_seconds: float | None,
    error: str | None,
    effective_command: str = "",
    pytest_timeout: str = "",
    step_budget: str = "",
    secondary_note: str | None = None,
    overhead_note: str | None = None,
) -> str:
    """Render the per-job markdown appended to $GITHUB_STEP_SUMMARY."""
    identity = suite if not shard else f"{suite} (shard {shard})"
    outcome = classify_outcome(selected, junit_available, test_outcome)
    lines = [
        f"## Backend pytest evidence -- `{suite}`",
        "",
        f"- Suite/shard identity: `{identity}`",
        f"- Tested revision: `{revision or 'unavailable'}`",
        f"- Run/attempt: `{run_id or 'unavailable'}` / `{attempt or 'unavailable'}`",
        f"- Outcome: `{outcome}` (test step outcome: `{test_outcome or 'unavailable'}`)",
        "",
        "### Effective command and budgets",
        "",
    ]
    if effective_command:
        lines.append(f"- Effective pytest command: `{effective_command}`")
    else:
        lines.append("- Effective pytest command: unavailable (not recorded for this row).")
    budgets = []
    if pytest_timeout:
        budgets.append(f"per-test timeout `{pytest_timeout}`")
    if step_budget:
        budgets.append(f"step budget `{step_budget}`")
    if budgets:
        lines.append(f"- Budgets: {', '.join(budgets)} (measured).")
    else:
        lines.append("- Budgets: unavailable (not recorded for this row).")
    lines += [
        "",
        "### Counts (from JUnit testsuite attributes, never from progress %)",
        "",
    ]
    if junit is not None:
        lines.append(
            f"- Selected/collected/executed: tests=`{junit.tests}` "
            f"failures=`{junit.failures}` errors=`{junit.errors}` "
            f"skipped=`{junit.skipped}`"
        )
        lines.append(
            "- Note: `tests` is the JUnit collected total; intentionally "
            "unselected rows report `intentionally unselected`, never zero."
        )
    elif not selected:
        lines.append("- Selected/collected/executed: intentionally unselected (no tests run).")
    else:
        lines.append(
            "- Selected/collected/executed: unavailable (no final JUnit report; "
            "not zero tests, not a passing suite)."
        )
    lines += ["", "### Timings", ""]
    if test_seconds is not None:
        lines.append(f"- Test step wall time: `{test_seconds:.0f}s` (measured).")
    else:
        lines.append("- Test step wall time: unavailable (not measured or interrupted).")
    if junit is not None:
        lines.append(f"- JUnit suite time: `{junit.time:.2f}s` (available).")
    else:
        lines.append("- JUnit suite time: unavailable (no final JUnit report).")
    lines.append("- Setup: unavailable as a separate measurement in this row (included in wall time; see job logs).")
    lines.append("- Collection: unavailable as a separate measurement in this row (included in wall time; see job logs).")
    lines.append("- Cleanup: unavailable as a separate measurement in this row (runs as a separate always() step; see job logs).")
    lines += ["", "### Slowest cases", ""]
    if junit is not None and junit.cases:
        for case in junit.cases[:SUMMARY_SLOWEST_SHOWN]:
            lines.append(f"- `{case.time:.2f}s` `{case.nodeid}`")
    elif not selected:
        lines.append("- Slowest cases: intentionally unselected.")
    else:
        lines.append("- Slowest cases: unavailable (no JUnit timings).")
    lines += ["", "### Retained evidence", ""]
    if log_available:
        size = f"`{log_bytes}` bytes" if log_bytes is not None else "available"
        lines.append(f"- Text log: `{log_path}` ({size}).")
    else:
        lines.append(f"- Text log: `{log_path}` (unavailable -- streamed live in Actions logs).")
    if junit_available:
        lines.append(f"- JUnit report: `{junit_path}` (available).")
    else:
        lines.append(
            f"- JUnit report: `{junit_path}` (unavailable -- interrupted before pytest "
            "wrote its final report; never treated as zero tests)."
        )
    if slowest_available:
        lines.append(f"- Slowest-test report: `{slowest_path}` (available).")
    else:
        lines.append(f"- Slowest-test report: `{slowest_path}` (unavailable).")
    if durations_available:
        lines.append(
            f"- Duration-hints snapshot: `{durations_path}` (available; per-shard "
            "artifact, never overwrites the shared selection baseline)."
        )
    else:
        lines.append(f"- Duration-hints snapshot: `{durations_path}` (unavailable).")
    lines += [
        "",
        "> Runner disappearance or a hard job kill may prevent final uploads. "
        + "Live Actions output (streamed via `tee`) remains the primary record in that case.",
    ]
    if overhead_note:
        lines += ["", "### Reporting overhead", "", overhead_note]
    if secondary_note:
        lines += [
            "",
            "### Secondary diagnostics/teardown (does not change the primary outcome)",
            "",
            secondary_note,
        ]
    if error:
        lines += ["", f"> Evidence hook note: `{error}` (test outcome unchanged)."]
    return "\n".join(lines) + "\n"


def _read_test_seconds(path: str | None) -> float | None:
    if not path:
        return None
    try:
        return float(Path(path).read_text(encoding="utf-8").strip().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _read_collection_note(path: str | None) -> str | None:
    """Surface secondary diagnostics/teardown failures distinctly.

    Returns a markdown note when the collection-status file records a
    failed/timed-out/missing collection operation, else None. The primary
    pytest outcome is never altered here; this only makes secondary
    failures visible in the summary.
    """
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    lowered = text.lower()
    markers = ("failed", "timed out", "timed-out", "timeout", "missing", "unavailable")
    if not any(marker in lowered for marker in markers):
        return None
    # Keep the note compact: first few non-empty lines only.
    excerpt_lines = [line.strip() for line in text.splitlines() if line.strip()][:8]
    excerpt = "; ".join(excerpt_lines)[:800]
    return (
        f"- Secondary collection/teardown reported an issue (primary pytest "
        f"outcome unchanged): `{excerpt}`."
    )


def _evidence_bytes(*paths: str) -> int | None:
    total = 0
    seen_any = False
    for raw in paths:
        if not raw:
            continue
        try:
            total += Path(raw).stat().st_size
            seen_any = True
        except OSError:
            continue
    return total if seen_any else None


def build_evidence(args: argparse.Namespace) -> tuple[str, str | None]:
    """Parse JUnit, write slowest/durations files, return (markdown, error)."""
    import time

    hook_start = time.monotonic()
    junit: JUnitSummary | None = None
    error: str | None = None
    junit_path = Path(args.junit)
    junit_available = junit_path.is_file()
    if junit_available:
        try:
            junit = parse_junit(junit_path)
        except (OSError, ValueError, ET.ParseError) as exc:
            error = f"JUnit parse failed: {exc}"
            junit = None
            junit_available = False
    log_path = Path(args.log)
    log_available = log_path.is_file()
    log_bytes: int | None = None
    if log_available:
        try:
            log_bytes = log_path.stat().st_size
        except OSError:
            log_bytes = None
    slowest_available = False
    durations_available = False
    if junit is not None:
        try:
            write_slowest_report(junit, Path(args.slowest))
            slowest_available = True
        except OSError as exc:
            error = f"slowest-report write failed: {exc}"
        try:
            write_durations_snapshot(args.suite, junit, Path(args.durations_snapshot))
            durations_available = True
        except OSError as exc:
            note = f"durations-snapshot write failed: {exc}"
            error = f"{error}; {note}" if error else note
    import time as _time

    hook_seconds = _time.monotonic() - hook_start
    evidence_total = _evidence_bytes(
        args.log if log_available else "",
        args.junit if (junit_available and junit is not None) else "",
        args.slowest if slowest_available else "",
        args.durations_snapshot if durations_available else "",
    )
    if evidence_total is not None:
        overhead_note = (
            f"- Reporting overhead: hook `{hook_seconds:.2f}s`, "
            f"retained evidence `{evidence_total}` bytes "
            f"(log/JUnit/slowest/snapshot only; rich bundles stay failure-gated)."
        )
    else:
        overhead_note = (
            f"- Reporting overhead: hook `{hook_seconds:.2f}s`, "
            f"no retained evidence files (interrupted before pytest wrote them)."
        )
    secondary_note = _read_collection_note(getattr(args, "collection_status", ""))
    markdown = render_summary(
        suite=args.suite,
        shard=args.shard or "",
        revision=args.revision or "",
        run_id=args.run_id or "",
        attempt=args.attempt or "",
        selected=args.selected == "true",
        test_outcome=args.test_outcome or "unknown",
        junit=junit,
        junit_path=args.junit,
        junit_available=junit_available and junit is not None,
        log_path=args.log,
        log_available=log_available,
        log_bytes=log_bytes,
        slowest_path=args.slowest,
        slowest_available=slowest_available,
        durations_path=args.durations_snapshot,
        durations_available=durations_available,
        test_seconds=_read_test_seconds(args.test_seconds_file),
        error=error,
        effective_command=getattr(args, "effective_command", "") or "",
        pytest_timeout=getattr(args, "pytest_timeout", "") or "",
        step_budget=getattr(args, "step_budget", "") or "",
        secondary_note=secondary_note,
        overhead_note=overhead_note,
    )
    return markdown, error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--shard", default="")
    parser.add_argument("--junit", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--slowest", required=True)
    parser.add_argument("--durations-snapshot", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--revision", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--attempt", default="")
    parser.add_argument("--selected", default="true", choices=["true", "false"])
    parser.add_argument("--test-outcome", default="unknown")
    parser.add_argument("--test-seconds-file", default="")
    parser.add_argument("--effective-command", default="")
    parser.add_argument("--pytest-timeout", default="")
    parser.add_argument("--step-budget", default="")
    parser.add_argument("--collection-status", default="")
    args = parser.parse_args(argv)
    # A diagnostic/timing parsing problem must never hide an unsuccessful
    # job or alter test selection: always exit 0 and record the note.
    try:
        markdown, _ = build_evidence(args)
    except Exception as exc:  # noqa: BLE001 -- hook must not fail the job
        markdown = render_summary(
            suite=args.suite,
            shard=args.shard or "",
            revision=args.revision or "",
            run_id=args.run_id or "",
            attempt=args.attempt or "",
            selected=args.selected == "true",
            test_outcome=args.test_outcome or "unknown",
            junit=None,
            junit_path=args.junit,
            junit_available=False,
            log_path=args.log,
            log_available=False,
            log_bytes=None,
            slowest_path=args.slowest,
            slowest_available=False,
            durations_path=args.durations_snapshot,
            durations_available=False,
            test_seconds=None,
            error=f"evidence hook failed: {exc}",
        )
    try:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    except OSError as exc:
        print(f"warning: cannot append step summary: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
