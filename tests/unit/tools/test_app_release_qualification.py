"""Exercise the real release writer with a disposable registry/API substitute."""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
IMAGE = "ghcr.io/fixture/moonmind"
CANDIDATE = IMAGE + "@sha256:" + "a" * 64
PREVIOUS = IMAGE + "@sha256:" + "b" * 64


def _workflow(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def _writer():
    jobs = _workflow("docker-publish.yml")["jobs"]
    writers = [
        (name, job, step)
        for name, job in jobs.items()
        for step in job.get("steps", [])
        if "imagetools create" in step.get("run", "") and ":latest" in step["run"]
    ]
    assert len(writers) == 1, "One serialized owner must advance the default channel"
    return writers[0]


def _ancestors(jobs: dict, name: str) -> set[str]:
    needs = jobs[name].get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    return set(needs).union(*(_ancestors(jobs, dependency) for dependency in needs))


def _channel_updates(commands):
    return [
        command
        for command in commands
        if command[:3] == ["buildx", "imagetools", "create"]
        and IMAGE + ":latest" in command
    ]


def _image_value(expression, candidate):
    """Resolve the workflow's image input/fallback without executing expressions."""
    match = re.fullmatch(r"\$\{\{\s*(.*?)\s*\}\}", expression)
    if match is None:
        return expression
    parts = match[1].split("||", 1)
    assert parts[0].strip() == "inputs.candidate_image"
    return candidate or (ast.literal_eval(parts[1].strip()) if len(parts) == 2 else "")


def _qualification_context(
    *, assembly="success", source="success", migration="success"
):
    jobs = _workflow("docker-publish.yml")["jobs"]
    context = {}
    for name, job in jobs.items():
        if job.get("uses") == "./.github/workflows/pytest-unit-tests.yml":
            context[name] = {"result": source}
        elif job.get("uses") == "./.github/workflows/alembic-migration-gate.yml":
            context[name] = {"result": migration}
        elif "image_ref" in job.get("outputs", {}):
            context[name] = {"result": assembly, "outputs": {"image_ref": CANDIDATE}}
        elif "image_name" in job.get("outputs", {}):
            context[name] = {
                "result": "success",
                "outputs": {"image_name": IMAGE, "version_tag": "fixture-version"},
            }
    return context


def _run_writer(
    tmp_path,
    *,
    assembly="success",
    source="success",
    migration="success",
    ref="refs/heads/main",
    main_head="candidate-commit",
    candidate=CANDIDATE,
    step=None,
):
    writer = step if step is not None else _writer()[2]
    needs = _qualification_context(
        assembly=assembly, source=source, migration=migration
    )
    for value in needs.values():
        if "image_ref" in value.get("outputs", {}):
            value["outputs"]["image_ref"] = candidate

    def expression(match):
        path = match.group(1).strip().split(".")
        value = {"needs": needs, "github": {"token": "fixture"}}
        for part in path:
            value = value.get(part, "") if isinstance(value, dict) else ""
        return str(value)

    def render(value):
        return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", expression, value)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "docker-calls.jsonl"
    channel = tmp_path / "latest"
    channel.write_text(PREVIOUS)
    docker = bin_dir / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['DOCKER_CALLS'], 'a') as output: output.write(json.dumps(args) + '\\n')\n"
        "if args[:3] == ['buildx', 'imagetools', 'create'] and os.environ['IMAGE_NAME'] + ':latest' in args:\n"
        "    Path(os.environ['CHANNEL_PATH']).write_text(args[-1])\n"
        "if args[:3] == ['buildx', 'imagetools', 'inspect']: print('sha256:' + 'a' * 64)\n"
    )
    docker.chmod(0o755)
    gh = bin_dir / "gh"
    gh.write_text('#!/bin/sh\nprintf "%s\\n" "$MAIN_HEAD"\n')
    gh.chmod(0o755)
    digests = tmp_path / "digests"
    digests.mkdir()
    (digests / ("c" * 64)).touch()
    environment = {
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "MAIN_HEAD": main_head,
        "GITHUB_REF": ref,
        "GITHUB_SHA": "candidate-commit",
        "GITHUB_REPOSITORY": "fixture/moonmind",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
        "IMAGE_NAME": IMAGE,
        "DOCKER_CALLS": str(calls),
        "CHANNEL_PATH": str(channel),
        **{key: render(value) for key, value in writer.get("env", {}).items()},
    }
    result = subprocess.run(
        ["bash", "-c", render(writer["run"])],
        cwd=digests,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    commands = (
        [json.loads(line) for line in calls.read_text().splitlines()]
        if calls.exists()
        else []
    )
    return result, channel.read_text(), commands


@pytest.mark.parametrize("gate", ["assembly", "source", "migration"])
@pytest.mark.parametrize(
    "outcome", ["failure", "cancelled", "timed_out", "skipped", ""]
)
def test_non_success_or_missing_qualification_cannot_mutate_latest(
    tmp_path, gate, outcome
):
    result, channel, commands = _run_writer(tmp_path, **{gate: outcome})
    assert channel == PREVIOUS, (
        f"Unqualified image replaced the usable channel: {commands}"
    )
    assert not _channel_updates(commands), (
        "The final writer must refuse the channel mutation"
    )
    assert result.returncode != 0


@pytest.mark.parametrize(
    ("ref", "main_head", "publish"),
    [
        ("refs/heads/main", "candidate-commit", True),
        ("refs/heads/main", "newer-commit", False),
        ("refs/heads/feature", "candidate-commit", False),
    ],
)
def test_only_qualified_current_main_promotes_the_exact_tested_manifest(
    tmp_path, ref, main_head, publish
):
    result, channel, commands = _run_writer(tmp_path, ref=ref, main_head=main_head)
    assert result.returncode == 0, result.stderr
    if publish:
        assert channel == CANDIDATE
        updates = _channel_updates(commands)
        assert len(updates) == 1
        assert [arg for arg in updates[0] if arg.startswith(IMAGE + "@")] == [CANDIDATE]
        assert any(
            updates[0][index - 1] in {"-t", "--tag"}
            for index, arg in enumerate(updates[0])
            if arg == IMAGE + ":latest"
        )
    else:
        assert channel == PREVIOUS
        assert not _channel_updates(commands)


@pytest.mark.parametrize(
    "candidate", ["", IMAGE + ":latest", "ghcr.io/other/app@sha256:" + "a" * 64]
)
def test_mutable_missing_or_foreign_candidate_is_not_published(tmp_path, candidate):
    result, channel, commands = _run_writer(tmp_path, candidate=candidate)
    assert result.returncode != 0
    assert channel == PREVIOUS
    assert not _channel_updates(commands)


def test_default_writer_depends_on_existing_source_migration_and_image_owners():
    jobs = _workflow("docker-publish.yml")["jobs"]
    writer_name, writer, step = _writer()
    qualifications = {
        path: next(
            (name for name, job in jobs.items() if job.get("uses") == path), None
        )
        for path in (
            "./.github/workflows/pytest-unit-tests.yml",
            "./.github/workflows/alembic-migration-gate.yml",
        )
    }
    assert all(qualifications.values()), (
        "Existing required CI and migration owners must qualify publication"
    )
    required = _ancestors(jobs, writer_name)
    assert set(qualifications.values()) <= required
    assert writer["concurrency"] == {
        "group": "app-image-latest",
        "cancel-in-progress": False,
    }
    for name in qualifications.values():
        assert f"${{{{ needs.{name}.result }}}}" in step["env"].values()
    source = jobs[qualifications["./.github/workflows/pytest-unit-tests.yml"]]
    image_expression = source["with"]["candidate_image"]
    assert image_expression == step["env"]["CANDIDATE_IMAGE"]
    assembly_name = re.fullmatch(
        r"\$\{\{\s*needs\.([^.]+)\.outputs\.image_ref\s*\}\}", image_expression
    )[1]
    assert assembly_name in _ancestors(
        jobs, qualifications["./.github/workflows/pytest-unit-tests.yml"]
    )
    assert assembly_name in required
    assert not any(":latest" in s.get("run", "") for s in jobs[assembly_name]["steps"])


def test_assembly_preserves_version_and_sha_tags_without_moving_default_channel(
    tmp_path,
):
    jobs = _workflow("docker-publish.yml")["jobs"]
    assembly = next(
        job for job in jobs.values() if "image_ref" in job.get("outputs", {})
    )
    step = next(
        step for step in assembly["steps"] if "imagetools create" in step.get("run", "")
    )
    result, channel, commands = _run_writer(tmp_path, step=step)
    assert result.returncode == 0, result.stderr
    assert channel == PREVIOUS
    assert not _channel_updates(commands)
    created = [
        command
        for command in commands
        if command[:3] == ["buildx", "imagetools", "create"]
    ]
    assert len(created) == 1
    assert IMAGE + ":fixture-version" in created[0]
    assert IMAGE + ":sha-candidate-commit" in created[0]
    assert (tmp_path / "outputs").read_text().strip() == "image_ref=" + CANDIDATE


def test_existing_installed_image_consumers_use_the_optional_release_manifest():
    config = _workflow("pytest-unit-tests.yml")
    events = config.get("on", config.get(True))
    assert events["workflow_call"]["inputs"]["candidate_image"]["default"] == ""
    installed_jobs = [
        job
        for job in config["jobs"].values()
        if any(
            "first_run_journey_3938.sh" in step.get("run", "")
            or "gather_exact_artifact_runtime_evidence.py" in step.get("run", "")
            for step in job.get("steps", [])
        )
    ]
    assert len(installed_jobs) == 2
    for job in installed_jobs:
        steps = job["steps"]
        pulls = [
            step
            for step in steps
            if re.search(r"docker(?:\s+image)?\s+pull\s", step.get("run", ""))
            and "CANDIDATE_IMAGE" in step.get("env", {})
        ]
        assert len(pulls) == 1
        assert _image_value(pulls[0]["env"]["CANDIDATE_IMAGE"], CANDIDATE) == CANDIDATE
        builds = [
            step
            for step in steps
            if step.get("with", {}).get("file", "").removeprefix("./")
            == "api_service/Dockerfile"
            and step["with"].get("target") != "test-runtime"
        ]
        assert len(builds) == 1
        # The optional input owns whether the existing local build or registry
        # pull runs; keep ordinary empty-input builds available.
        assert "inputs.candidate_image" in builds[0]["if"]
        assert "inputs.candidate_image" in pulls[0]["if"]
    integration = next(
        job
        for job in installed_jobs
        if any(
            "first_run_journey_3938.sh" in step.get("run", "") for step in job["steps"]
        )
    )
    image_consumers = [
        step
        for step in integration["steps"]
        if "first_run_journey_3938.sh" in step.get("run", "")
    ]
    assert len(image_consumers) == 3
    local_image = next(
        step["with"]["tags"]
        for step in integration["steps"]
        if step.get("with", {}).get("file", "").removeprefix("./")
        == "api_service/Dockerfile"
        and step["with"].get("target") != "test-runtime"
    )
    assert all(
        _image_value(step["env"]["MOONMIND_IMAGE"], CANDIDATE) == CANDIDATE
        for step in image_consumers
    )
    assert all(
        _image_value(step["env"]["MOONMIND_IMAGE"], "") == local_image
        for step in image_consumers
    )
    journal = next(
        step
        for step in integration["steps"]
        if "MOONMIND_TEST_JOURNAL_APP_IMAGE" in step.get("env", {})
    )
    assert (
        _image_value(journal["env"]["MOONMIND_TEST_JOURNAL_APP_IMAGE"], CANDIDATE)
        == CANDIDATE
    )
    assert (
        _image_value(journal["env"]["MOONMIND_TEST_JOURNAL_APP_IMAGE"], "")
        == local_image
    )


@pytest.mark.parametrize("release_image", ["", CANDIDATE])
def test_exact_probe_and_recovery_consumers_keep_registry_and_local_identities_distinct(
    tmp_path, release_image
):
    jobs = _workflow("pytest-unit-tests.yml")["jobs"].values()
    job = next(
        job
        for job in jobs
        if any(
            "gather_exact_artifact_runtime_evidence.py" in step.get("run", "")
            for step in job.get("steps", [])
        )
    )
    identity = next(
        step
        for step in job["steps"]
        if "runnable=" in step.get("run", "") and "GITHUB_OUTPUT" in step["run"]
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/sh\nprintf '%s\\n' 'sha256:" + "d" * 64 + "'\n")
    docker.chmod(0o755)
    output = tmp_path / "outputs"
    result = subprocess.run(
        ["bash", "-c", identity["run"]],
        env={
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "GITHUB_OUTPUT": str(output),
            **{
                key: _image_value(value, release_image)
                for key, value in identity.get("env", {}).items()
            },
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    expected = release_image or "sha256:" + "d" * 64
    assert values["runnable"] == expected
    assert values["digest"] == ("sha256:" + ("a" if release_image else "d") * 64)
    assert values["ref"] == (
        release_image or "moonmind-exact-artifact@sha256:" + "d" * 64
    )
    for consumer, flag in (
        ("gather_exact_artifact_runtime_evidence.py", "--image"),
        ("run_omnigent_exact_artifact_conformance.py", "--runnable-image"),
        ("run_credential_free_host_recovery.py", "--moonmind-image"),
    ):
        step = next(step for step in job["steps"] if consumer in step.get("run", ""))
        argument = re.search(re.escape(flag) + r'\s+"([^"\n]+)"', step["run"])[1]
        match = re.fullmatch(
            r"\$\{\{\s*steps\."
            + re.escape(identity["id"])
            + r"\.outputs\.([a-z_]+)\s*\}\}",
            argument,
        )
        assert match is not None, f"{consumer} lost the prepared application image"
        assert values[match[1]] == expected


@pytest.mark.parametrize("candidate", ["", CANDIDATE])
def test_supplied_image_selects_every_required_journey_on_a_narrow_pr(
    tmp_path, candidate
):
    jobs = _workflow("pytest-unit-tests.yml")["jobs"].values()
    selection = next(
        step
        for job in jobs
        for step in job.get("steps", [])
        if "python3 tools/select_test_suites.py" in step.get("run", "")
    )
    changes = tmp_path / "changes"
    changes.write_text("docs/README.md\n")
    output = tmp_path / "selection"
    result = subprocess.run(
        [
            "bash",
            "-c",
            selection["run"].replace(
                "/tmp/changed-files.txt", shlex.quote(str(changes))
            ),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REF_NAME": "fixture-qualification",
            "GITHUB_OUTPUT": str(output),
            "QUALIFICATION_IMAGE": candidate,
            "QUALIFICATION_ARCHIVE": "",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values["full_backend"] == ("true" if candidate else "false")
    if candidate:
        for key in (
            "exact_artifact",
            "integration_fresh_journey",
            "integration_upgrade_journey",
            "integration_controller_journey",
        ):
            assert values[key] == "true", (
                f"Supplied image lost required {key} execution"
            )


def test_isolated_caller_qualifies_one_archive_without_a_publication_path():
    fixture = _workflow("app-image-qualification.yml")
    jobs = fixture["jobs"]
    caller_name, caller = next(
        (name, job)
        for name, job in jobs.items()
        if job.get("uses") == "./.github/workflows/pytest-unit-tests.yml"
    )
    producers = []
    for key in ("candidate_image", "candidate_oci_artifact"):
        producer, output = re.fullmatch(
            r"\$\{\{\s*needs\.([^.]+)\.outputs\.([^\s]+)\s*\}\}", caller["with"][key]
        ).groups()
        assert producer in _ancestors(jobs, caller_name)
        assert output in jobs[producer]["outputs"]
        producers.append(producer)
    assert len(set(producers)) == 1, "One owner must assemble the shared candidate"
    builder = jobs[producers[0]]
    image_builds = [
        step
        for step in builder["steps"]
        if step.get("with", {}).get("file") == "api_service/Dockerfile"
    ]
    assert len(image_builds) == 1
    build = image_builds[0]["with"]
    assert build["outputs"].startswith("type=oci,")
    assert build.get("push", False) is False
    assert "github.event.pull_request.head.sha || github.sha" in build["build-args"]
    assert any(
        "release_image_fixture.py describe" in step.get("run", "")
        for step in builder["steps"]
    )
    assert any(
        step.get("uses", "").startswith("actions/upload-artifact@")
        for step in builder["steps"]
    )
    for job in jobs.values():
        assert "write" not in job.get("permissions", {}).values()
        assert job.get("uses") != "./.github/workflows/docker-publish.yml"
        assert not any(
            "imagetools create" in step.get("run", "") for step in job.get("steps", [])
        )
    for job in _workflow("pytest-unit-tests.yml")["jobs"].values():
        steps = job.get("steps", [])
        if not any(
            "first_run_journey_3938.sh" in step.get("run", "")
            or "gather_exact_artifact_runtime_evidence.py" in step.get("run", "")
            for step in steps
        ):
            continue
        restores = [
            step
            for step in steps
            if "release_image_fixture.py restore" in step.get("run", "")
        ]
        assert len(restores) == 1
        restore = restores[0]
        assert _image_value(restore["env"]["CANDIDATE_IMAGE"], CANDIDATE) == CANDIDATE
        downloads = [
            step
            for step in steps
            if step.get("with", {}).get("name")
            == "${{ inputs.candidate_oci_artifact }}"
        ]
        assert len(downloads) == 1
        assert steps.index(downloads[0]) < steps.index(restore)
        cleanup = next(
            step
            for step in steps
            if "release_image_fixture.py cleanup" in step.get("run", "")
        )
        assert "always()" in cleanup["if"]
        assert cleanup["env"]["FIXTURE_OWNER"] == restore["env"]["FIXTURE_OWNER"]
