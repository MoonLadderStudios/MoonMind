"""Replay the verifier's tool boundary without duplicating Skill semantics.

Provider read/command/report actions and service responses are scripted fixture
inputs. Production materialization, CLI parsing, submission identity, HTTP calls,
polling, and terminal/diagnostic rendering are real. Autonomous interpretation
of the prose Skill and Docker workload execution require separate live evidence.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import shutil
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from typer.testing import CliRunner

from moonmind.cli import app
from moonmind.schemas.agent_skill_models import (
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
    RuntimeMaterializationMode,
)
from moonmind.services.skill_materialization import AgentSkillMaterializer
from tests.integration.reliability.helpers import load_replay

pytestmark = [pytest.mark.integration, pytest.mark.reliability_journey]
_REPLAY = "verifier-docker-test-discovery"
_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.asyncio
@pytest.mark.parametrize("case_name", ["available", "rejected"])
async def test_verifier_docker_discovery_tool_replay(tmp_path, monkeypatch, case_name):
    manifest = load_replay(_REPLAY, "manifest.json")
    case = manifest["cases"][case_name]
    workspace = tmp_path / "repo"
    shutil.copytree(Path(__file__).parent / "replays" / _REPLAY / "repo", workspace)
    payload = (_ROOT / ".agents/skills/moonspec-verify/SKILL.md").read_bytes()
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()

    class ArtifactStore:
        async def read(self, *, artifact_id, principal, allow_restricted_raw):
            assert artifact_id == "artifact:resolved-verifier"
            assert principal == "system" and allow_restricted_raw
            return object(), payload

    snapshot = ResolvedSkillSet(
        snapshot_id="verifier-docker-discovery",
        resolved_at=datetime(2026, 9, 10, tzinfo=UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="moonspec-verify",
                content_ref="artifact:resolved-verifier",
                content_digest=digest,
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.DEPLOYMENT
                ),
            )
        ],
    )
    materialized = await AgentSkillMaterializer(
        str(workspace), artifact_service=ArtifactStore()
    ).materialize(
        snapshot, manifest["runtimeId"], RuntimeMaterializationMode.WORKSPACE_MOUNTED
    )
    active = Path(materialized.metadata["visiblePath"])
    assert materialized.metadata["activeSkills"] == manifest["selectedSkills"]
    visible_payload = (active / "moonspec-verify/SKILL.md").read_bytes()
    assert visible_payload == payload
    instructions = " ".join(visible_payload.decode().split())
    # Instruction-delivery regression check is intentionally separate from the
    # scripted provider actions below. Removing discovery from the real bundle
    # fails here; the replay never pretends to interpret prose as agent behavior.
    for fragment in manifest["requiredInstructionFragments"]:
        assert fragment in instructions, (
            f"resolved verifier lost discovery guidance: {fragment}"
        )

    observed = ["skill.read"]
    documents = {}
    for relative_path in manifest["providerReadActions"]:
        documents[relative_path] = (workspace / relative_path).read_text()
        observed.append(f"read:{relative_path}")
    assert "docs/testing.md" in documents["README.md"]
    playbook = documents["docs/testing.md"]
    command = next(line for line in playbook.splitlines() if line.startswith("moonmind "))
    argv = shlex.split(command)[1:]
    assert argv[argv.index("--spec") + 1] == "tools/automation-job.json"
    assert manifest["candidateCheckpoint"] in argv[argv.index("--request-id") + 1]
    # An empty local tool directory models the escaped missing-compiler input;
    # no provider credentials, compiler, Docker executable or socket are used.
    local_bin = tmp_path / "local-bin"
    local_bin.mkdir()
    monkeypatch.setenv("PATH", str(local_bin))
    assert shutil.which(manifest["compiler"]) is None
    assert shutil.which("docker") is None
    observed.append("local-compiler:absent")

    requests = []
    states = iter(case.get("serviceStates", []))
    job_id = "container-job:" + "a" * 32

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            tool = body["tool"]
            observed.append(tool)
            assert self.path == "/mcp/tools/call"
            assert self.headers["Authorization"] == "Bearer replay-scoped-token"
            status = 200
            if tool == "container.submit":
                if case_name == "rejected":
                    status = case["admissionStatus"]
                    response = {"detail": {"message": case["admissionDiagnostic"]}}
                else:
                    response = {"result": {"jobId": job_id, "state": "queued"}}
            elif tool == "container.status":
                state = next(states)
                observed.append(f"state:{state}")
                result = {"jobId": job_id, "state": state}
                if state == "succeeded":
                    result.update(
                        terminal={"exitCode": 0},
                        logsRef="artifact:automation-logs",
                        artifactsRef="artifact:automation-report",
                    )
                response = {"result": result}
            elif tool == "container.logs":
                response = {
                    "result": {
                        "jobId": job_id,
                        "entries": [{
                            "sequence": 1,
                            "stream": "stdout",
                            "text": "native-smoke: 3 passed",
                        }],
                        "nextCursor": None,
                    }
                }
            else:
                raise AssertionError(f"unexpected tool {tool}")
            encoded = json.dumps(response).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Isolate any operator credential selectors; only a synthetic session token
    # may reach the replay HTTP service.
    for key in (
        "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN_FILE",
        "MOONMIND_CONTAINER_JOBS_SOURCE_KIND",
        "MOONMIND_CONTAINER_JOBS_WORKSPACE_KIND",
        "MOONMIND_CONTAINER_JOBS_WORKSPACE_ID",
        "MOONMIND_CONTAINER_JOBS_WORKSPACE_RELATIVE_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(workspace)
    env = {
        "MOONMIND_ACTIVE_SKILLS_DIR": str(active),
        "MOONMIND_CONTAINER_JOBS_MCP_URL": f"http://127.0.0.1:{server.server_port}/mcp",
        "MOONMIND_AGENT_RUN_ID": manifest["incidentWorkflowId"],
        "MOONMIND_TASK_WORKFLOW_ID": manifest["incidentWorkflowId"],
        "MOONMIND_RUNTIME_ID": manifest["runtimeId"],
        "MOONMIND_CONTAINER_JOBS_SESSION_ID": "replay-session",
        "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN": "replay-scoped-token",
    }
    try:
        output = CliRunner().invoke(app, argv, env=env)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert output.exit_code == (0 if case_name == "available" else 1), output.output
    for fragment in case["requiredToolEvidence"]:
        assert fragment in output.output, f"CLI lost authoritative evidence: {fragment}"
    observed.append("tool.evidence-returned")
    # Replay the fixture's final provider action only after checking its tool
    # evidence. This validates the recorded trace, not a native verifier policy.
    report_path = tmp_path / "verification-report.txt"
    report_path.write_text(case["providerReport"] + "\n" + output.output)
    observed.append("provider.report")
    assert observed.index("local-compiler:absent") < observed.index("container.submit")
    assert observed[-2:] == ["tool.evidence-returned", "provider.report"]
    submissions = [
        request for request in requests if request["tool"] == "container.submit"
    ]
    assert len(submissions) == 1
    submission = requests[0]["arguments"]
    assert submission["idempotencyKey"].endswith(":verify-candidate-01-automation")
    assert submission["spec"]["workspaceRef"] == {
        "kind": "managed_runtime",
        "runtimeId": "codex_cli",
        "agentRunId": manifest["incidentWorkflowId"],
        "relativePath": "repo",
    }
    assert submission["spec"]["imageSourceRef"] == "sample-native-tests"
    documented_spec = json.loads(documents["tools/automation-job.json"])
    assert submission["spec"]["command"] == documented_spec["command"]
    if case_name == "available":
        assert observed[-9:] == [
            "container.status",
            "state:queued",
            "container.status",
            "state:running",
            "container.status",
            "state:succeeded",
            "container.logs",
            "tool.evidence-returned",
            "provider.report",
        ]
        assert "NOT RUN" not in report_path.read_text()
    else:
        assert [request["tool"] for request in requests] == ["container.submit"]
        assert case["admissionDiagnostic"] in report_path.read_text()
        assert "NOT RUN" in report_path.read_text()
