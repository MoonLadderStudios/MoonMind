"""Bootstrap evidence stays inside the explicitly selected state directory."""

from pathlib import Path

import pytest

from moonmind.omnigent.bootstrap import store
from moonmind.omnigent.bootstrap.models import (
    BootstrapRecord,
    ResolvedOmnigentDeploymentState,
)


@pytest.mark.parametrize("kind", ["bootstrap", "resolved"])
@pytest.mark.parametrize("filename", ["state.json", "resolved-images.json"])
def test_explicit_state_path_does_not_overwrite_deployment_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, filename: str
) -> None:
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    bootstrap = deployment / "bootstrap.json"
    resolved = deployment / "resolved-images.json"
    for path in (bootstrap, resolved):
        path.write_text('"verified deployment evidence"', encoding="utf-8")
    monkeypatch.setattr(store, "_COMPOSE_BOOTSTRAP", bootstrap)
    monkeypatch.setattr(store, "_COMPOSE_RESOLVED", resolved)
    selected = tmp_path / "isolated" / filename
    if kind == "bootstrap":
        monkeypatch.setenv("MOONMIND_OMNIGENT_BOOTSTRAP_STATE_PATH", str(selected))
        record = BootstrapRecord()
        store.save_bootstrap_record(record)
        assert store.load_bootstrap_record() == record
    else:
        monkeypatch.setenv("MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", str(selected))
        record = ResolvedOmnigentDeploymentState(
            details={"opencodeHostCompatibility": {"status": "blocked"}}
        )
        store.save_resolved_state(record)
        assert store.load_resolved_state() == record

    assert selected.is_file()
    for path in (bootstrap, resolved):
        assert path.read_text(encoding="utf-8") == '"verified deployment evidence"'
