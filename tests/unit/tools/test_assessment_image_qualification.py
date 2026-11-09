"""Qualification executes the production source-free probe, not a mocked scan."""

import hashlib
import json

import pytest
import yaml

from tools.ci import qualify_repository_security_image as qualification


def test_readiness_container_is_source_free_and_hardened():
    args = qualification.readiness_container(
        "scanner:current", ["prowler", "--version"]
    )
    assert args == [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network",
        "none",
        "--hostname",
        "localhost",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--cpus",
        "1",
        "--memory",
        "1g",
        "--pids-limit",
        "128",
        "--tmpfs",
        "/tmp:rw,exec,nosuid,nodev,size=536870912",
        "--env",
        "HOME=/tmp",
        "--env",
        "TMPDIR=/tmp",
        "--entrypoint",
        "timeout",
        "scanner:current",
        "--signal=TERM",
        "--kill-after=5s",
        "75s",
        "prowler",
        "--version",
    ]


def test_repository_qualification_proves_each_selected_helper(monkeypatch, tmp_path):
    calls = []

    def invoke(argv, evidence, **kwargs):
        calls.append(argv)
        selection = json.loads(argv[-1])
        return json.dumps(
            {
                "schemaVersion": "scanner-readiness/v1",
                "status": "ready",
                "selectionDigest": hashlib.sha256(
                    json.dumps(
                        selection, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                "checks": [
                    {
                        "component": "scanner",
                        "code": "scanner_available",
                        "status": "ready",
                    }
                ],
            }
        )

    monkeypatch.setattr(qualification, "invoke", invoke)
    qualification.qualify_readiness("scanner:current", tmp_path, kind="repository")
    assert len(calls) == 4
    assert all("moonmind.security.scanner_readiness" in args for args in calls)
    assert all("--mount" not in args for args in calls)


@pytest.mark.parametrize("output", ["{}", "not json", '{"status":"blocked"}'])
def test_repository_qualification_rejects_missing_or_blocked_evidence(
    monkeypatch, tmp_path, output
):
    monkeypatch.setattr(qualification, "invoke", lambda *a, **k: output)
    with pytest.raises(qualification.ImageError):
        qualification.qualify_readiness("scanner:current", tmp_path, kind="repository")


@pytest.mark.parametrize(
    "kind,command",
    [
        ("web", ["/zap/zap.sh", "-Xmx512m", "-dir", "/tmp/zap", "-version"]),
        ("cloud", ["prowler", "--version"]),
    ],
)
def test_upstream_qualification_requires_real_version(
    monkeypatch, tmp_path, kind, command
):
    calls = []

    def invoke(argv, *args, **kwargs):
        calls.append(argv)
        return "3.2.1\n"

    monkeypatch.setattr(qualification, "invoke", invoke)
    qualification.qualify_readiness("upstream:stable", tmp_path, kind=kind)
    assert calls[0][-len(command) :] == command
    monkeypatch.setattr(qualification, "invoke", lambda *a, **k: "startup failed")
    with pytest.raises(qualification.ImageError):
        qualification.qualify_readiness("upstream:stable", tmp_path, kind=kind)


def test_readiness_changes_trigger_nonpublishing_ci():
    path = qualification.ROOT / ".github/workflows/upstream-assessment-images.yml"
    workflow = yaml.safe_load(path.read_text())
    triggers = workflow.get("on", workflow.get(True))
    for event in ("push", "pull_request"):
        assert "moonmind/security/scanner_readiness.py" in triggers[event]["paths"]
    commands = "\n".join(
        step.get("run", "")
        for job in workflow["jobs"].values()
        for step in job["steps"]
    )
    assert "--readiness-only" in commands
    assert "--kind web" in commands and "--kind cloud" in commands
    assert "docker push" not in commands
