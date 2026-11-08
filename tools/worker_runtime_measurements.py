"""Bounded, read-only evidence from disposable worker comparison journeys.

Only numeric resource observations, callback timing and diagnostic counts are
retained. Raw worker logs, environment maps, task representations and Docker
inspect are never saved.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FLEETS = ("workflow", "artifacts", "llm", "integrations")
SERVICES = tuple(f"temporal-worker-{fleet}" for fleet in FLEETS)
PHASES = (
    "startup",
    "cold_idle",
    "workload",
    "restart",
    "post_restart_workload",
    "warm_idle",
)
# Restart can finish between samples or temporarily have no running worker.
RESOURCE_PHASES = ("cold_idle", "workload", "post_restart_workload", "warm_idle")
MAX_CALLBACK_EVENTS = 100
INSPECT_FORMAT = (
    '{{$debug := false}}{{range .Config.Env}}{{$parts := split . "="}}'
    '{{if eq (index $parts 0) "PYTHONASYNCIODEBUG"}}{{if gt (len .) 19}}'
    "{{$debug = true}}{{end}}{{end}}{{end}}"
    '{"imageId":{{json .Image}},"debugEnabled":{{json $debug}},'
    '"memoryLimit":{{json .HostConfig.Memory}},'
    '"nanoCpus":{{json .HostConfig.NanoCpus}},'
    '"cpuQuota":{{json .HostConfig.CpuQuota}},'
    '"cpuPeriod":{{json .HostConfig.CpuPeriod}},'
    '"cpusetCpus":{{json .HostConfig.CpusetCpus}}}'
)


def comparison_override(model, *, combined, debug):
    """Keep the ordinary topology, or host its four fleets on the workflow service."""
    definitions = model["services"]
    specs = [definitions[service] for service in SERVICES]
    debug_env = {"PYTHONASYNCIODEBUG": "1" if debug else ""}
    if not combined:
        return {
            "services": {
                service: {"environment": debug_env.copy()} for service in SERVICES
            }
        }
    first = specs[0]
    environment, dependencies, volumes = {}, {}, {}
    for spec in specs:
        for key in ("image", "networks", "entrypoint", "stop_grace_period", "env_file"):
            if spec.get(key) != first.get(key):
                raise ValueError(f"worker comparison cannot merge conflicting {key}")
        for key, value in spec.get("environment", {}).items():
            if key in {"TEMPORAL_WORKER_FLEET", "TEMPORAL_WORKER_FLEETS"}:
                continue
            if key in environment and environment[key] != value:
                raise ValueError(
                    "worker comparison cannot merge conflicting environment values"
                )
            environment[key] = value
        for key, value in spec.get("depends_on", {}).items():
            if key in dependencies and dependencies[key] != value:
                raise ValueError(
                    "worker comparison cannot merge conflicting startup dependencies"
                )
            dependencies[key] = value
        for volume in spec.get("volumes", []):
            target = volume["target"]
            if target in volumes and volumes[target] != volume:
                raise ValueError(
                    "worker comparison cannot merge conflicting volume targets"
                )
            volumes[target] = copy.deepcopy(volume)
    environment.update(debug_env)
    environment.update(
        TEMPORAL_WORKER_FLEET="artifacts", TEMPORAL_WORKER_FLEETS=json.dumps(FLEETS)
    )
    result = {
        service: {"profiles": ["separate-worker-comparison"]}
        for service in SERVICES[1:]
    }
    result[SERVICES[0]] = {
        "environment": environment,
        "depends_on": dependencies,
        "volumes": list(volumes.values()),
        "labels": {"moonmind.journal-consumer": "true"},
    }
    return {"services": result}


def parse_worker_log(text, *, phase_started_at=None):
    # Docker's timestamp describes emission, not an exact callback end time.
    # Retain no task representation, name, arguments or arbitrary source paths.
    callbacks, events = [], []
    phase_start = (
        datetime.fromisoformat(phase_started_at).timestamp()
        if phase_started_at
        else None
    )
    for line in text.splitlines():
        callback = re.search(r"Executing .+ took (\d+(?:\.\d+)?) seconds", line)
        if not callback:
            continue
        duration = float(callback[1])
        callbacks.append(duration)
        if len(events) >= MAX_CALLBACK_EVENTS:
            continue
        timestamp = re.match(
            r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z)\s", line
        )
        emitted_at = None
        if timestamp:
            try:
                emitted_at = datetime.fromisoformat(
                    timestamp[1].replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                pass
        events.append(
            {
                "emittedAtUnixSeconds": emitted_at,
                "durationSeconds": duration,
                "estimatedCrossesPhaseStart": (
                    emitted_at - duration < phase_start
                    if emitted_at is not None and phase_start is not None
                    else None
                ),
            }
        )
    lag = [
        float(value)
        for value in re.findall(
            r"Temporal worker event loop lag: (\d+(?:\.\d+)?) seconds", text
        )
    ]
    lines = text.splitlines()
    return {
        "asyncioSlowCallbacks": {
            "count": len(callbacks),
            "maxSeconds": max(callbacks, default=None),
            "aboveHalfSecond": sum(value >= 0.5 for value in callbacks),
            "events": events,
            "omittedEvents": len(callbacks) - len(events),
        },
        "workflowActivationDeadlocks": sum(
            "TMPRL1101" in line or "Potential deadlock detected" in line
            for line in lines
        ),
        "eventLoopLagWarnings": sum("event loop lag" in line.lower() for line in lines),
        "eventLoopLag": {
            "count": len(lag),
            "maxSeconds": max(lag, default=None),
            "aboveHalfSecond": sum(value >= 0.5 for value in lag),
        },
        "retainedLogLines": len(lines),
        "possiblyTruncated": len(lines) >= 10000,
    }


def summarize_samples(samples):
    result = {}
    for sample in samples:
        phase = sample["phase"]
        group = result.setdefault(phase, {"samples": 0, "unavailableSamples": 0})
        group["samples"] += 1
        if "unavailable" in sample:
            group["unavailableSamples"] += 1
    for phase, group in result.items():
        for key in ("totalMemoryMiB", "workerMemoryMiB", "runningContainers"):
            values = [
                sample[key]
                for sample in samples
                if sample["phase"] == phase and key in sample
            ]
            if values:
                group[key] = {
                    "min": min(values),
                    "max": max(values),
                    "mean": round(statistics.mean(values), 2),
                }
    return result


def validate_project(project):
    if not re.fullmatch(r"moonmind-test(?:-[a-z0-9][a-z0-9_-]*)?", project):
        raise ValueError(
            "worker measurement requires a disposable moonmind-test project"
        )
    return project


def phase_boundaries(path):
    """Read actual owner-recorded transitions, including phases between samples."""
    return json.loads(path.read_text()) if path.exists() else {}


def mark_phase(path, phase, *, timestamp=None):
    if phase not in (*PHASES, "stop"):
        raise ValueError("unknown measurement phase")
    boundaries = {} if phase == "startup" else phase_boundaries(path)
    boundaries[phase] = timestamp or datetime.now(timezone.utc).isoformat()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(boundaries) + "\n")
    temporary.replace(path)


def compare_reports(baseline, candidate):
    for key in ("host", "revision", "workload"):
        if (
            not baseline.get(key)
            or baseline[key] != candidate.get(key)
            or "unavailable" in baseline[key]
        ):
            raise ValueError(f"worker comparison requires matching observed {key}")
    limits = (
        "imageId",
        "debugEnabled",
        "memoryLimit",
        "nanoCpus",
        "cpuQuota",
        "cpuPeriod",
        "cpusetCpus",
    )
    conditions = []
    for report, topology, expected in (
        (baseline, "separate", set(SERVICES)),
        (candidate, "combined", set(SERVICES[:1])),
    ):
        workers = list(report.get("workers", {}).values())
        if (
            report.get("topology") != topology
            or {worker["service"] for worker in workers} != expected
        ):
            raise ValueError("worker comparison is missing an expected topology")
        conditions.append({tuple(worker[key] for key in limits) for worker in workers})
        for phase in RESOURCE_PHASES:
            summary = report.get("summary", {}).get(phase, {})
            if not all(
                key in summary
                for key in ("workerMemoryMiB", "totalMemoryMiB", "runningContainers")
            ):
                raise ValueError(f"worker comparison has no observed {phase} resources")
    if len(conditions[0]) != 1 or conditions[0] != conditions[1]:
        raise ValueError(
            "worker comparison requires matching image, debug and container resource limits"
        )
    diagnostics = {}
    for name, report in (("baseline", baseline), ("candidate", candidate)):
        logs = report.get("logs", {})
        diagnostics[name] = all(
            cid in logs
            and all(
                phase in logs[cid].get("phases", {})
                and "unavailable" not in logs[cid]["phases"][phase]
                and not logs[cid]["phases"][phase].get("possiblyTruncated", True)
                for phase in PHASES[1:]
            )
            for cid in report["workers"]
        )
    before, after = baseline["summary"]["warm_idle"], candidate["summary"]["warm_idle"]
    return {
        "revision": baseline["revision"],
        "host": baseline["host"],
        "warmIdleWorkerMemorySavedMiB": round(
            before["workerMemoryMiB"]["mean"] - after["workerMemoryMiB"]["mean"], 2
        ),
        "warmIdleTotalMemorySavedMiB": round(
            before["totalMemoryMiB"]["mean"] - after["totalMemoryMiB"]["mean"], 2
        ),
        "baselineRunningContainers": before["runningContainers"]["max"],
        "candidateRunningContainers": after["runningContainers"]["max"],
        "baseline": baseline["summary"],
        "candidate": candidate["summary"],
        "diagnosticsComplete": diagnostics,
        "normalUseObservation": "Unverified: matched disposable journeys cannot establish one-to-two-day normal-use safety",
        "diagnosticEvidence": "Separate phase-specific slow callbacks, loop lag, and workflow activation deadlocks are recorded in each input report",
    }


def _docker(*args, timeout=20):
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        # Stderr can contain private configuration; retain only the operation/status.
        raise RuntimeError(f"docker {args[0]} exited {result.returncode}")
    return result.stdout + result.stderr if args[0] == "logs" else result.stdout


def _memory_mib(value):
    match = re.fullmatch(
        r"\s*([\d.]+)\s*(B|kB|MB|GB|KiB|MiB|GiB)\s*", value.split("/")[0]
    )
    if not match:
        raise ValueError("unsupported Docker memory observation")
    units = {
        "B": 1,
        "kB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
    }
    return round(float(match[1]) * units[match[2]] / 1024**2, 2)


def observe_sample(project, phase, *, combined, metadata, runner=_docker):
    validate_project(project)
    listing = runner(
        "ps",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--format",
        '{{.ID}}\t{{.Label "com.docker.compose.service"}}',
    )
    containers = dict(
        line.split("\t", 1) for line in listing.splitlines() if "\t" in line
    )
    if not containers or any(
        not re.fullmatch(r"[a-f0-9]{12,64}", cid) for cid in containers
    ):
        raise ValueError("no valid running project containers")
    expected = set(SERVICES[:1] if combined else SERVICES)
    if not expected <= set(containers.values()):
        raise ValueError("not every measured worker is running")
    observed_workers = [
        service for service in containers.values() if service in SERVICES
    ]
    if set(observed_workers) != expected or len(observed_workers) != len(expected):
        raise ValueError(
            "observed worker topology does not match the requested comparison"
        )
    stats = [
        json.loads(line)
        for line in runner(
            "stats", "--no-stream", "--format", "{{json .}}", *containers
        ).splitlines()
        if line
    ]
    rows, observed_ids = [], set()
    for row in stats:
        matches = [
            cid
            for cid in containers
            if cid.startswith(row["ID"]) or row["ID"].startswith(cid)
        ]
        if len(matches) != 1 or matches[0] in observed_ids:
            raise ValueError("Docker stats inventory changed during observation")
        cid = matches[0]
        observed_ids.add(cid)
        service = containers[cid]
        rows.append({"service": service, "memoryMiB": _memory_mib(row["MemUsage"])})
    if observed_ids != set(containers):
        raise ValueError("Docker stats inventory is incomplete")
    for cid, service in containers.items():
        if service in expected and cid not in metadata:
            observed = json.loads(runner("inspect", "--format", INSPECT_FORMAT, cid))
            metadata[cid] = {
                "service": service,
                **{
                    key: observed[key]
                    for key in (
                        "imageId",
                        "debugEnabled",
                        "memoryLimit",
                        "nanoCpus",
                        "cpuQuota",
                        "cpuPeriod",
                        "cpusetCpus",
                    )
                },
            }
    return {
        "phase": phase,
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "runningContainers": len(rows),
        "services": rows,
        "totalMemoryMiB": round(sum(row["memoryMiB"] for row in rows), 2),
        "workerMemoryMiB": round(
            sum(row["memoryMiB"] for row in rows if row["service"] in expected), 2
        ),
    }


def record(args):
    validate_project(args.project)
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    start = time.monotonic()
    samples, metadata, boundaries = [], {}, {}
    report = {
        "project": args.project,
        "topology": args.topology,
        "workload": "default first-run journey with controlled provider, production artifact roundtrip and empty memory evaluation",
        "revision": args.revision,
        "normalUseObservation": "Unverified: disposable journey, not a one-to-two-day normal-use observation",
    }

    def save():
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(args.output)

    try:
        report["host"] = json.loads(
            _docker(
                "info",
                "--format",
                '{"cpus":{{json .NCPU}},"memoryBytes":{{json .MemTotal}}}',
            )
        )
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as error:
        report["host"] = {"unavailable": type(error).__name__}
    while not stopped and time.monotonic() - start < args.duration:
        boundaries = phase_boundaries(args.phase_file)
        phase = next(reversed(boundaries), "startup")
        if phase == "stop":
            break
        if phase not in PHASES:
            phase = "startup"
        try:
            sample = observe_sample(
                args.project,
                phase,
                combined=args.topology == "combined",
                metadata=metadata,
            )
            if next(reversed(phase_boundaries(args.phase_file)), "startup") != phase:
                raise ValueError("measurement phase changed during Docker observation")
        except (
            OSError,
            subprocess.SubprocessError,
            ValueError,
            RuntimeError,
            KeyError,
        ) as error:
            sample = {"phase": phase, "unavailable": type(error).__name__}
        samples.append(sample)
        # Save each observation durably so killed/canceled runs still have evidence.
        report.update(
            samples=samples,
            workers=metadata,
            summary=summarize_samples(samples),
            phaseStartedAt=boundaries,
        )
        save()
        deadline = time.monotonic() + args.interval
        while not stopped and time.monotonic() < deadline:
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
    report["durationSeconds"] = round(time.monotonic() - start, 2)
    boundaries = phase_boundaries(args.phase_file)
    report["phaseStartedAt"] = boundaries
    report["logs"] = {}
    log_deadline = time.monotonic() + 45
    for cid, worker in metadata.items():
        evidence = {}
        phases = list(boundaries)
        for index, phase in enumerate(phases):
            if phase == "stop":
                continue
            until = (
                boundaries[phases[index + 1]]
                if index + 1 < len(phases)
                else datetime.now(timezone.utc).isoformat()
            )
            try:
                remaining = log_deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("worker diagnostic collection budget exhausted")
                text = _docker(
                    "logs",
                    "--timestamps",
                    "--tail",
                    "10000",
                    "--since",
                    boundaries[phase],
                    "--until",
                    until,
                    cid,
                    timeout=min(20, remaining),
                )
                evidence[phase] = parse_worker_log(
                    text, phase_started_at=boundaries[phase]
                )
            except (
                OSError,
                subprocess.SubprocessError,
                ValueError,
                RuntimeError,
            ) as error:
                evidence[phase] = {"unavailable": type(error).__name__}
        report["logs"][cid] = {"service": worker["service"], "phases": evidence}
    save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    override = sub.add_parser("override")
    override.add_argument("--topology", choices=("separate", "combined"), required=True)
    override.add_argument("--debug", action="store_true")
    observe = sub.add_parser("record")
    observe.add_argument("--project", required=True)
    observe.add_argument("--topology", choices=("separate", "combined"), required=True)
    observe.add_argument("--revision", required=True)
    observe.add_argument("--phase-file", type=Path, required=True)
    observe.add_argument("--output", type=Path, required=True)
    observe.add_argument("--interval", type=float, default=5)
    observe.add_argument("--duration", type=float, default=1200)
    compare = sub.add_parser("compare")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    mark = sub.add_parser("mark")
    mark.add_argument("--phase-file", type=Path, required=True)
    mark.add_argument("--phase", choices=(*PHASES, "stop"), required=True)
    args = parser.parse_args()
    if args.command == "override":
        json.dump(
            comparison_override(
                json.load(sys.stdin),
                combined=args.topology == "combined",
                debug=args.debug,
            ),
            sys.stdout,
        )
    elif args.command == "mark":
        mark_phase(args.phase_file, args.phase)
    elif args.command == "compare":
        json.dump(
            compare_reports(
                json.loads(args.baseline.read_text()),
                json.loads(args.candidate.read_text()),
            ),
            sys.stdout,
            indent=2,
        )
    else:
        if args.interval <= 0 or not 0 < args.duration <= 3600:
            parser.error(
                "measurement interval must be positive and duration at most one hour"
            )
        record(args)


if __name__ == "__main__":
    main()
