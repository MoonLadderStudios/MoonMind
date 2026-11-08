"""The service inventory CLI submits only a typed native profile selection."""

import json

import pytest
from typer.testing import CliRunner

from moonmind import cli
from moonmind.schemas.container_job_models import ContainerJobSpec
from tests.unit.security.test_service_inventory_profile import inventory_spec


def test_cli_service_inventory_uses_typed_source_free_job(tmp_path, monkeypatch):
    selected = inventory_spec().workload_profile
    admission = tmp_path / "admission.json"
    admission.write_text(selected.model_dump_json(by_alias=True))
    submitted = []
    monkeypatch.setattr(cli, "run_container_job", lambda spec: submitted.append(spec))
    monkeypatch.setattr(cli, "_print_container_job_result", lambda _: None)
    result = CliRunner().invoke(
        cli.app, ["container", "service-inventory", "--admission", str(admission)]
    )
    assert result.exit_code == 0, result.output
    spec = ContainerJobSpec.model_validate(submitted[0])
    assert spec.workload_profile == selected
    assert spec.input_attachments == [] and spec.workspace_ref is None
    assert spec.command == [] and spec.entrypoint == [] and spec.image is None
    assert spec.timeout_seconds >= selected.native_network.limits.max_duration_seconds


@pytest.mark.parametrize(
    "gap",
    [
        "missing-options",
        "missing-native",
        "extra-arguments",
        "unadmitted-probe",
        "oversized",
    ],
)
def test_cli_inventory_rejects_invalid_admission_without_echo_or_submission(
    tmp_path, monkeypatch, gap
):
    selected = inventory_spec().workload_profile.model_dump(by_alias=True)
    if gap == "missing-options":
        selected.pop("serviceInventory")
    elif gap == "missing-native":
        selected.pop("nativeNetwork")
    elif gap == "extra-arguments":
        selected["arguments"] = {"secret": "unshared-private-marker"}
    elif gap == "unadmitted-probe":
        selected["serviceInventory"]["httpHeadPorts"] = [443]
    admission = tmp_path / "admission.json"
    admission.write_text(
        " " * (96 * 1024 + 1) if gap == "oversized" else json.dumps(selected)
    )
    submitted = []
    monkeypatch.setattr(cli, "run_container_job", lambda spec: submitted.append(spec))
    result = CliRunner().invoke(
        cli.app, ["container", "service-inventory", "--admission", str(admission)]
    )
    assert result.exit_code == 1, result.output
    assert not submitted
    assert "unshared-private-marker" not in result.output
    assert "Invalid service inventory admission" in result.output


def test_cli_inventory_reads_admission_with_a_real_byte_bound(tmp_path, monkeypatch):
    from pathlib import Path

    admission = tmp_path / "admission.json"
    admission.write_text(
        inventory_spec().workload_profile.model_dump_json(by_alias=True)
    )
    original = Path.read_bytes

    def reject_unbounded_read(path):
        if path == admission:
            raise AssertionError("admission must not use an unbounded read")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", reject_unbounded_read)
    submitted = []
    monkeypatch.setattr(cli, "run_container_job", lambda spec: submitted.append(spec))
    monkeypatch.setattr(cli, "_print_container_job_result", lambda _: None)
    result = CliRunner().invoke(
        cli.app, ["container", "service-inventory", "--admission", str(admission)]
    )
    assert result.exit_code == 0, result.output
    assert len(submitted) == 1
