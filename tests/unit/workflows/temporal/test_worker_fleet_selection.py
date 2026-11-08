"""Fleet selection keeps process configuration separate from queue contracts."""

import json

import pytest
from pydantic import ValidationError

from moonmind.config.settings import TemporalSettings, settings
from moonmind.workflows.temporal import workers


@pytest.mark.parametrize(
    "value",
    [
        " workflow, ARTIFACTS,workflow ",
        '["workflow", "ARTIFACTS", "workflow"]',
        ["workflow", "ARTIFACTS", "workflow"],
    ],
)
def test_plural_selection_is_normalized_and_authoritative(value):
    config = TemporalSettings(
        _env_file=None, worker_fleet="sandbox", worker_fleets=value
    )
    assert config.worker_fleets == ("workflow", "artifacts")
    topologies = workers.describe_configured_workers(temporal_settings=config)
    assert tuple(topology.fleet for topology in topologies) == ("workflow", "artifacts")
    for topology in topologies:
        assert topology == workers.build_worker_topology(
            fleet=topology.fleet, temporal_settings=config
        )


@pytest.mark.parametrize("value", ["", "workflow,", "bogus", [], ["workflow", "bogus"]])
def test_invalid_plural_configuration_fails_closed(value):
    with pytest.raises(ValidationError, match="TEMPORAL_WORKER_FLEETS"):
        TemporalSettings(_env_file=None, worker_fleets=value)


def test_singular_fallback_and_explicit_override():
    config = TemporalSettings(_env_file=None, worker_fleet="sandbox")
    assert [
        t.fleet for t in workers.describe_configured_workers(temporal_settings=config)
    ] == ["sandbox"]
    config = config.model_copy(update={"worker_fleets": ("workflow", "artifacts")})
    assert [
        t.fleet
        for t in workers.describe_configured_workers(
            fleet="llm", temporal_settings=config
        )
    ] == ["llm"]
    assert (
        workers.describe_configured_worker(temporal_settings=config).fleet == "sandbox"
    )


def test_plural_environment_is_csv(monkeypatch):
    monkeypatch.setenv("TEMPORAL_WORKER_FLEETS", "workflow,artifacts")
    assert TemporalSettings(_env_file=None).worker_fleets == ("workflow", "artifacts")


def test_cli_plural_description_and_explicit_override(monkeypatch, capsys):
    config = TemporalSettings(
        _env_file=None, worker_fleet="sandbox", worker_fleets="workflow,artifacts"
    )
    monkeypatch.setattr(settings, "temporal", config)
    assert workers.main(["--describe-json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["fleet"] for item in payload["fleets"]] == ["workflow", "artifacts"]
    assert workers.main(["--fleet", "llm", "--describe-json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["fleet"] == "llm"
    assert "fleets" not in payload


def test_plural_json_environment_preserves_legacy_marker(monkeypatch):
    monkeypatch.setenv(
        "TEMPORAL_WORKER_FLEETS", '["workflow","artifacts","llm","integrations"]'
    )
    monkeypatch.setenv("TEMPORAL_WORKER_FLEET", "artifacts")
    config = TemporalSettings(_env_file=None)
    assert config.worker_fleet == "artifacts"
    assert config.worker_fleets == ("workflow", "artifacts", "llm", "integrations")
    assert [
        t.fleet for t in workers.describe_configured_workers(temporal_settings=config)
    ] == list(config.worker_fleets)
