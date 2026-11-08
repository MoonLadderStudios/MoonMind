"""Behavioral tests for matched disposable worker resource evidence."""

from __future__ import annotations

import copy
import json
import subprocess

import pytest

from tools import worker_runtime_measurements as measurement


def model():
    return {
        "services": {
            f"temporal-worker-{fleet}": {
                "image": "example@sha256:abc",
                "environment": {
                    "TEMPORAL_WORKER_FLEET": fleet,
                    "SHARED": "unchanged",
                    f"QUEUE_{fleet}": fleet,
                },
                "volumes": [
                    {"type": "volume", "source": "secrets", "target": "/secrets"}
                ],
                "depends_on": {"temporal": {"condition": "service_started"}},
                "networks": {"control": None},
                "entrypoint": ["sh", "start-worker.sh"],
                "stop_grace_period": "6m",
            }
            for fleet in ("workflow", "artifacts", "llm", "integrations")
        }
    }


def test_combined_override_keeps_workflow_environment_and_unions_requirements():
    config = model()
    config["services"]["temporal-worker-workflow"]["volumes"].append(
        {"type": "volume", "source": "workspaces", "target": "/work"}
    )
    config["services"]["temporal-worker-artifacts"]["depends_on"]["minio"] = {
        "condition": "service_started"
    }
    before = copy.deepcopy(config)
    result = measurement.comparison_override(config, combined=True, debug=True)
    core = result["services"]["temporal-worker-workflow"]
    assert (
        core["environment"]["TEMPORAL_WORKER_FLEETS"]
        == '["workflow", "artifacts", "llm", "integrations"]'
    )
    assert core["environment"]["TEMPORAL_WORKER_FLEET"] == "artifacts"
    assert core["environment"]["SHARED"] == "unchanged"
    assert core["environment"]["PYTHONASYNCIODEBUG"] == "1"
    assert {key for key in core["environment"] if key.startswith("QUEUE_")} == {
        "QUEUE_workflow",
        "QUEUE_artifacts",
        "QUEUE_llm",
        "QUEUE_integrations",
    }
    assert {volume["target"] for volume in core["volumes"]} == {"/work", "/secrets"}
    assert set(core["depends_on"]) == {"temporal", "minio"}
    assert core["labels"]["moonmind.journal-consumer"] == "true"
    assert all(
        result["services"][f"temporal-worker-{fleet}"]["profiles"]
        == ["separate-worker-comparison"]
        for fleet in ("artifacts", "llm", "integrations")
    )
    assert config == before


@pytest.mark.parametrize(
    "field",
    ["environment", "volumes", "networks", "image", "entrypoint", "stop_grace_period"],
)
def test_override_refuses_to_silently_change_incompatible_fleet_configuration(field):
    config = model()
    service = config["services"]["temporal-worker-artifacts"]
    if field == "environment":
        service[field]["SHARED"] = "different-secret-must-not-appear-in-errors"
    elif field == "volumes":
        service[field][0]["source"] = "different"
    else:
        service[field] = "different"
    with pytest.raises(ValueError) as error:
        measurement.comparison_override(config, combined=True, debug=True)
    assert "different-secret" not in str(error.value)


def test_separate_debug_override_does_not_change_fleet_or_queue_configuration():
    result = measurement.comparison_override(model(), combined=False, debug=True)
    assert result == {
        "services": {
            f"temporal-worker-{fleet}": {"environment": {"PYTHONASYNCIODEBUG": "1"}}
            for fleet in ("workflow", "artifacts", "llm", "integrations")
        }
    }


def test_log_evidence_separates_loop_callbacks_and_activation_deadlocks_without_task_content():
    text = "\n".join(
        [
            "2026-11-08T08:00:00Z Executing <Task customer-private-secret> took 0.315 seconds",
            "2026-11-08T08:00:01Z Executing <Handle another-secret> took 1.500 seconds",
            "2026-11-08T08:00:02Z TMPRL1101 Potential deadlock detected: secret-workflow",
            "2026-11-08T08:00:03Z Temporal worker event loop lag: 0.271000 seconds",
        ]
    )
    result = measurement.parse_worker_log(text)
    assert {
        key: result["asyncioSlowCallbacks"][key]
        for key in ("count", "maxSeconds", "aboveHalfSecond")
    } == {
        "count": 2,
        "maxSeconds": 1.5,
        "aboveHalfSecond": 1,
    }
    assert result["workflowActivationDeadlocks"] == 1
    assert result["eventLoopLagWarnings"] == 1
    assert result["eventLoopLag"] == {
        "count": 1,
        "maxSeconds": 0.271,
        "aboveHalfSecond": 0,
    }
    assert "secret" not in str(result)


def test_summary_reports_cold_and_warmed_work_separately_and_never_treats_missing_as_zero():
    samples = [
        {
            "phase": "startup",
            "totalMemoryMiB": 1000,
            "workerMemoryMiB": 400,
            "persistentContainers": 14,
        },
        {
            "phase": "workload",
            "totalMemoryMiB": 2000,
            "workerMemoryMiB": 600,
            "persistentContainers": 14,
        },
        {
            "phase": "workload",
            "totalMemoryMiB": 2200,
            "workerMemoryMiB": 800,
            "persistentContainers": 14,
        },
        {"phase": "warm_idle", "unavailable": "missing workers"},
    ]
    result = measurement.summarize_samples(samples)
    assert result["startup"]["workerMemoryMiB"]["max"] == 400
    assert result["workload"]["workerMemoryMiB"] == {
        "min": 600,
        "max": 800,
        "mean": 700,
    }
    assert result["warm_idle"]["unavailableSamples"] == 1
    assert "workerMemoryMiB" not in result["warm_idle"]


@pytest.mark.parametrize(
    "project", ["moonmind", "production", "moonmind-test-", "moonmind-test-../prod"]
)
def test_measurement_rejects_non_disposable_projects(project):
    with pytest.raises(ValueError):
        measurement.validate_project(project)


def test_docker_logs_include_stderr_diagnostics_but_other_commands_parse_only_stdout(
    monkeypatch,
):
    monkeypatch.setattr(
        measurement.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, stdout="{}\n", stderr="Executing private-task took 0.600 seconds\n"
        ),
    )
    assert (
        measurement.parse_worker_log(measurement._docker("logs", "container"))[
            "asyncioSlowCallbacks"
        ]["count"]
        == 1
    )
    assert measurement._docker("inspect", "container") == "{}\n"


def test_sample_records_exact_worker_images_limits_and_total_memory_without_unrelated_env():
    metadata = {}
    calls = []
    ids = {
        "a" * 12: "api",
        **{
            str(index) * 12: service
            for index, service in enumerate(measurement.SERVICES, start=1)
        },
    }

    def run(*args):
        calls.append(args)
        if args[0] == "ps":
            return "\n".join(f"{cid}\t{service}" for cid, service in ids.items())
        if args[0] == "stats":
            return "\n".join(
                json.dumps({"ID": cid, "MemUsage": "100MiB / 1GiB"}) for cid in ids
            )
        return json.dumps(
            {
                "imageId": "sha256:" + "a" * 64,
                "debugEnabled": True,
                "memoryLimit": 0,
                "nanoCpus": 0,
                "cpuQuota": 0,
                "cpuPeriod": 0,
                "cpusetCpus": "",
                "unrequestedSecret": "do-not-retain",
            }
        )

    result = measurement.observe_sample(
        "moonmind-test-evidence",
        "workload",
        combined=False,
        metadata=metadata,
        runner=run,
    )
    assert result["workerMemoryMiB"] == 400
    assert result["totalMemoryMiB"] == 500
    assert result["runningContainers"] == 5
    assert len(metadata) == 4
    assert all(item["debugEnabled"] for item in metadata.values())
    assert "do-not-retain" not in str(metadata)
    assert calls[0][2] == "label=com.docker.compose.project=moonmind-test-evidence"
    assert not any(args[0] not in {"ps", "stats", "inspect"} for args in calls)


def test_missing_measured_worker_does_not_turn_into_a_lower_memory_result():
    with pytest.raises(ValueError, match="not every measured worker"):
        measurement.observe_sample(
            "moonmind-test-evidence",
            "warm_idle",
            combined=False,
            metadata={},
            runner=lambda *args: "a" * 12 + "\tapi",
        )


@pytest.mark.parametrize(
    "extra", ["temporal-worker-artifacts", "temporal-worker-workflow"]
)
def test_combined_sample_refuses_extra_or_duplicate_worker_containers(extra):
    ids = {"a" * 12: "temporal-worker-workflow", "b" * 12: extra}

    def run(*args):
        if args[0] == "ps":
            return "\n".join(f"{cid}\t{service}" for cid, service in ids.items())
        if args[0] == "stats":
            return "\n".join(
                json.dumps({"ID": cid, "MemUsage": "100MiB / 1GiB"}) for cid in ids
            )
        return json.dumps(
            {
                "imageId": "sha256:a",
                "debugEnabled": True,
                "memoryLimit": 0,
                "nanoCpus": 0,
                "cpuQuota": 0,
                "cpuPeriod": 0,
                "cpusetCpus": "",
            }
        )

    with pytest.raises(ValueError, match="topology"):
        measurement.observe_sample(
            "moonmind-test-evidence", "workload", combined=True, metadata={}, runner=run
        )


def test_sample_refuses_partial_or_duplicate_total_resource_inventory():
    def run(*args):
        if args[0] == "ps":
            return "a" * 12 + "\ttemporal-worker-workflow\n" + "b" * 12 + "\tapi"
        if args[0] == "stats":
            return json.dumps({"ID": "a" * 12, "MemUsage": "100MiB / 1GiB"})
        return json.dumps(
            {
                "imageId": "sha256:a",
                "debugEnabled": True,
                "memoryLimit": 0,
                "nanoCpus": 0,
                "cpuQuota": 0,
                "cpuPeriod": 0,
                "cpusetCpus": "",
            }
        )

    with pytest.raises(ValueError, match="inventory"):
        measurement.observe_sample(
            "moonmind-test-evidence", "workload", combined=True, metadata={}, runner=run
        )


def test_phase_boundaries_preserve_actual_transition_times_between_samples(tmp_path):
    phases = tmp_path / "phases.json"
    measurement.mark_phase(phases, "startup", timestamp="2026-11-08T08:00:00+00:00")
    measurement.mark_phase(phases, "workload", timestamp="2026-11-08T08:00:10+00:00")
    measurement.mark_phase(phases, "warm_idle", timestamp="2026-11-08T08:00:12+00:00")
    # The next sample runs much later; the workload boundary is not overwritten.
    assert measurement.phase_boundaries(phases) == {
        "startup": "2026-11-08T08:00:00+00:00",
        "workload": "2026-11-08T08:00:10+00:00",
        "warm_idle": "2026-11-08T08:00:12+00:00",
    }


def report(topology):
    separate = topology == "separate"
    return {
        "topology": topology,
        "host": {"cpus": 4, "memoryBytes": 16000},
        "revision": "a" * 40,
        "workload": "same controlled workload",
        "workers": {
            str(index): {
                "service": service,
                "imageId": "sha256:a",
                "debugEnabled": True,
                "memoryLimit": 0,
                "nanoCpus": 0,
                "cpuQuota": 0,
                "cpuPeriod": 0,
                "cpusetCpus": "",
            }
            for index, service in enumerate(
                measurement.SERVICES if separate else measurement.SERVICES[:1]
            )
        },
        "summary": {
            phase: {
                "samples": 3,
                "unavailableSamples": 0,
                "workerMemoryMiB": {"mean": 1000 if separate else 400},
                "totalMemoryMiB": {"mean": 2000 if separate else 1400},
                "runningContainers": {
                    "min": 14 if separate else 11,
                    "max": 14 if separate else 11,
                },
            }
            for phase in measurement.PHASES[1:]
        },
    }


def test_comparison_requires_matched_evidence_and_records_measured_savings():
    result = measurement.compare_reports(report("separate"), report("combined"))
    assert result["warmIdleWorkerMemorySavedMiB"] == 600
    assert result["warmIdleTotalMemorySavedMiB"] == 600
    assert result["candidateRunningContainers"] == 11
    assert result["normalUseObservation"].startswith("Unverified")
    assert result["diagnosticsComplete"] == {"baseline": False, "candidate": False}


@pytest.mark.parametrize(
    "mismatch",
    [
        "host",
        "revision",
        "workload",
        "imageId",
        "debugEnabled",
        "memoryLimit",
        "missingSamples",
    ],
)
def test_comparison_refuses_incomplete_or_unmatched_conditions(mismatch):
    baseline, candidate = report("separate"), report("combined")
    if mismatch in {"host", "revision", "workload"}:
        candidate[mismatch] = "different"
    elif mismatch == "missingSamples":
        candidate["summary"].pop("warm_idle")
    else:
        candidate["workers"]["0"][mismatch] = "different"
    with pytest.raises(ValueError):
        measurement.compare_reports(baseline, candidate)


def test_callback_events_keep_only_bounded_numeric_evidence():
    prefix = "2026-11-08T08:00:00.250000123Z "
    text = "\n".join(
        prefix + f"Executing <Task private-secret-{index}> took 0.600 seconds"
        for index in range(103)
    )
    result = measurement.parse_worker_log(
        text, phase_started_at="2026-11-08T08:00:00+00:00"
    )["asyncioSlowCallbacks"]
    assert result["count"] == 103
    assert result["aboveHalfSecond"] == 103
    assert len(result["events"]) == 100
    assert result["omittedEvents"] == 3
    assert result["events"][0] == {
        "emittedAtUnixSeconds": 1794124800.25,
        "durationSeconds": 0.6,
        "estimatedCrossesPhaseStart": True,
    }
    assert "private" not in str(result)


def test_callback_event_boundary_and_missing_timestamp_are_explicit():
    result = measurement.parse_worker_log(
        "2026-11-08T08:00:01Z Executing <Handle private> took 0.200 seconds\n"
        "Executing <Task private> took 0.700 seconds\n"
        "2026-99-99T08:00:01Z Executing <Task private> took 0.800 seconds",
        phase_started_at="2026-11-08T08:00:00+00:00",
    )["asyncioSlowCallbacks"]
    assert result["events"][0]["estimatedCrossesPhaseStart"] is False
    for event in result["events"][1:]:
        assert event["emittedAtUnixSeconds"] is None
        assert event["estimatedCrossesPhaseStart"] is None
    assert result["omittedEvents"] == 0
    assert "private" not in str(result)


def test_comparison_allows_restart_without_resource_samples():
    baseline, candidate = report("separate"), report("combined")
    assert "restart" in measurement.PHASES
    assert "post_restart_workload" in measurement.PHASES
    for item in (baseline, candidate):
        item["summary"]["restart"] = {"samples": 1, "unavailableSamples": 1}
    measurement.compare_reports(baseline, candidate)
    candidate["summary"].pop("post_restart_workload")
    with pytest.raises(ValueError, match="post_restart_workload"):
        measurement.compare_reports(baseline, candidate)


@pytest.mark.parametrize("pre_restart", [None, {"samples": 1, "unavailableSamples": 1}])
def test_short_diagnostic_windows_report_missing_resources_without_inventing_samples(
    pre_restart,
):
    baseline, candidate = report("separate"), report("combined")
    baseline["summary"].pop("workload")
    if pre_restart is not None:
        baseline["summary"]["workload"] = pre_restart
    candidate["summary"].pop("restart")
    before = copy.deepcopy((baseline, candidate))
    result = measurement.compare_reports(baseline, candidate)
    assert result["unobservedResourcePhases"] == {
        "baseline": ["startup", "workload"],
        "candidate": ["startup", "restart"],
    }
    assert result["baseline"] == baseline["summary"]
    assert result["candidate"] == candidate["summary"]
    assert (baseline, candidate) == before


@pytest.mark.parametrize("phase", ["cold_idle", "post_restart_workload", "warm_idle"])
def test_comparison_requires_observed_idle_and_post_restart_workload_resources(phase):
    baseline, candidate = report("separate"), report("combined")
    candidate["summary"][phase] = {"samples": 1, "unavailableSamples": 1}
    with pytest.raises(ValueError, match=phase):
        measurement.compare_reports(baseline, candidate)
