#!/usr/bin/env python3
"""Run credentialed Omnigent conformance journeys for #3368.

The runner owns only an isolated Compose project.  In particular, it never
removes volumes: the enrolled Codex OAuth volume is operator-owned evidence,
not disposable test state.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.conformance import (  # noqa: E402
    CaseResult,
    ConformanceContractError,
    assert_secret_free,
    build_report,
    load_profile,
    require_pinned_images,
)

PROFILE = REPO_ROOT / "tests/fixtures/omnigent/conformance-v1.json"
PROJECT = "moonmind-test-omnigent-live"
PROVIDER_TEST = "tests/provider/omnigent/test_omnigent_smoke.py"
LIVE_CASES = {
    "stock": {"stock-images.proxy"},
    "static": {"compose.static-codex-oauth"},
    "ondemand": {"ondemand.codex-oauth", "cleanup.lease-owned-only"},
    "failures": {"failures.lifecycle-and-redaction"},
}
STOCK_ROUTES = (
    "agents", "hosts", "session.create", "session.get", "event.post",
    "events.stream", "elicitation.resolve", "interrupt", "stop",
    "changed-files", "workspace.files", "workspace.content", "workspace.diff",
    "session.files", "session.content", "terminal.snapshot",
)
FAILURE_CASES = (
    "invalid_oauth", "profile_lease_busy", "host_image_start_failure",
    "registration_timeout", "bridge_server_auth_failure", "server_unavailable",
    "ambiguous_first_message_reconciliation", "active_session_disconnect",
    "resource_route_unavailable", "cleanup_failure",
)
ONDEMAND_ACTIONS = (
    "lease_acquired", "host_launched", "preflight_ready", "session_bound",
    "executed", "resources_harvested", "partial_start_retry", "janitor_recovery",
    "host_removed", "workflow_detail_reloaded", "lease_released",
)
SCENARIOS = {
    "stock": f"{PROVIDER_TEST}::test_live_stock_proxy_compatibility_profile",
    "static": f"{PROVIDER_TEST}::test_live_static_workflow_detail_restart_replay",
    "ondemand": f"{PROVIDER_TEST}::test_live_ondemand_oauth_lifecycle_and_cleanup",
    "failures": f"{PROVIDER_TEST}::test_live_failure_matrix_and_durable_evidence",
}
EVIDENCE_ENV = {
    "logs": "MOONMIND_OMNIGENT_LOG_EVIDENCE",
    "temporalHistory": "MOONMIND_OMNIGENT_TEMPORAL_HISTORY_EVIDENCE",
    "screenshots": "MOONMIND_OMNIGENT_SCREENSHOT_EVIDENCE",
    "archives": "MOONMIND_OMNIGENT_ARCHIVE_EVIDENCE",
}
SCENARIO_EVIDENCE_ENV = {
    "stock": "MOONMIND_OMNIGENT_STOCK_EVIDENCE",
    "static": "MOONMIND_OMNIGENT_STATIC_EVIDENCE",
    "ondemand": "MOONMIND_OMNIGENT_ONDEMAND_EVIDENCE",
    "failures": "MOONMIND_OMNIGENT_FAILURE_EVIDENCE",
}


class LiveRunner:
    def __init__(self, *, output_dir: Path, env: dict[str, str]) -> None:
        self.output_dir = output_dir
        self.env = env
        self.logs: list[Path] = []

    def run(self, name: str, command: Sequence[str]) -> Path:
        log_path = self.output_dir / f"{name}.log"
        with log_path.open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                command,
                cwd=REPO_ROOT,
                env=self.env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        self.logs.append(log_path)
        if result.returncode:
            raise RuntimeError(f"{name} failed; see {log_path}")
        return log_path

    def action(self, scenario: str, action: str, **inputs: object) -> dict[str, object]:
        """Execute an operator-supplied live adapter and parse its observed result.

        The adapter is a portable executable boundary, not an evidence file: each
        invocation must perform the named action and return one JSON object on
        stdout. This keeps credentials and deployment-specific API mechanics out
        of this repository while making the runner own ordering and conclusions.
        """
        executable = self.env.get("MOONMIND_OMNIGENT_ACTION_COMMAND", "").strip()
        if not executable:
            raise ConformanceContractError("MOONMIND_OMNIGENT_ACTION_COMMAND is required")
        command = [executable, scenario, action, json.dumps(inputs, separators=(",", ":"))]
        result = subprocess.run(
            command, cwd=REPO_ROOT, env=self.env, capture_output=True,
            text=True, check=False,
        )
        log_path = self.output_dir / f"{scenario}-{action.replace('.', '-')}.log"
        log_path.write_text(result.stderr, encoding="utf-8")
        self.logs.append(log_path)
        if result.returncode:
            raise RuntimeError(f"{scenario}/{action} failed; see {log_path}")
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ConformanceContractError(f"{scenario}/{action} returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise ConformanceContractError(f"{scenario}/{action} did not report observed success")
        return payload

    def write_evidence(self, mode: str, payload: dict[str, object]) -> Path:
        path = self.output_dir / f"{mode}-evidence.json"
        payload = {"schemaVersion": "moonmind.omnigent.live-evidence/v1", **payload}
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self.env[SCENARIO_EVIDENCE_ENV[mode]] = str(path)
        return path

    def stock(self, images: dict[str, str]) -> None:
        observed = {route: self.action("stock", route) for route in STOCK_ROUTES}
        inventory = self.action("stock", "inventory")
        self.write_evidence("stock", {
            "images": images, "hostSource": "published-stock-image",
            "moonmindHostPatch": False,
            "protocolVersion": inventory.get("protocolVersion"),
            "hostArchitecture": inventory.get("hostArchitecture"),
            "advertisedAgents": inventory.get("agents"),
            "advertisedCapabilities": inventory.get("capabilities"),
            "assertions": {name: result["ok"] is True for name, result in observed.items()},
        })
        self.scenario("stock")

    @staticmethod
    def compose(*args: str) -> list[str]:
        return [
            "docker", "compose", "--project-name", PROJECT,
            "--profile", "omnigent-host-codex", *args,
        ]

    def scenario(self, mode: str, *, phase: str | None = None) -> None:
        """Run exactly one strict provider scenario and reject skips/no collection."""
        self.env["MOONMIND_OMNIGENT_LIVE_MODE"] = mode
        self.env["MOONMIND_OMNIGENT_STRICT_LIVE"] = "1"
        if phase is not None:
            self.env["MOONMIND_OMNIGENT_STATIC_PHASE"] = phase
        evidence_name = f"{mode}-{phase}" if phase else mode
        junit = self.output_dir / f"{evidence_name}-junit.xml"
        self.run(
            f"{evidence_name}-journey",
            [sys.executable, "-m", "pytest", SCENARIOS[mode], "-q", "-s", f"--junitxml={junit}"],
        )
        if not junit.is_file():
            raise RuntimeError(f"{mode} did not produce pytest outcome evidence")
        root = ET.parse(junit).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        totals = {key: sum(int(s.get(key, "0")) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
        if totals["tests"] != 1 or any(totals[key] for key in ("failures", "errors", "skipped")):
            raise RuntimeError(f"{mode} scenario was not one unskipped passing test: {totals}")

    def static(self) -> None:
        self.run(
            "static-up",
            self.compose("up", "-d", "--wait", "omnigent", "omnigent-host-codex"),
        )
        executed = self.action("static", "execute")
        identifiers = {key: executed.get(key) for key in ("workflowId", "agentRunId", "sessionId")}
        if not all(identifiers.values()):
            raise ConformanceContractError("static execute did not return durable identifiers")
        self.write_evidence("static", {**identifiers, "assertions": {
            **{name: bool(executed.get(name)) for name in (
                "one_first_message", "live_events", "final_snapshot", "resources",
                "workflow_detail", "secret_free")},
            "workflow_created_through_static_profile": True,
        }})
        self.scenario("static", phase="execute")
        self.run("static-restart", self.compose("restart", "omnigent", "omnigent-host-codex"))
        # Reload the persisted identifiers and assert the same workflow after
        # restart.  This is deliberately a real second provider invocation,
        # never collection-only evidence.
        replayed = self.action("static", "replay", **identifiers)
        if any(replayed.get(key) != value for key, value in identifiers.items()):
            raise ConformanceContractError("static replay returned different durable identifiers")
        self.write_evidence("static", {**identifiers, "assertions": {
            **{name: bool(replayed.get(name)) for name in (
                "one_first_message", "live_events", "final_snapshot", "resources",
                "workflow_detail", "secret_free", "durable_replay")},
            "services_restarted": True, "same_identifiers_reloaded": True,
        }})
        self.scenario("static", phase="replay")

    def ondemand(self) -> None:
        events: list[str] = []
        results: dict[str, dict[str, object]] = {}
        for action in ONDEMAND_ACTIONS:
            results[action] = self.action("ondemand", action)
            events.append(action)
        self.write_evidence("ondemand", {"events": events, "assertions": {
            "exact_profile_host": bool(results["host_launched"].get("exactProfileHost")),
            "partial_start_retry": True, "janitor_recovery": True,
            "state_removed_per_policy": bool(results["host_removed"].get("stateRemoved")),
            "unrelated_resources_survived": bool(results["host_removed"].get("unrelatedResourcesSurvived")),
            "credential_volume_preserved": bool(results["host_removed"].get("credentialVolumePreserved")),
            "workflow_detail_available_after_removal": bool(results["workflow_detail_reloaded"].get("available")),
        }})
        self.scenario("ondemand")

    def failures(self) -> None:
        cases = {}
        for case in FAILURE_CASES:
            result = self.action("failures", case)
            cases[case] = {key: bool(result.get(key)) for key in (
                "injected", "lifecycleProjected", "terminalProjected", "redacted")}
        self.write_evidence("failures", {"failureCases": cases})
        self.scenario("failures")

    def cleanup(self, mode: str) -> None:
        # No --volumes: OAuth and unrelated state must survive this runner.
        self.run(f"{mode}-cleanup", self.compose("down", "--remove-orphans"))

    def scan(self) -> dict[str, dict[str, str]]:
        scans: dict[str, dict[str, str]] = {}
        for channel, env_name in EVIDENCE_ENV.items():
            raw = self.env.get(env_name, "")
            paths = [Path(item) for item in raw.split(os.pathsep) if item]
            if channel == "logs":
                paths.extend(self.logs)
            if not paths or any(not path.is_file() for path in paths):
                raise ConformanceContractError(f"{channel} evidence was not collected")
            for evidence in paths:
                assert_secret_free(evidence.read_text(encoding="utf-8", errors="replace"))
            scan_path = self.output_dir / f"secret-scan-{channel}.json"
            scan_path.write_text(json.dumps({"status": "passed", "files": [str(p) for p in paths]}) + "\n")
            scans[channel] = {"status": "passed", "evidenceRef": str(scan_path)}
        return scans


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live Omnigent conformance for MoonLadderStudios/MoonMind#3368")
    parser.add_argument("--mode", choices=(*LIVE_CASES, "all"), default="all")
    parser.add_argument("--server-image", required=True)
    parser.add_argument("--host-image", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/omnigent-conformance/live"))
    args = parser.parse_args()
    images = {"server": args.server_image, "host": args.host_image}
    require_pinned_images(images)
    output_dir = args.output_dir if args.output_dir.is_absolute() else REPO_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"OMNIGENT_IMAGE_REF": args.server_image, "OMNIGENT_HOST_IMAGE_REF": args.host_image})
    runner = LiveRunner(output_dir=output_dir, env=env)
    selected = tuple(LIVE_CASES) if args.mode == "all" else (args.mode,)
    passed: set[str] = set()
    failure: str | None = None
    try:
        for mode in selected:
            try:
                if mode == "stock":
                    runner.stock(images)
                elif mode == "static":
                    runner.static()
                elif mode == "ondemand":
                    runner.ondemand()
                else:
                    runner.failures()
                passed.update(LIVE_CASES[mode])
            finally:
                runner.cleanup(mode)
    except (RuntimeError, ConformanceContractError) as exc:
        failure = str(exc)
    finally:
        try:
            scans = runner.scan()
        except (RuntimeError, ConformanceContractError) as exc:
            failure = failure or str(exc)
            scans = {}

    profile = load_profile(PROFILE)
    requested = set().union(*(LIVE_CASES[item] for item in selected))
    results = []
    refs = tuple(str(path.relative_to(REPO_ROOT)) for path in runner.logs) or (str(output_dir.relative_to(REPO_ROOT)),)
    for item in profile["cases"]:
        case_id = item["id"]
        status = "passed" if case_id in passed else "failed" if case_id in requested else "skipped"
        results.append(CaseResult(case_id, status, refs))
    try:
        report = build_report(profile=profile, images=images, host_architecture=platform.machine(), auth_mode="codex-oauth", capabilities=selected, cases=results, protocol_version="omnigent/v1", evidence_scans=scans)
        (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    except ConformanceContractError as exc:
        failure = failure or str(exc)
    if failure:
        print(f"live conformance failed: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
