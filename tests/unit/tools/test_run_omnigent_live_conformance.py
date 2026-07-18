from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[3] / "tools/run_omnigent_live_conformance.py"
    spec = importlib.util.spec_from_file_location("omnigent_live", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compose_is_isolated_and_cleanup_preserves_volumes(tmp_path, monkeypatch):
    module = _module()
    calls = []

    class Result:
        returncode = 0

    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: calls.append(command) or Result())
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    runner.cleanup("stock")
    command = calls[0]
    assert command[:4] == ["docker", "compose", "--project-name", module.PROJECT]
    assert "down" in command
    assert "--volumes" not in command
    assert "-v" not in command


def test_static_restart_precedes_replay_and_cleanup_is_explicit(tmp_path, monkeypatch):
    module = _module()
    names = []
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    monkeypatch.setattr(runner, "run", lambda name, command: names.append(name))
    monkeypatch.setattr(runner, "scenario", lambda mode, phase=None: names.append(f"{mode}-{phase}"))
    runner.static()
    runner.cleanup("static")
    assert names == ["static-up", "static-execute", "static-restart", "static-replay", "static-cleanup"]


def test_scan_rejects_secret_like_live_evidence(tmp_path):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    log = tmp_path / "provider.log"
    log.write_text("authorization=do-not-publish")
    runner.logs.append(log)
    try:
        runner.scan()
    except module.ConformanceContractError:
        pass
    else:
        raise AssertionError("secret-like evidence was accepted")


def test_each_mode_selects_a_distinct_provider_node():
    module = _module()
    assert set(module.SCENARIOS) == set(module.LIVE_CASES)
    assert len(set(module.SCENARIOS.values())) == len(module.SCENARIOS)


def test_static_replay_is_not_pytest_collection_placeholder():
    module = _module()
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "--collect-only" not in source


def test_every_mode_has_dedicated_scenario_evidence_channel():
    module = _module()
    assert set(module.SCENARIO_EVIDENCE_ENV) == set(module.LIVE_CASES)
    assert len(set(module.SCENARIO_EVIDENCE_ENV.values())) == len(module.LIVE_CASES)


def test_scan_requires_each_evidence_channel(tmp_path):
    module = _module()
    runner = module.LiveRunner(output_dir=tmp_path, env={})
    try:
        runner.scan()
    except module.ConformanceContractError as exc:
        assert "evidence was not collected" in str(exc)
    else:
        raise AssertionError("missing evidence channels were accepted")
