"""Network configuration exports: sanitization, the analyzer job and its summary.

The native answers under ``tests/fixtures/security/network_config`` were produced
by the real Batfish analyzer image (2026.08.27) from the sanitized fixtures, so
these tests normalize genuine analyzer output without running the image.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from moonmind.schemas.container_job_models import ContainerJobSpec
from moonmind.schemas.repository_scanner_models import NetworkConfigAssessment
from moonmind.security.network_configs import (
    REDACTION_TOKEN,
    RULES,
    build_network_config_job,
    compare_with_inventory,
    prepare_exports,
    review_locator_errors,
    sanitize_export,
    snapshot_files,
    summarize_network_config,
)
from moonmind.utils.logging import redact_sensitive_text

FIXTURES = Path(__file__).parents[2] / "fixtures" / "security" / "network_config"
#: Every secret value planted in the fixture exports.
SECRETS = (
    "0822455D0A1651",
    "fixture-cleartext-pw",
    "fixture-community-rw",
    "fixture-community-ro",
    "fixture-psk-value",
    "fixture-vty-pw",
    "fixtureSalt",
    "fixtureHash",
)


def assessment(name: str) -> NetworkConfigAssessment:
    data = json.loads((FIXTURES / "assessments.json").read_text())
    return NetworkConfigAssessment.model_validate(data[name])


def raw_exports(settings) -> dict[str, bytes]:
    return {
        e.file_name: (FIXTURES / e.file_name).read_bytes() for e in settings.exports
    }


def summary(name: str):
    settings = assessment(name)
    prepared = prepare_exports(settings, raw_exports(settings))
    native = json.loads((FIXTURES / f"native-{name}.json").read_text())
    refs = {
        file: f"art_sanitized{index}" for index, file in enumerate(prepared.sanitized)
    }
    return summarize_network_config(
        settings, prepared, native, native_ref="art_native", sanitized_refs=refs
    )


def test_sanitized_exports_keep_lines_and_drop_every_secret():
    for name in ("edge-weakened.cfg", "edge-corrected.cfg"):
        raw = (FIXTURES / name).read_bytes()
        clean, disposition, lines = sanitize_export(raw, max_bytes=1 << 20)
        assert disposition is None
        text = clean.decode()
        assert text.count("\n") == raw.decode().count("\n")
        assert not [secret for secret in SECRETS if secret in text]
        for number in lines:
            assert REDACTION_TOKEN in text.splitlines()[number - 1]
        # Structure the analyzer needs survives sanitization.
        assert "ip access-list extended OUTSIDE-IN" in text


@pytest.mark.parametrize(
    "line",
    [
        "enable password 7 0822455D0A1651",
        "username admin privilege 15 password 0 fixture-cleartext-pw",
        "snmp-server community fixture-community-rw RW",
        "crypto isakmp key fixture-psk-value address 198.51.100.7",
        " password fixture-vty-pw",
        "enable secret 9 $9$fixtureSalt$fixtureHashValue",
        'set system root-authentication encrypted-password "$6$fixtureSalt$x"',
        "    set psksecret ENC fixture-psk-value",
        "<pre-shared-key><key>fixture-psk-value</key></pre-shared-key>",
        '{"PreSharedKey": "fixture-psk-value"}',
    ],
)
def test_shared_redaction_masks_device_secrets_in_logs_and_reports(line):
    # The same owner scrubs logs, reports, plans and model-delivered evidence.
    assert not [secret for secret in SECRETS if secret in redact_sensitive_text(line)]


def test_shared_redaction_keeps_key_identifiers_and_ordinary_prose():
    assert (
        redact_sensitive_text("ntp authentication-key 1 md5 fixture-psk-value 7")
        == "ntp authentication-key 1 md5 [REDACTED] 7"
    )
    for prose in ("password reset requested for user", "key: value"):
        assert redact_sensitive_text(prose) == prose


def test_line_numbers_follow_newlines_only():
    raw = "hostname r1\fbanner\u2028x\r\n snmp-server community fixture-community-rw\n".encode()
    clean, _, lines = sanitize_export(raw, max_bytes=1 << 10)
    text = clean.decode()
    assert text.split("\n")[0] == "hostname r1\fbanner\u2028x\r"
    assert lines == [2] and "fixture-community-rw" not in text
    assert len(text) - len(raw.decode()) == len(REDACTION_TOKEN) - len(
        "fixture-community-rw"
    )


def test_unreadable_and_empty_exports_are_named_not_analyzed():
    assert sanitize_export(b"\xff\xfe", max_bytes=10)[1] == "undecodable"
    assert sanitize_export(b"   \n", max_bytes=10)[1] == "empty"
    assert sanitize_export(b"x" * 11, max_bytes=10)[1] == "input_too_large"
    key = (
        b"-----BEGIN RSA PRIVATE KEY-----\nMIIfixture\n-----END RSA PRIVATE KEY-----\n"
    )
    clean, _, lines = sanitize_export(b"hostname r1\n" + key, max_bytes=1 << 10)
    assert b"MIIfixture" not in clean and lines == [2, 3, 4]


def test_job_reads_captured_exports_with_no_network_or_credentials():
    settings = assessment("weakened")
    spec = ContainerJobSpec.model_validate(build_network_config_job(settings))
    assert spec.network_mode == "none"
    assert spec.egress_policy_ref is None and spec.workspace_ref is None
    assert spec.registry_credential_ref is None and not spec.environment
    assert [a.artifact_ref for a in spec.input_attachments] == [
        e.artifact_ref for e in settings.exports
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"networkMode": "bridge"},
        {"egressPolicyRef": "anything"},
        {"command": ["-c", "id"]},
        {"environment": [{"name": "X", "value": "1"}]},
        {"inputAttachments": []},
        {"timeoutSeconds": 86400},
    ],
)
def test_job_refuses_any_widened_variant(change):
    spec = {**build_network_config_job(assessment("weakened")), **change}
    with pytest.raises(ValidationError):
        ContainerJobSpec.model_validate(spec)


def test_contract_refuses_names_and_hosts_outside_its_shape():
    base = json.loads((FIXTURES / "assessments.json").read_text())["corrected"]
    for change in (
        {"flows": [{"host": "edge.example", "port": 22}]},
        {"exports": base["exports"] * 2},
        {"exports": [{**base["exports"][0], "exportedAt": "2026-11-27T08:00:00"}]},
    ):
        with pytest.raises(ValidationError):
            NetworkConfigAssessment.model_validate({**base, **change})


def test_weakened_and_corrected_exports_differ_on_the_expected_rules():
    weakened, corrected = summary("weakened"), summary("corrected")
    edge = [f for f in weakened.findings if f.file == "edge-weakened.cfg"]
    assert {f.rule_id for f in edge} == set(RULES)
    assert corrected.findings == []
    assert set(weakened.checks) == set(corrected.checks) == set(RULES)
    located = {(f.rule_id, tuple(f.lines)) for f in edge}
    assert ("acl-line-unreachable", (22,)) in located
    assert ("cleartext-protocol-permitted", (21,)) in located
    assert ("undefined-reference", (18,)) in located
    assert corrected.status == "complete"
    file = corrected.file("edge-corrected.cfg")
    assert (file.vendor, file.os, file.version, file.coverage) == (
        "Cisco",
        "IOS or IOS XE",
        "15.2",
        "complete",
    )


def test_unsupported_unparsed_and_unqualified_content_are_coverage_gaps():
    result = summary("weakened")
    gaps = {(g.code, g.file): g for g in result.gaps}
    assert gaps[("unparsed_lines", "core-partial.cfg")].lines == [9, 10, 11]
    assert ("version_not_qualified", "branch-legacy.cfg") in gaps
    assert ("unsupported_format", "acme-unsupported.cfg") in gaps
    assert ("not_analyzed", "segmentation.md") in gaps
    assert result.status == "partial"
    coverage = {f.file_name: f.coverage for f in result.files}
    assert coverage == {
        "edge-weakened.cfg": "complete",
        "core-partial.cfg": "partial",
        "branch-legacy.cfg": "partial",
        "acme-unsupported.cfg": "not_analyzed",
        "segmentation.md": "not_analyzed",
    }
    # A file the analyzer could not parse never yields a clean finding set.
    assert not [f for f in result.findings if f.file == "acme-unsupported.cfg"]


def test_missing_or_failed_answers_never_read_as_clean():
    settings = assessment("corrected")
    prepared = prepare_exports(settings, raw_exports(settings))
    missing = summarize_network_config(
        settings, prepared, None, native_ref="unavailable", sanitized_refs={}
    )
    assert missing.status == "failed" and missing.checks == []
    assert {g.code for g in missing.gaps} == {"analyzer_unavailable"}
    native = json.loads((FIXTURES / "native-corrected.json").read_text())
    native["answers"]["filterLineReachability"] = {"error": "HTTPError"}
    failed = summarize_network_config(
        settings, prepared, native, native_ref="art_native", sanitized_refs={}
    )
    assert "acl-line-unreachable" not in failed.checks
    assert failed.status == "partial"


def test_summary_records_provenance_and_point_in_time_freshness():
    result = summary("corrected")
    file = result.file("edge-corrected.cfg")
    assert file.device == "edge-corrected" and file.supplier == "Fixture network team"
    assert (file.expires_at - file.exported_at).days == 90
    assert result.sensitivity == "confidential"
    assert "did not observe the running device" in result.provenance
    assert "Unverified" in result.qualification


def test_snapshot_holds_only_sanitized_device_configurations():
    settings = assessment("weakened")
    files = snapshot_files(settings, prepare_exports(settings, raw_exports(settings)))
    configs = sorted(p.rsplit("/", 1)[1] for p in files if "/snapshot/configs/" in p)
    assert configs == sorted(
        [
            "edge-weakened.cfg",
            "core-partial.cfg",
            "branch-legacy.cfg",
            "acme-unsupported.cfg",
        ]
    )
    blob = b"".join(files.values()).decode()
    assert not [secret for secret in SECRETS if secret in blob]


def inventory(reachability: str):
    return SimpleNamespace(
        endpoints=[
            SimpleNamespace(authority="10.10.10.20:23", reachability=reachability)
        ]
    )


def test_inventory_open_port_the_rules_deny_is_a_contradiction():
    corrected, weakened = summary("corrected"), summary("weakened")
    [row] = compare_with_inventory(corrected, inventory("open"))
    assert row["relation"] == "contradiction"
    assert row["artifactRefs"] == [corrected.file("edge-corrected.cfg").artifact_ref]
    assert compare_with_inventory(weakened, inventory("open"))[0]["relation"] == (
        "corroboration"
    )
    assert compare_with_inventory(corrected, inventory("refused"))[0]["relation"] == (
        "corroboration"
    )


def test_review_interpretations_cite_the_sanitized_export_and_lines():
    result = summary("corrected")
    file = result.file("edge-corrected.cfg")

    def review(locator, refs):
        return SimpleNamespace(
            interpretations=[
                SimpleNamespace(
                    subject=SimpleNamespace(asset=file.artifact_ref, locator=locator),
                    evidence_refs=refs,
                )
            ]
        )

    assert (
        review_locator_errors(
            review("edge-corrected.cfg:21-22", [file.sanitized_ref]), [result]
        )
        == []
    )
    assert review_locator_errors(
        review("edge-corrected.cfg", [file.sanitized_ref]), [result]
    )
    assert review_locator_errors(
        review("edge-corrected.cfg:999", [file.sanitized_ref]), [result]
    )
    assert review_locator_errors(review("edge-corrected.cfg:21", []), [result])


@pytest.mark.asyncio
async def test_worker_prepares_and_collects_without_publishing_secrets(tmp_path):
    from moonmind.config.container_backend_settings import (
        resolve_container_backend_settings,
    )
    from moonmind.workflows.temporal import container_job_attachments as attachments
    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
    )

    settings = assessment("weakened")
    spec = ContainerJobSpec.model_validate(build_network_config_job(settings))
    published: dict[str, bytes] = {}

    async def publish(request, name, data):
        published[name] = data
        return f"art_{len(published)}"

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=resolve_container_backend_settings({}),
        evidence_publisher=publish,
    )
    request = SimpleNamespace(
        request=SimpleNamespace(spec=spec),
        job_id="network-job",
        terminal_state="succeeded",
        ownership_token="owner",
    )
    storage = attachments.storage_for(tmp_path, "network-job")
    attachments.claim(storage, "owner")
    for index, export in enumerate(settings.exports):
        attachments.capture_bytes(
            (FIXTURES / export.file_name).read_bytes(),
            storage.inputs / f"network-export-{index}",
            "export",
        )
    await backend._prepare_network_config_assessment(request)
    root = storage.outputs / settings.output_directory
    snapshot = (root / "snapshot" / "configs" / "edge-weakened.cfg").read_text()
    assert "permit ip any any" in snapshot
    assert not [s for s in SECRETS if s in snapshot]
    assert (root / "runner.py").read_bytes().startswith(b'"""Batfish answers')
    # The analyzer cannot plant secrets or forge a summary: answers are
    # redacted again and the summary is regenerated by the worker.
    native = json.loads((FIXTURES / "native-weakened.json").read_text())
    native["answers"]["parseWarning"].append(
        {
            "Filename": "configs/core-partial.cfg",
            "Line": 3,
            "Text": "snmp-server community fixture-community-rw RW",
        }
    )
    (root / "native.json").write_text(json.dumps(native))
    (root / "summary.json").write_text('{"status": "complete"}')
    (root / "snapshot" / "configs" / "edge-weakened.cfg").chmod(0o644)
    (root / "snapshot" / "configs" / "edge-weakened.cfg").write_text(
        "snmp-server community fixture-community-rw RW\n"
    )
    manifest = await backend._collect_network_config_outputs(request)
    blob = b"".join(published.values()).decode()
    assert not [s for s in SECRETS if s in blob]
    names = sorted(published)
    assert "network-job-output-summary.json" in names
    assert "network-job-output-native.json" in names
    assert sum(name.endswith(".txt") for name in names) == len(settings.exports)
    result = json.loads(published["network-job-output-summary.json"])
    assert result["status"] == "partial"
    assert {f["ruleId"] for f in result["findings"]} == set(RULES)
    assert all(item.collection_status == "collected" for item in manifest)
    exported = datetime(2026, 11, 27, 8, tzinfo=UTC)
    assert result["files"][0]["exportedAt"].startswith(exported.date().isoformat())
