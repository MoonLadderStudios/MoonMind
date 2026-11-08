"""The DNS assessment CLI submits only a typed query-only profile selection."""

import json

import pytest
from typer.testing import CliRunner

from moonmind import cli
from moonmind.schemas.container_job_models import ContainerJobSpec
from tests.unit.security.test_dns_assessment_admission import spec as dns_spec


def test_cli_dns_assessment_uses_typed_source_free_job(tmp_path, monkeypatch):
    selected = dns_spec().workload_profile
    admission = tmp_path / "admission.json"
    admission.write_text(selected.model_dump_json(by_alias=True))
    submitted = []
    monkeypatch.setattr(cli, "run_container_job", lambda spec: submitted.append(spec))
    monkeypatch.setattr(cli, "_print_container_job_result", lambda _: None)
    result = CliRunner().invoke(
        cli.app, ["container", "dns-assessment", "--admission", str(admission)]
    )
    assert result.exit_code == 0, result.output
    spec = ContainerJobSpec.model_validate(submitted[0])
    assert spec.workload_profile == selected
    assert spec.input_attachments == [] and spec.workspace_ref is None
    assert spec.command == [] and spec.entrypoint == [] and spec.image is None
    assert spec.timeout_seconds >= selected.dns_assessment.timeout_seconds


@pytest.mark.parametrize(
    "gap",
    [
        "missing-options",
        "missing-authority",
        "extra-arguments",
        "unadmitted-probe",
        "oversized",
    ],
)
def test_cli_dns_rejects_invalid_admission_without_echo_or_submission(
    tmp_path, monkeypatch, gap
):
    selected = dns_spec().workload_profile.model_dump(by_alias=True)
    if gap == "missing-options":
        selected.pop("dnsAssessment")
    elif gap == "missing-authority":
        selected["assessmentRef"] = None
    elif gap == "extra-arguments":
        selected["arguments"] = {"secret": "unshared-private-marker"}
    elif gap == "unadmitted-probe":
        selected["dnsResolver"] = {"endpoint": "https://evil.test/dns-query"}
    admission = tmp_path / "admission.json"
    admission.write_text(
        " " * (96 * 1024 + 1) if gap == "oversized" else json.dumps(selected)
    )
    submitted = []
    monkeypatch.setattr(cli, "run_container_job", lambda spec: submitted.append(spec))
    result = CliRunner().invoke(
        cli.app, ["container", "dns-assessment", "--admission", str(admission)]
    )
    assert result.exit_code == 1, result.output
    assert not submitted
    assert "unshared-private-marker" not in result.output
    assert "Invalid DNS assessment admission" in result.output


def test_cli_dns_reads_admission_with_a_real_byte_bound(tmp_path, monkeypatch):
    from pathlib import Path

    admission = tmp_path / "admission.json"
    admission.write_text(
        dns_spec().workload_profile.model_dump_json(by_alias=True)
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
        cli.app, ["container", "dns-assessment", "--admission", str(admission)]
    )
    assert result.exit_code == 0, result.output
    assert len(submitted) == 1
