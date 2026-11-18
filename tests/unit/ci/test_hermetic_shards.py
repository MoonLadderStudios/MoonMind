"""Two physical owners must prove one complete hermetic execution."""

import json

import pytest

from tools.ci import hermetic_shards as subject


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    root = tmp_path / "owners"
    nodes = [
        "tests/integration/test_a.py::test_a",
        "tests/integration/test_b.py::test_b",
    ]
    universe = nodes + list(subject.STORAGE.values())
    manifest = tmp_path / "ownership.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "moonmind.test-ownership/v1",
                "revision": "abc",
                "runId": "123",
                "attempt": "1",
                "nodes": [
                    {"nodeid": node, "eligible": True, "owners": ["integration-ci"]}
                    for node in universe
                ],
            }
        )
    )
    directories = {}
    for shard in (1, 2):
        directory = root / f"integration-ci-hermetic-shard-{shard}-attempt-1" / "report"
        directory.mkdir(parents=True)
        identity = {
            "revision": "abc",
            "runId": "123",
            "attempt": "1",
            "suite": f"integration-hermetic-shard-{shard}",
        }
        phases = {"main.receipt.json": nodes[shard - 1]}
        if shard == 1:
            phases.update(subject.STORAGE)
            (directory / "qualification.json").write_text(
                json.dumps(
                    {
                        **identity,
                        "status": "passed",
                        "phases": ["docker-proxy", "scoped-egress"],
                    }
                )
            )
        for name, node in phases.items():
            (directory / name).write_text(
                json.dumps({**identity, "expectedNodeIds": [node]})
            )
        case = directory / "security-quality" / f"case-{shard}"
        case.mkdir(parents=True)
        (case / "result.json").write_text(
            json.dumps(
                {
                    "executionIdentity": identity,
                    "runIdentity": f"uid-{shard}",
                    "case": {"id": f"case-{shard}"},
                }
            )
        )
        directories[shard] = directory

    def validate(path, **identity):
        result = json.loads(path.read_text())
        assert result["revision"] == identity["revision"]
        assert result["runId"] == identity["run_id"]
        assert result["attempt"] == identity["attempt"]
        if result.get("failed"):
            raise ValueError("failed receipt")
        return result

    monkeypatch.setattr(subject, "validate_receipt", validate)
    from tests.integration.security import quality

    cases_manifest = tmp_path / "quality-cases.json"
    cases_manifest.write_text(
        json.dumps(
            {"cases": [{"id": name} for name in ("case-1", "case-2", "case-other")]}
        )
    )
    monkeypatch.setattr(quality, "MANIFEST", cases_manifest)

    def summarize(directory, *, execution_identities, case_ids=None):
        assert len(list(directory.glob("*/result.json"))) == (
            len(case_ids) if case_ids is not None else 2
        )
        assert len(execution_identities) == (1 if case_ids is not None else 2)
        return {"passed": True}

    monkeypatch.setattr(quality, "summarize", summarize)
    return dict(
        root=root,
        ownership_manifest=manifest,
        revision="abc",
        run_id="123",
        attempt="2",
        ownership_attempt="1",
        output=tmp_path / "quality",
    ), directories


def rewrite(path, **updates):
    payload = json.loads(path.read_text())
    payload.update(updates)
    path.write_text(json.dumps(payload))


def test_complete_execution_and_retained_attempts(evidence):
    args, _ = evidence
    assert subject.aggregate(**args)["passed"]
    assert (args["output"] / "summary.json").is_file()


@pytest.mark.parametrize(
    "name",
    ["main.receipt.json", "storage-seed.receipt.json", "storage-verify.receipt.json"],
)
def test_missing_phase_cannot_pass(evidence, name):
    args, dirs = evidence
    (dirs[1] / name).unlink()
    with pytest.raises(ValueError, match="phase receipts"):
        subject.aggregate(**args)


def test_missing_shard_cannot_pass(evidence):
    args, dirs = evidence
    import shutil

    shutil.rmtree(dirs[2].parent)
    with pytest.raises(ValueError, match="Both hermetic"):
        subject.aggregate(**args)


def test_newer_incomplete_attempt_cannot_borrow_old_receipts(evidence):
    args, _ = evidence
    (args["root"] / "integration-ci-hermetic-shard-1-attempt-2").mkdir()
    with pytest.raises(ValueError, match="phase receipts"):
        subject.aggregate(**args)


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("overlap", "Duplicate hermetic"),
        ("missing-node", "corpus mismatch"),
        ("failure", "failed receipt"),
        ("suite", "suite differs"),
        ("quality-identity", "another shard"),
        ("quality-unknown", "Unknown security"),
        ("qualification", "qualification failed"),
        ("storage-owner", "phase receipts"),
    ],
)
def test_fail_closed(evidence, mutation, match):
    args, dirs = evidence
    if mutation == "overlap":
        rewrite(
            dirs[2] / "main.receipt.json",
            expectedNodeIds=["tests/integration/test_a.py::test_a"],
        )
    elif mutation == "missing-node":
        rewrite(dirs[2] / "main.receipt.json", expectedNodeIds=[])
    elif mutation == "failure":
        rewrite(dirs[2] / "main.receipt.json", failed=True)
    elif mutation == "suite":
        rewrite(dirs[2] / "main.receipt.json", suite="integration-hermetic-shard-1")
    elif mutation == "quality-identity":
        rewrite(dirs[2] / "security-quality/case-2/result.json", executionIdentity={})
    elif mutation == "quality-unknown":
        rewrite(dirs[2] / "security-quality/case-2/result.json", case={"id": "unknown"})
    elif mutation == "qualification":
        rewrite(dirs[1] / "qualification.json", status="failed")
    elif mutation == "storage-owner":
        (dirs[2] / "storage-seed.receipt.json").write_text(
            (dirs[1] / "storage-seed.receipt.json").read_text()
        )
    with pytest.raises(ValueError, match=match):
        subject.aggregate(**args)


def test_other_run_manifest_is_rejected(evidence):
    args, _ = evidence
    rewrite(args["ownership_manifest"], runId="other")
    with pytest.raises(ValueError, match="another source"):
        subject.aggregate(**args)


def test_future_artifact_is_not_admitted(evidence):
    args, _ = evidence
    (args["root"] / "integration-ci-hermetic-shard-1-attempt-3").mkdir()
    with pytest.raises(ValueError, match="not admitted"):
        subject.aggregate(**args)


def test_quality_failure_is_not_published(evidence, monkeypatch):
    args, _ = evidence
    from tests.integration.security import quality

    monkeypatch.setattr(quality, "summarize", lambda *a, **kw: {"passed": False})
    with pytest.raises(ValueError, match="quality aggregate failed"):
        subject.aggregate(**args)
    assert not args["output"].exists()


def test_duplicate_receipt_is_not_silently_selected(evidence):
    args, dirs = evidence
    nested = dirs[1] / "duplicate"
    nested.mkdir()
    (nested / "main.receipt.json").write_bytes(
        (dirs[1] / "main.receipt.json").read_bytes()
    )
    with pytest.raises(ValueError, match="phase receipts"):
        subject.aggregate(**args)


def test_mixed_quality_runs_within_shard_fail(evidence):
    args, dirs = evidence
    source = dirs[1] / "security-quality/case-1/result.json"
    second = source.parent.parent / "case-other"
    second.mkdir()
    (second / "result.json").write_bytes(source.read_bytes())
    rewrite(second / "result.json", case={"id": "case-other"}, runIdentity="other-run")
    with pytest.raises(ValueError, match="Mixed security"):
        subject.aggregate(**args)


@pytest.mark.parametrize(
    "contents", [None, "not-json", "{}", '{"bad":1}', '{"x::y":-1}', '{"x::y":NaN}']
)
def test_bad_or_missing_hints_keep_no_history_partition(tmp_path, contents):
    path = tmp_path / "hints.json"
    if contents is not None:
        path.write_text(contents)
    assert subject.durations_pytest_args(path) == []


def test_valid_stale_hint_is_advisory_not_selection(tmp_path):
    path = tmp_path / "hints.json"
    path.write_text('{"tests/integration/removed.py::test_old":3.5}')
    assert subject.durations_pytest_args(path) == ["--durations-path", str(path)]


@pytest.mark.parametrize(
    "damage",
    [None, "receipt", "retained-evidence", "valid-repetition", "corrupt-repetition"],
)
def test_real_receipt_and_quality_owners_admit_complete_artifacts(tmp_path, damage):
    """Exercise both real validators, including the full current quality manifest.

    Observations here are synthetic validator inputs, not execution evidence for
    the application. This proves artifact admission, not the security scenarios.
    """
    import hashlib
    import sys

    from tests.integration.security import quality
    from tools.ci.write_backend_matrix_summary import (
        environment_fingerprint,
        environment_path,
        observed_environment,
    )

    root = tmp_path / "artifacts"
    identity = {"revision": "abc", "runId": "123", "attempt": "1"}
    main_nodes = ["tests/integration/a.py::test_a", "tests/integration/b.py::test_b"]
    universe = main_nodes + list(subject.STORAGE.values())
    ownership = tmp_path / "ownership.json"
    ownership.write_text(
        json.dumps(
            {
                "schema": "moonmind.test-ownership/v1",
                **identity,
                "nodes": [
                    {"nodeid": node, "eligible": True, "owners": ["integration-ci"]}
                    for node in universe
                ],
            }
        )
    )
    environment = observed_environment()
    fingerprint = environment_fingerprint(environment)
    directories = {}
    for shard in (1, 2):
        directory = root / f"integration-ci-hermetic-shard-{shard}-attempt-1"
        directory.mkdir(parents=True)
        directories[shard] = directory
        current = {**identity, "suite": f"integration-hermetic-shard-{shard}"}
        phases = {"main.receipt.json": main_nodes[shard - 1]}
        if shard == 1:
            phases.update(subject.STORAGE)
            (directory / "qualification.json").write_text(
                json.dumps(
                    {
                        **current,
                        "status": "passed",
                        "phases": ["docker-proxy", "scoped-egress"],
                    }
                )
            )
        for name, node in phases.items():
            path = directory / name
            path.write_text(
                json.dumps(
                    {
                        "schema": "moonmind.pytest-verification/v1",
                        **current,
                        "status": "passed",
                        "exitCode": 0,
                        "errors": [],
                        **environment,
                        "arguments": [node],
                        "environmentFingerprint": fingerprint,
                        "expectedNodeIds": [node],
                        "cases": [
                            {
                                "nodeid": node,
                                "outcome": "passed",
                                "phases": {
                                    phase: {"outcome": "passed", "seconds": 0.01}
                                    for phase in ("setup", "call", "teardown")
                                },
                            }
                        ],
                    }
                )
            )
            environment_path(path).write_text(
                json.dumps(
                    {
                        "schema": "moonmind.pytest-environment/v1",
                        **current,
                        "environment": environment,
                        "fingerprint": fingerprint,
                        "expectedPython": f"{sys.version_info.major}.{sys.version_info.minor}",
                        "pytestArguments": [node],
                    }
                )
            )
    manifest_bytes = quality.MANIFEST.read_bytes()
    cases = json.loads(manifest_bytes)["cases"]
    for index, case in enumerate(cases):
        shard = index % 2 + 1
        directory = directories[shard] / "security-quality" / case["id"]
        directory.mkdir(parents=True)
        body = b"synthetic admission fixture, not scenario execution"
        (directory / "evidence.txt").write_bytes(body)
        observed = {
            **case["expected"],
            "executionEvidence": ["native"],
            "evidenceByFinding": {
                finding: ["native"] for finding in case["expected"]["findings"]
            },
        }
        evaluation = quality.evaluate(case["expected"], observed, {"native": body})
        assert evaluation["passed"]
        mutation = {**observed, "outcome": "intentionally-wrong"}
        (directory / "result.json").write_text(
            json.dumps(
                {
                    "case": case,
                    "manifestDigest": "sha256:"
                    + hashlib.sha256(manifest_bytes).hexdigest(),
                    "executionIdentity": {
                        **identity,
                        "suite": f"integration-hermetic-shard-{shard}",
                    },
                    "runIdentity": f"uid-{shard}",
                    "observed": observed,
                    "metrics": evaluation["metrics"],
                    "repeatObserved": observed,
                    "mutationChecks": {
                        name: {"observed": mutation}
                        for name in case.get("requiredMutations", [])
                    },
                    "retainedArtifacts": {
                        "native": {
                            "path": "evidence.txt",
                            "sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
                        }
                    },
                }
            )
        )
    if damage in {"valid-repetition", "corrupt-repetition"}:
        import shutil

        source = directories[1] / "security-quality" / cases[0]["id"]
        target = directories[2] / "security-quality" / cases[0]["id"]
        shutil.copytree(source, target)
        rewrite(
            target / "result.json",
            executionIdentity={**identity, "suite": "integration-hermetic-shard-2"},
            runIdentity="uid-2",
        )
        if damage == "corrupt-repetition":
            (target / "evidence.txt").write_bytes(b"tampered repetition")
    if damage == "receipt":
        rewrite(directories[2] / "main.receipt.json", environmentFingerprint="tampered")
    elif damage == "retained-evidence":
        next(directories[1].rglob("evidence.txt")).write_bytes(b"tampered")
    args = dict(
        root=root,
        ownership_manifest=ownership,
        revision="abc",
        run_id="123",
        attempt="1",
        ownership_attempt="1",
        output=tmp_path / "quality",
    )
    if damage and damage != "valid-repetition":
        with pytest.raises(
            ValueError, match="Invalid verification receipt|quality aggregate failed"
        ):
            subject.aggregate(**args)
        assert not args["output"].exists()
    else:
        result = subject.aggregate(**args)
        assert result["passed"]
        assert len(result["cases"]) == len(cases)
        assert len(result["receipts"]) == 4
        if damage == "valid-repetition":
            assert (
                args["output"] / "repetitions/shard-2" / cases[0]["id"] / "result.json"
            ).is_file()


def test_duplicate_case_within_one_shard_is_rejected(evidence):
    args, dirs = evidence
    source = dirs[1] / "security-quality/case-1/result.json"
    duplicate = source.parent.parent / "other-directory"
    duplicate.mkdir()
    (duplicate / "result.json").write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match="Duplicate security case within shard"):
        subject.aggregate(**args)
