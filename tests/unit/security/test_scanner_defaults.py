"""Supported scanner defaults retain selected authority and actual provenance."""

import hashlib
import json
from pathlib import Path

import pytest

from moonmind.schemas.repository_scanner_models import RepositoryScan
from moonmind.security import repository_scanner_runner as runner
from moonmind.security.assessments import content_key
from moonmind.security.repository_scanners import ScannerExecution, build_scanner_job
from moonmind.security.scanner_feeds import scanner_feed
from tests.unit.security.test_scanner_feeds import NOW, publish


def test_default_scans_need_no_rule_or_feed_identity_json():
    opengrep = RepositoryScan(scanner="opengrep", outputDirectory="scan/opengrep")
    assert opengrep.rules is None
    trivy = RepositoryScan(scanner="trivy", outputDirectory="scan/trivy")
    assert trivy.database_cache_ref == "scanner-db"
    assert trivy.database_digest is None
    assert build_scanner_job(trivy)["networkMode"] == "none"
    assert build_scanner_job(trivy)["caches"][0]["readOnly"] is True
    with pytest.raises(ValueError):
        RepositoryScan(scanner="opengrep", outputDirectory="scan", rules=[])


def test_current_feed_default_resolves_once_and_retains_explicit_snapshot(tmp_path):
    root = tmp_path / "feeds"
    first = publish(root, seed="a")
    selected = publish(root, seed="b")
    scan = RepositoryScan(scanner="trivy", outputDirectory="scan")
    cache = tmp_path / "private"
    cache.mkdir()
    evidence, limitations = scanner_feed(scan, root, cache, [], now=NOW)
    assert evidence.snapshot.snapshot_id == selected.snapshot_id
    assert limitations == []
    pinned = scan.model_copy(update={"database_digest": first.snapshot_id})
    other = tmp_path / "pinned"
    other.mkdir()
    evidence, _ = scanner_feed(pinned, root, other, [], now=NOW)
    assert evidence.snapshot.snapshot_id == first.snapshot_id


def rule_manifest(root):
    default = root / "default"
    default.mkdir(parents=True)
    body = b"rules: []\n"
    (default / "python.yml").write_bytes(body)
    rule = {
        "id": "moonmind-python",
        "path": "/opt/moonmind-scanner-rules/default/python.yml",
        "sha256": hashlib.sha256(body).hexdigest(),
        "license": "Apache-2.0",
        "source": "https://github.com/MoonLadderStudios/MoonMind",
        "version": "1",
        "languages": ["python"],
        "notice": "Original MoonMind rules, Apache-2.0.",
    }
    (default / "manifest.json").write_text(
        json.dumps({"schemaVersion": 1, "rules": [rule]})
    )
    return rule


def test_default_rules_are_resolved_from_image_not_checkout(tmp_path, monkeypatch):
    root = tmp_path / "rules"
    rule = rule_manifest(root)
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("eval(input())\n")
    output = tmp_path / "output"
    commands = []

    def run(self, argv, *, cwd, env, capture=None, **kwargs):
        commands.append(argv)
        if capture:
            capture.write_text("opengrep 1.2.3\n")
        if "--output" in argv:
            Path(argv[argv.index("--output") + 1]).write_text(
                json.dumps(
                    {"results": [], "errors": [], "paths": {"scanned": ["app.py"]}}
                )
            )
        return 0, "succeeded"

    monkeypatch.setattr(runner.BoundedProcesses, "run", run)
    scan = RepositoryScan(scanner="opengrep", outputDirectory="scan")
    assert runner.run_scanner(scan, source=source, output=output, rules_root=root) == 0
    execution = ScannerExecution.model_validate_json(
        (output / "execution.json").read_bytes()
    )
    assert (
        execution.effective_rules[0].model_dump(by_alias=True, exclude_defaults=True)
        == rule
    )
    assert any(str(root / "default/python.yml") in argv for argv in commands)
    assert scan.rules is None  # No mutation of the admitted request.


@pytest.mark.parametrize(
    "damage,diagnostic",
    [
        ("missing", "scanner_rules_unavailable"),
        ("nonobject", "scanner_rules_unavailable"),
        ("changed", "scanner_rule_integrity_failed"),
    ],
)
def test_missing_or_changed_rules_do_not_run_or_look_clean(
    tmp_path, monkeypatch, damage, diagnostic
):
    root = tmp_path / "rules"
    rule_manifest(root)
    if damage == "missing":
        (root / "default/manifest.json").unlink()
    elif damage == "nonobject":
        (root / "default/manifest.json").write_text("[]")
    else:
        (root / "default/python.yml").write_text("changed")
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"
    monkeypatch.setattr(
        runner.BoundedProcesses, "run", lambda *a, **k: pytest.fail("must not run")
    )
    scan = RepositoryScan(scanner="opengrep", outputDirectory="scan")
    assert runner.run_scanner(scan, source=source, output=output, rules_root=root) == 2
    metadata = json.loads((output / "execution.json").read_text())
    assert diagnostic in metadata["limitations"]
    assert metadata["states"] == {"scan": "failed"}


@pytest.mark.asyncio
@pytest.mark.parametrize("scanner", ["opengrep", "trivy"])
async def test_collection_and_report_keep_effective_defaults(tmp_path, scanner):
    from moonmind.security.repository_scanners import ScannerSummary
    from moonmind.security.scanner_feeds import feed_evidence
    from moonmind.workflows.temporal.container_job_attachments import claim, storage_for
    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
    )
    from tests.unit.security.test_scanner_assessment import observe
    from tests.unit.workflows.temporal.test_container_job_evidence import (
        _Publisher,
        _request,
    )

    scan = RepositoryScan(scanner=scanner, outputDirectory="scan")
    request = _request(tmp_path, image=None, **build_scanner_job(scan))
    request.terminal_state = "succeeded"
    request.resolved_image_ref = "sha256:" + "c" * 64
    storage = storage_for(tmp_path, request.job_id)
    claim(storage, request.ownership_token)
    output = storage.outputs / "scan"
    output.mkdir(parents=True)
    record = ScannerExecution(
        inputDigest="a" * 64,
        assets=["app.py"],
        version="1.2.3",
        exits={"scan": 0},
        states={"scan": "succeeded"},
    )
    if scanner == "opengrep":
        rule = rule_manifest(tmp_path / "rules")
        record.effective_rules = [runner.ScannerRule.model_validate(rule)]
        native = {"results": [], "errors": [], "paths": {"scanned": ["app.py"]}}
    else:
        snapshot = publish(tmp_path / "feed")
        record.database_digest = snapshot.snapshot_id
        record.feed = feed_evidence(snapshot, misconfig=True, max_age_hours=72, now=NOW)
        native = {
            "SchemaVersion": 2,
            "Results": [{"Target": "app.py", "Vulnerabilities": []}],
        }
    (output / "execution.json").write_text(record.model_dump_json(by_alias=True))
    (output / "scan.json").write_text(json.dumps(native))
    publisher = _Publisher()
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, evidence_publisher=publisher
    )
    await backend._collect_declared_outputs(request)
    summary = ScannerSummary.model_validate_json(
        publisher.artifacts[f"{request.job_id}-output-summary.json"]
    )
    assert summary.coverage == "complete", summary.limitations
    assert summary.execution.source_image == request.resolved_image_ref
    if scanner == "opengrep":
        assert summary.admission.rules[0].notice == rule["notice"]
        assert summary.admission.rules[0].sha256 == rule["sha256"]
    else:
        assert summary.admission.database_digest == snapshot.snapshot_id
    observation = observe(summary)
    assert observation.coverage[0].status == "complete"
    assert observation.provenance["scannerImage"] == request.resolved_image_ref
    assert observation.provenance["scannerRules"] == content_key(
        [
            r.model_dump(mode="json", by_alias=True)
            for r in summary.admission.rules or []
        ]
    )
