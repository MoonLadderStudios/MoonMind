"""The in-image endpoint runner: unpacking, offline narrowing and evaluation (#5439)."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import tarfile
from pathlib import Path

import pytest

from moonmind.schemas.repository_scanner_models import EndpointAssessment
from moonmind.security import endpoint_assessment_runner as runner
from moonmind.security.endpoint_assessments import (
    EndpointExecution,
    parse_results,
    summarize,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests/fixtures/security/endpoint"
ROOTFS = ROOT / "services/repository-security/fixtures/endpoint"
RULE = "xccdf_org.moonmind.fixture_rule_"
PROFILE = "xccdf_org.moonmind.fixture_profile_baseline"


def add(bundle, name, kind="file", data=b"x", link=None):
    info = tarfile.TarInfo(name)
    if kind == "file":
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
        return
    info.type = {
        "dir": tarfile.DIRTYPE,
        "sym": tarfile.SYMTYPE,
        "hard": tarfile.LNKTYPE,
        "chr": tarfile.CHRTYPE,
        "fifo": tarfile.FIFOTYPE,
    }[kind]
    if link:
        info.linkname = link
    bundle.addfile(info)


def tree(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*")}


def test_rootfs_unpacking_refuses_escapes_and_skips_devices(tmp_path):
    archive = tmp_path / "rootfs.tar"
    with tarfile.open(archive, "w") as bundle:
        add(bundle, ".", "dir")
        add(bundle, "etc", "dir")
        add(
            bundle,
            "etc/os-release",
            data=b'ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian 12"\n',
        )
        add(bundle, "/etc/absolute", data=b"kept inside the copy")
        add(bundle, "../escape")
        add(bundle, "dev/null", "chr")
        add(bundle, "run/pipe", "fifo")
        add(bundle, "etc/to-host", "sym", link="/etc/passwd")
        add(bundle, "etc/hard-out", "hard", link="../../outside")
        add(bundle, "etc/up", "sym", link="../../..")
        add(bundle, "etc/up/written-outside")
        add(bundle, "etc/shadow", data=b"root:*:1::::::\n")
    bundle_info = tarfile.open(archive).getmember("etc/shadow")
    assert bundle_info.mode & 0o400
    root = tmp_path / "root"
    root.mkdir()
    record = runner.unpack_input("rootfs", archive, root, 1 << 20)
    assert tree(root) == {
        "etc",
        "etc/os-release",
        "etc/absolute",
        "etc/to-host",
        "etc/up",
        "etc/shadow",
    }
    assert record.special_files_skipped == 2
    assert record.unsafe_members_refused == 3
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "outside").exists()
    # Symlinks keep their absolute target: it means the copy's /etc/passwd.
    assert os.readlink(root / "etc/to-host") == "/etc/passwd"
    os_release = runner.read_os_release(root)
    assert (os_release.id, os_release.version_id) == ("debian", "12")


def test_unpacking_is_bounded(tmp_path):
    archive = tmp_path / "rootfs.tar"
    with tarfile.open(archive, "w") as bundle:
        add(bundle, "big", data=b"x" * 2048)
    (tmp_path / "root").mkdir()
    with pytest.raises(runner.InputError, match="input_bound_exceeded"):
        runner.unpack_input("rootfs", archive, tmp_path / "root", 1024)


def layer(entries, *, compress=False) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        for entry in entries:
            add(bundle, *entry)
    return gzip.compress(buffer.getvalue()) if compress else buffer.getvalue()


LOWER = [
    ("etc", "dir"),
    ("etc/a", "file", b"a"),
    ("etc/b", "file", b"b"),
    ("opt", "dir"),
    ("opt/x",),
    ("opt/y",),
]
UPPER = [
    ("etc/.wh.a",),
    ("opt/.wh..wh..opq",),
    ("opt/z", "file", b"z"),
    ("etc/c", "file", b"c"),
]


@pytest.mark.parametrize("layout", ["docker", "oci"])
def test_image_layers_apply_in_order_with_whiteouts(tmp_path, layout):
    archive = tmp_path / "image.tar"
    lower, upper = layer(LOWER), layer(UPPER, compress=layout == "oci")
    with tarfile.open(archive, "w") as bundle:
        if layout == "docker":
            add(bundle, "l1/layer.tar", data=lower)
            add(bundle, "l2/layer.tar", data=upper)
            add(
                bundle,
                "manifest.json",
                data=json.dumps(
                    [{"Config": "c.json", "Layers": ["l1/layer.tar", "l2/layer.tar"]}]
                ).encode(),
            )
        else:
            digests = []
            for body in (lower, upper):
                digest = hashlib.sha256(body).hexdigest()
                digests.append(digest)
                add(bundle, f"blobs/sha256/{digest}", data=body)
            manifest = json.dumps(
                {"layers": [{"digest": f"sha256:{d}"} for d in digests]}
            ).encode()
            manifest_digest = hashlib.sha256(manifest).hexdigest()
            add(bundle, f"blobs/sha256/{manifest_digest}", data=manifest)
            add(
                bundle,
                "index.json",
                data=json.dumps(
                    {"manifests": [{"digest": f"sha256:{manifest_digest}"}]}
                ).encode(),
            )
            add(bundle, "oci-layout", data=b'{"imageLayoutVersion":"1.0.0"}')
    root = tmp_path / "root"
    root.mkdir()
    record = runner.unpack_input("image", archive, root, 1 << 20)
    assert record.layers == 2
    assert tree(root) == {"etc", "etc/b", "etc/c", "opt", "opt/z"}


def test_multi_image_archives_are_refused(tmp_path):
    archive = tmp_path / "image.tar"
    with tarfile.open(archive, "w") as bundle:
        add(bundle, "manifest.json", data=json.dumps([{}, {}]).encode())
    (tmp_path / "root").mkdir()
    with pytest.raises(runner.InputError, match="image_archive_not_single_image"):
        runner.unpack_input("image", archive, tmp_path / "root", 1 << 20)


def datastream(path: Path) -> Path:
    """The fixture benchmark and its OVAL in one document, as a datastream holds them."""
    parts = [
        (FIXTURES / name).read_text().split("?>", 1)[1]
        for name in ("fixture-xccdf.xml", "fixture-oval.xml")
    ]
    extra = """
<oval_definitions xmlns="http://oval.mitre.org/XMLSchema/oval-definitions-5" xmlns:ind="http://oval.mitre.org/XMLSchema/oval-definitions-5#independent" xmlns:unix="http://oval.mitre.org/XMLSchema/oval-definitions-5#unix">
  <definitions>
    <definition class="compliance" id="oval:x:def:extends" version="1"><criteria><extend_definition definition_ref="oval:org.moonmind.fixture:def:6"/></criteria></definition>
    <definition class="compliance" id="oval:x:def:proc" version="1"><criteria><criterion test_ref="oval:x:tst:proc"/></criteria></definition>
    <definition class="compliance" id="oval:x:def:var" version="1"><criteria><criterion test_ref="oval:x:tst:var"/></criteria></definition>
  </definitions>
  <tests>
    <ind:textfilecontent54_test id="oval:x:tst:proc" version="1" check="all"><ind:object object_ref="oval:x:obj:proc"/></ind:textfilecontent54_test>
    <ind:textfilecontent54_test id="oval:x:tst:var" version="1" check="all"><ind:object object_ref="oval:x:obj:var"/></ind:textfilecontent54_test>
  </tests>
  <objects>
    <ind:textfilecontent54_object id="oval:x:obj:proc" version="1"><ind:filepath>/proc/cmdline</ind:filepath><ind:pattern operation="pattern match">audit=1</ind:pattern><ind:instance datatype="int">1</ind:instance></ind:textfilecontent54_object>
    <ind:textfilecontent54_object id="oval:x:obj:var" version="1"><ind:filepath var_ref="oval:x:var:1"/><ind:pattern operation="pattern match">x</ind:pattern><ind:instance datatype="int">1</ind:instance></ind:textfilecontent54_object>
    <unix:process58_object id="oval:x:obj:pids" version="1"><unix:command_line>sshd</unix:command_line><unix:pid datatype="int" operation="greater than">0</unix:pid></unix:process58_object>
  </objects>
  <variables>
    <local_variable id="oval:x:var:1" version="1" datatype="string"><object_component item_field="command_line" object_ref="oval:x:obj:pids"/></local_variable>
  </variables>
</oval_definitions>"""
    rules = """
<Benchmark xmlns="http://checklists.nist.gov/xccdf/1.2" id="xccdf_org.x_benchmark_extra">
  <Rule id="xccdf_org.x_rule_extends"><check system="http://oval.mitre.org/XMLSchema/oval-definitions-5"><check-content-ref name="oval:x:def:extends"/></check></Rule>
  <Rule id="xccdf_org.x_rule_proc"><check system="http://oval.mitre.org/XMLSchema/oval-definitions-5"><check-content-ref name="oval:x:def:proc"/></check></Rule>
  <Rule id="xccdf_org.x_rule_var"><check system="http://oval.mitre.org/XMLSchema/oval-definitions-5"><check-content-ref name="oval:x:def:var"/></check></Rule>
</Benchmark>"""
    path.write_text("<collection>" + "".join(parts) + extra + rules + "</collection>")
    return path


def test_rules_needing_live_state_or_file_metadata_are_classified(tmp_path):
    found = runner.classify_offline_rules(datastream(tmp_path / "ds.xml"))
    assert found == {
        RULE + "process": "runtime",
        RULE + "sysctl": "runtime",
        RULE + "shadow_owner": "metadata",
        # Through an extended definition, a /proc path and a variable that
        # reads live processes.
        "xccdf_org.x_rule_extends": "runtime",
        "xccdf_org.x_rule_proc": "runtime",
        "xccdf_org.x_rule_var": "runtime",
    }


@pytest.fixture
def content(tmp_path):
    directory = tmp_path / "content"
    directory.mkdir()
    stream = datastream(directory / "ssg-debian12-ds.xml")
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "release": "0.1.82",
                "datastreams": {
                    "ssg-debian12-ds.xml": hashlib.sha256(
                        stream.read_bytes()
                    ).hexdigest()
                },
            }
        )
    )
    return directory


class FakeOscap:
    """Stands in for OpenSCAP and records how the runner invoked it."""

    def __init__(self, native: Path | None):
        self.native, self.calls = native, []

    def __call__(self, argv, *, cwd, env, capture=None, errors=None, **_):
        self.calls.append((argv, env))
        if argv == ["oscap", "--version"]:
            capture.write_text("OpenSCAP command line tool (oscap) 1.3.14\n")
            return 0, "succeeded"
        assert argv[:3] == ["oscap", "xccdf", "eval"]
        root = Path(env["OSCAP_PROBE_ROOT"])
        assert (root / "etc/apt/sources.list").is_file()
        shutil.copyfile(self.native, argv[argv.index("--results-arf") + 1])
        return 2, "succeeded"

    def install(self, monkeypatch):
        monkeypatch.setattr(
            runner.BoundedProcesses,
            "run",
            lambda _, *args, **kwargs: self(*args, **kwargs),
        )
        return self


def evaluate(
    tmp_path,
    monkeypatch,
    content,
    fixture="hardened",
    native="hardened-arf.xml",
    **changes,
):
    inputs = tmp_path / f"inputs-{fixture}"
    (inputs / "endpoint").mkdir(parents=True)
    with tarfile.open(inputs / "endpoint/rootfs.tar", "w") as bundle:
        bundle.add(ROOTFS / fixture, arcname=".")
    fake = FakeOscap(FIXTURES / native).install(monkeypatch)
    assessment = EndpointAssessment.model_validate(
        {
            "target": "rootfs",
            "asset": "web-01",
            "inputArtifactRef": "art_rootfs",
            "datastream": "ssg-debian12-ds.xml",
            "profile": PROFILE,
            **changes,
        }
    )
    output = tmp_path / f"out-{fixture}"
    code = runner.run_endpoint_assessment(
        assessment, inputs=inputs, output=output, content=content
    )
    execution = EndpointExecution.model_validate_json(
        (output / "execution.json").read_text()
    )
    return code, assessment, execution, output, fake


def test_evaluation_runs_only_oscap_eval_offline_and_retains_checked_evidence(
    tmp_path, monkeypatch, content
):
    code, assessment, execution, output, fake = evaluate(tmp_path, monkeypatch, content)
    assert code == 0 and execution.limitations == []
    argv, env = fake.calls[-1]
    assert argv == [
        "oscap",
        "xccdf",
        "eval",
        "--profile",
        PROFILE,
        "--results-arf",
        argv[-2],
        str(content / "ssg-debian12-ds.xml"),
    ]
    assert set(env) == {"PATH", "HOME", "TMPDIR", "OSCAP_PROBE_ROOT"}
    assert (execution.version, execution.exit_code, execution.state) == (
        "1.3.14",
        2,
        "succeeded",
    )
    assert execution.content_release == "0.1.82"
    assert (
        execution.content_digest
        == hashlib.sha256((content / "ssg-debian12-ds.xml").read_bytes()).hexdigest()
    )
    assert (
        execution.input_digest
        == hashlib.sha256(
            (tmp_path / "inputs-hardened/endpoint/rootfs.tar").read_bytes()
        ).hexdigest()
    )
    assert (execution.os_release.id, execution.os_release.version_id) == (
        "debian",
        "12",
    )
    assert execution.offline_rules == {
        RULE + "process": "runtime",
        RULE + "sysctl": "runtime",
        RULE + "shadow_owner": "metadata",
    }
    retained = (output / "results.xml").read_bytes()
    assert b"data-stream-collection" not in retained and b"oval_results" not in retained
    summary = summarize(
        assessment, execution, parse_results(retained, 1 << 25), native_ref="art"
    )
    failed = {r.rule_id for r in summary.rules if r.state == "failed"}
    assert failed == set()
    _, _, weak_execution, weak_output, _ = evaluate(
        tmp_path, monkeypatch, content, fixture="weakened", native="weakened-arf.xml"
    )
    weak = summarize(
        assessment,
        weak_execution,
        parse_results((weak_output / "results.xml").read_bytes(), 1 << 25),
        native_ref="art",
    )
    assert {r.rule_id for r in weak.rules if r.state == "failed"} == {
        RULE + "apt_unauthenticated",
        RULE + "sources_official",
    }


def test_tailoring_is_passed_to_oscap_and_identified(tmp_path, monkeypatch, content):
    tailoring = tmp_path / "inputs-hardened/tailoring/tailoring.xml"
    tailoring.parent.mkdir(parents=True)
    tailoring.write_text("<Tailoring/>")
    _, _, execution, _, fake = evaluate(
        tmp_path, monkeypatch, content, tailoringArtifactRef="art_tailoring"
    )
    argv, _ = fake.calls[-1]
    assert argv[argv.index("--tailoring-file") + 1] == str(tailoring)
    assert execution.tailoring_digest == hashlib.sha256(b"<Tailoring/>").hexdigest()


def test_changed_shipped_content_is_refused_before_evaluation(
    tmp_path, monkeypatch, content
):
    (content / "ssg-debian12-ds.xml").write_text("<collection/>")
    code, _, execution, output, fake = evaluate(tmp_path, monkeypatch, content)
    assert code == 2
    assert {"content_digest_mismatch", "scanner_prerequisite_failed"} <= set(
        execution.limitations
    )
    assert not any(argv[:3] == ["oscap", "xccdf", "eval"] for argv, _ in fake.calls)
    assert not (output / "results.xml").exists()


def test_imported_results_are_checked_without_running_oscap(tmp_path, monkeypatch):
    inputs = tmp_path / "inputs"
    (inputs / "endpoint").mkdir(parents=True)
    shutil.copyfile(FIXTURES / "hardened-arf.xml", inputs / "endpoint/results.xml")
    fake = FakeOscap(None).install(monkeypatch)
    assessment = EndpointAssessment.model_validate(
        {"target": "results", "asset": "web-01", "inputArtifactRef": "art_arf"}
    )
    code = runner.run_endpoint_assessment(
        assessment, inputs=inputs, output=tmp_path / "out"
    )
    execution = EndpointExecution.model_validate_json(
        (tmp_path / "out/execution.json").read_text()
    )
    assert code == 0 and fake.calls == []
    assert (execution.state, execution.version, execution.offline_rules) == (
        "succeeded",
        "not_run",
        {},
    )
    retained = (tmp_path / "out/results.xml").read_bytes()
    assert b"<target>unknown</target>" in retained
    assert b"data-stream-collection" not in retained


def test_readiness_needs_oscap_and_the_selected_content(
    tmp_path, monkeypatch, content, capsys
):
    FakeOscap(None).install(monkeypatch)
    evaluate_admission = EndpointAssessment.model_validate(
        {
            "target": "rootfs",
            "asset": "web-01",
            "inputArtifactRef": "art_rootfs",
            "datastream": "ssg-debian12-ds.xml",
            "profile": PROFILE,
        }
    )
    assert runner.readiness(evaluate_admission, content=content) == 0
    assert "1.3.14" in capsys.readouterr().out
    (content / "manifest.json").write_text("{}")
    assert runner.readiness(evaluate_admission, content=content) == 2


def test_invocation_errors_never_echo_input(capsys):
    assert (
        runner.main(["runner", '{"target": "rootfs", "secret": "s3cr3t"}', "/tmp/x"])
        == 2
    )
    out = capsys.readouterr().out
    assert "s3cr3t" not in out and "invocation failed" in out
