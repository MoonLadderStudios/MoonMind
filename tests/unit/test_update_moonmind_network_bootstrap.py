"""A stopped stack must not be needed to start its own repair helper."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "update_network", ROOT / ".agents/skills/update-moonmind/scripts/update_release.py"
)
update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update)


@pytest.mark.parametrize("existing", [False, True])
def test_private_network_exists_before_helper_is_started(tmp_path, monkeypatch, existing):
    network = "custom-controller-link"
    project = "custom-app"
    identity = {"port": 8472, "targetNetwork": network, "targetProject": project}
    monkeypatch.setattr(update, "_controller_identity", lambda repo: identity)
    events = []
    available = existing
    bootstrap = ROOT / "deploy/controller/bootstrap.py"

    def run(command, **kwargs):
        nonlocal available
        if command[0] == sys.executable and command[1] == "-c":
            # Execute the actual image-owned network function with only Docker
            # transport replaced; the temporary helper cannot repair its network.
            def docker(args, **kw):
                nonlocal available
                events.append(args)
                if args[:3] == ["docker", "network", "inspect"]:
                    return subprocess.CompletedProcess(args, 0 if available else 1, "[]", "")
                assert args == ["docker", "network", "create", "--internal", "--label", "com.docker.compose.project=custom-app", "--label", "com.docker.compose.network=deployment-controller-network", network]
                available = True
                return subprocess.CompletedProcess(args, 0, "network-id", "")
            with monkeypatch.context() as m:
                m.setattr(subprocess, "run", docker)
                m.setattr(sys, "argv", ["-c", *command[3:]])
                exec(command[2], {"__name__": "__main__"})
            return ""
        if "describe-installation" in command:
            return json.dumps({"bindSources": {"repo": str(tmp_path), "state": str(tmp_path / "state")}})
        assert command[:2] == ["docker", "run"]
        assert command[command.index("--network") + 1] == network
        if not available:
            raise RuntimeError("docker run exited 125: network custom-controller-link not found")
        events.append(command)
        return ""

    monkeypatch.setattr(update, "run", run)
    command = update._controller_bootstrap_command(
        bootstrap, {"project": project, "image": "release@sha256:abc"}, tmp_path,
        "http://127.0.0.1:8472", source_image="release@sha256:abc", privileged=True,
    )
    update.run(command, cwd=tmp_path)
    assert events[-1][:2] == ["docker", "run"]
    assert len([e for e in events if e[:3] == ["docker", "network", "create"]]) == (0 if existing else 1)


def test_network_failure_stops_before_helper_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "_controller_identity", lambda repo: {
        "port": 8472, "targetNetwork": "private-link", "targetProject": "saved-app",
    })
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert update._CONTROLLER_ENSURE_NETWORK in command
        assert command[-2:] == ["private-link", "saved-app"]
        raise RuntimeError("Cannot create Docker network: daemon unavailable")

    monkeypatch.setattr(update, "run", run)
    with pytest.raises(RuntimeError, match="Cannot create Docker network"):
        update._controller_bootstrap_command(
            ROOT / "deploy/controller/bootstrap.py",
            {"project": "inferred-other-project", "image": "release"}, tmp_path,
            "http://127.0.0.1:8472", source_image="release", privileged=True,
        )
    assert len(calls) == 1
