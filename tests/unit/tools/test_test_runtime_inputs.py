from pathlib import Path

import pytest

from tools.ci import test_runtime_inputs


def test_runtime_cache_identity_covers_dependencies_and_resolved_bases(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    for name in test_runtime_inputs.INPUTS:
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    recipe = (
        "ARG PYTHON_BASE=python:3.15-slim-bookworm\nARG NODE_BASE=node:26-bookworm\n"
        "ARG SOCKET_PROXY_BASE=ghcr.io/wollomatic/socket-proxy:1.13.2\n"
        "FROM ${PYTHON_BASE} AS test-runtime\nCOPY "
        + " ".join(
            name
            for name in test_runtime_inputs.INPUTS
            if name not in {"api_service/Dockerfile", ".dockerignore"}
        )
        + " /app/\n"
    )
    Path("api_service/Dockerfile").write_text(recipe)
    base = ["sha256:" + "a" * 64]
    monkeypatch.setattr(
        test_runtime_inputs.subprocess, "check_output", lambda *a, **k: base[0]
    )

    def identity():
        test_runtime_inputs.main()
        lines = dict(
            line.split("=", 1) for line in capsys.readouterr().out.splitlines()
        )
        assert all(
            "@sha256:" in lines[name]
            for name in ("PYTHON_BASE", "NODE_BASE", "SOCKET_PROXY_BASE")
        )
        return lines["key"]

    before = identity()
    Path("README.md").write_text("unrelated prose")
    assert identity() == before
    for name in test_runtime_inputs.INPUTS[1:]:
        original = Path(name).read_text()
        Path(name).write_text(original + "changed")
        assert identity() != before, name
        Path(name).write_text(original)
    base[0] = "sha256:" + "b" * 64
    assert identity() != before
    with pytest.raises(ValueError, match="inventory"):
        test_runtime_inputs.verify_input_inventory(
            recipe + "COPY new-dependency.sh /app/\n"
        )
