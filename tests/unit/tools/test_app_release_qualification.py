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


def _evaluate(expression, context):
    """Evaluate the GitHub expression subset used by these jobs and values."""
    match = re.fullmatch(r"\s*\$\{\{\s*(.*?)\s*\}\}\s*", expression, re.S)

    def lookup(path):
        value = context
        for key in path.split("."):
            value = value.get(key, "") if isinstance(value, dict) else ""
        return value

    def translate(token):
        text = token[0]
        if text.startswith("'"):
            return repr(ast.literal_eval(text))
        if text in {"always()", "!cancelled()"}:
            return "True"
        return {"&&": " and ", "||": " or ", "!": " not "}.get(
            text, f"lookup({text!r})"
        )

    python = re.sub(
        r"'[^']*'|always\(\)|!cancelled\(\)|&&|\|\||!(?!=)"
        r"|[A-Za-z_][\w-]*(?:\.[\w-]+)+",
        translate,
        match[1] if match else expression,
    )
    return eval(python, {"__builtins__": {}}, {"lookup": lookup})


def _job_runs(job, context):
    """Apply GitHub's implicit success() gate unless a status check is explicit."""
    condition = job.get("if", "")
    needs = job.get("needs", [])
    if not re.search(r"\b(?:always|success|failure|cancelled)\(\)", condition):
        needs = [needs] if isinstance(needs, str) else needs
        if any(context["needs"][name]["result"] != "success" for name in needs):
            return False
    return not condition or bool(_evaluate(condition, context))


def _image_value(expression, candidate, job=None):
    """Resolve an image value the way a consumer of ``candidate`` sees it."""
    context = {
        "inputs": {"candidate_image": candidate},
        "needs": {"prepare-app-image": {"outputs": {"image_ref": candidate}}},
    }
    context["env"] = {
        key: _evaluate(value, context)
        for key, value in (job or {}).get("env", {}).items()
    }
    return _evaluate(expression, context)


def _image_calls(jobs):
    return {
        name: job
        for name, job in jobs.items()
        if job.get("uses") == "./.github/workflows/pytest-unit-tests.yml"
        and job.get("with", {}).get("candidate_image")
    }


def _qualification_context(
    *,
    assembly="success",
    source="success",
    artifact="success",
    recovery="success",
    migration="success",
    source_receipt="success",
    qualified_image=CANDIDATE,
    qualified_phase=None,
):
    jobs = _workflow("docker-publish.yml")["jobs"]
    results = {"source": source, "artifact": artifact, "recovery": recovery}
    context = {}
    for name, job in jobs.items():
        if job.get("uses") == "./.github/workflows/pytest-unit-tests.yml":
            phase = job["with"]["verification_phase"]
            context[name] = {
                "result": results[phase],
                "outputs": {
                    "qualified_image": (
                        qualified_image
                        if qualified_phase in {None, phase}
                        else CANDIDATE
                    ),
                    "source_receipt_artifact": "source-verification-1-1",
                    "source_receipt_digest": "sha256:" + "d" * 64,
                },
            }
        elif job.get("uses") == "./.github/workflows/alembic-migration-gate.yml":
            context[name] = {"result": migration}
        elif name == "verify-source-receipt":
            context[name] = {"result": source_receipt}
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
    artifact="success",
    recovery="success",
    migration="success",
    source_receipt="success",
    ref="refs/heads/main",
    main_head="candidate-commit",
    candidate=CANDIDATE,
    qualified_image=None,
    qualified_phase=None,
    step=None,
):
    writer = step if step is not None else _writer()[2]
    needs = _qualification_context(
        assembly=assembly,
        source=source,
        artifact=artifact,
        recovery=recovery,
        migration=migration,
        source_receipt=source_receipt,
        qualified_image=candidate if qualified_image is None else qualified_image,
        qualified_phase=qualified_phase,
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


@pytest.mark.parametrize(
    "gate",
    ["assembly", "source", "artifact", "recovery", "migration", "source_receipt"],
)
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


@pytest.mark.parametrize("phase", ["artifact", "recovery"])
@pytest.mark.parametrize("qualified_image", ["", PREVIOUS, IMAGE + ":fixture-version"])
def test_publication_requires_the_image_that_artifact_qualification_consumed(
    tmp_path, phase, qualified_image
):
    result, channel, commands = _run_writer(
        tmp_path, qualified_image=qualified_image, qualified_phase=phase
    )
    assert result.returncode != 0
    assert channel == PREVIOUS
    assert not _channel_updates(commands)


def test_publication_summary_keeps_source_and_image_provenance(tmp_path):
    result, channel, _ = _run_writer(tmp_path)
    assert result.returncode == 0, result.stderr
    assert channel == CANDIDATE
    summary = (tmp_path / "summary").read_text()
    for value in (CANDIDATE, "source-verification-1-1", "sha256:" + "d" * 64):
        assert value in summary


def test_default_writer_depends_on_existing_source_migration_and_image_owners():
    jobs = _workflow("docker-publish.yml")["jobs"]
    writer_name, writer, step = _writer()
    qualifications = {
        path: next(
            (
                name
                for name, job in jobs.items()
                if job.get("uses") == path
                and (
                    path.endswith("alembic-migration-gate.yml")
                    or job.get("with", {}).get("candidate_image")
                )
            ),
            None,
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


def test_existing_installed_image_consumers_use_one_shared_immutable_manifest():
    config = _workflow("pytest-unit-tests.yml")
    events = config.get("on", config.get(True))
    assert events["workflow_call"]["inputs"]["candidate_image"]["default"] == ""
    jobs = config["jobs"]
    producer = jobs["prepare-app-image"]
    builds = [
        step
        for step in producer["steps"]
        if step.get("with", {}).get("file") == "api_service/Dockerfile"
    ]
    assert len(builds) == 1
    assert builds[0]["with"]["outputs"].startswith("type=oci,")
    assert builds[0]["with"].get("push", False) is False
    for name in ("integration-ci", "omnigent-exact-artifact", "omnigent-host-recovery"):
        job = jobs[name]
        assert "prepare-app-image" in _ancestors(jobs, name)
        assert not any(
            step.get("with", {}).get("file") == "api_service/Dockerfile"
            for step in job["steps"]
        )
        pull = _pull_step(job)
        assert _image_value("${{ env.CANDIDATE_IMAGE }}", CANDIDATE, job) == CANDIDATE
        assert "org.opencontainers.image.revision" in pull["run"]
    journeys = [
        step
        for step in jobs["integration-ci"]["steps"]
        if "first_run_journey_3938.sh" in step.get("run", "")
    ]
    assert len(journeys) == 3
    installed = jobs["integration-ci"]
    assert all(
        _image_value(step["env"]["MOONMIND_IMAGE"], CANDIDATE, installed) == CANDIDATE
        for step in journeys
    )
    journal = next(
        step
        for step in jobs["integration-ci"]["steps"]
        if "MOONMIND_TEST_JOURNAL_APP_IMAGE" in step.get("env", {})
    )
    assert (
        _image_value(
            journal["env"]["MOONMIND_TEST_JOURNAL_APP_IMAGE"], CANDIDATE, installed
        )
        == CANDIDATE
    )


@pytest.mark.parametrize(
    "release_image",
    ["localhost:5001/moonmind-test-qualification@sha256:" + "d" * 64, CANDIDATE],
)
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
                key: _image_value(value, release_image, job)
                for key, value in identity.get("env", {}).items()
            },
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    expected = release_image
    assert values["runnable"] == expected
    assert values["digest"] == release_image.rsplit("@", 1)[1]
    assert values["ref"] == release_image
    for consumer, flag in (
        ("gather_exact_artifact_runtime_evidence.py", "--image"),
        ("run_omnigent_exact_artifact_conformance.py", "--runnable-image"),
        ("run_credential_free_host_recovery.py", "--moonmind-image"),
    ):
        step = next(
            step
            for candidate_job in jobs
            for step in candidate_job.get("steps", [])
            if consumer in step.get("run", "")
        )
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
            "MOONMIND_CI_PHASE": "full",
            "SOURCE_RECEIPT": "",
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
    events = fixture.get("on", fixture.get(True))
    assert "pull_request" not in events
    assert caller.get("with", {}).get("candidate_image", "") == ""
    builder = _workflow("pytest-unit-tests.yml")["jobs"]["prepare-app-image"]
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
        assert '--image "$CANDIDATE_IMAGE"' in restore["run"]
        assert _image_value("${{ env.CANDIDATE_IMAGE }}", CANDIDATE, job) == CANDIDATE
        downloads = [
            step
            for step in steps
            if step.get("with", {}).get("name") == "${{ env.CANDIDATE_ARCHIVE }}"
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


def _concurrency_value(expression, context):
    """Evaluate dotted fields and the fallback operators used by these groups."""

    def field(token):
        token = token.strip()
        if "==" in token:
            left, right = token.split("==", 1)
            return str(field(left)) == str(field(right))
        if token.startswith("'") or token.isdecimal():
            return ast.literal_eval(token)
        assert re.fullmatch(r"[\w.]+", token), f"Unsupported concurrency field: {token}"
        value = context
        for key in token.split("."):
            value = value.get(key, "") if isinstance(value, dict) else ""
        return value

    def resolve(match):
        value = ""
        for alternative in match[1].split("||"):
            for token in alternative.split("&&"):
                value = field(token)
                if not value:
                    break
            if value:
                break
        return str(value or "")

    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", resolve, expression)


@pytest.mark.parametrize(
    "owner", ["pytest-unit-tests.yml", "alembic-migration-gate.yml"]
)
def test_older_release_rerun_cannot_cancel_current_candidate_qualification(owner):
    config = _workflow(owner)
    call = next(
        job
        for job in _workflow("docker-publish.yml")["jobs"].values()
        if job.get("uses") == "./.github/workflows/" + owner
    )
    groups = []
    for run in ("older-run", "current-run"):
        context = {
            "github": {
                "workflow": "Release / Build App Image",
                "ref": "refs/heads/main",
                "run_id": run,
                "event": {},
            },
            "needs": _qualification_context(),
        }
        context["inputs"] = {
            key: _concurrency_value(value, context)
            for key, value in call.get("with", {}).items()
        }
        groups.append(_concurrency_value(config["concurrency"]["group"], context))
    assert groups[0] != groups[1], "An older rerun must not cancel the current release"


@pytest.mark.parametrize(
    "owner", ["pytest-unit-tests.yml", "alembic-migration-gate.yml"]
)
@pytest.mark.parametrize(
    "event", ["pull_request", "push", "schedule", "workflow_dispatch"]
)
def test_ordinary_ci_keeps_latest_run_cancellation_within_its_existing_owner(
    owner, event
):
    config = _workflow(owner)
    assert config["concurrency"]["cancel-in-progress"] is True

    def group(run, *, number=73, ref="refs/heads/main"):
        return _concurrency_value(
            config["concurrency"]["group"],
            {
                "github": {
                    "workflow": config["name"],
                    "ref": ref,
                    "run_id": run,
                    "event": {"pull_request": {"number": number}}
                    if event == "pull_request"
                    else {},
                },
                "inputs": {},
            },
        )

    assert group("older-head") == group("newer-head")
    if event == "pull_request":
        assert group("same-run", number=73) != group("same-run", number=74)
    else:
        assert group("same-run") != group("same-run", ref="refs/heads/another")


def test_release_source_checks_start_before_assembly_and_only_once():
    config = _workflow("docker-publish.yml")
    source = config["jobs"]["source-checks"]
    assert source["uses"] == "./.github/workflows/pytest-unit-tests.yml"
    assert source["with"]["verification_phase"] == "source"
    assert "assemble" not in _ancestors(config["jobs"], "source-checks")
    assert config["jobs"]["qualify"]["with"]["verification_phase"] == "artifact"
    assert "source-checks" not in _ancestors(config["jobs"], "qualify")
    # The receipt is verified as soon as the source phase hands it off, in
    # parallel with artifact qualification instead of after it.
    assert _ancestors(config["jobs"], "verify-source-receipt") == {"source-checks"}
    assert "verify-source-receipt" in _ancestors(config["jobs"], _writer()[0])
    test_events = _workflow("pytest-unit-tests.yml")
    events = test_events.get("on", test_events.get(True))
    assert "push" not in events


def test_release_image_consumers_wait_only_for_their_own_inputs():
    jobs = _workflow("docker-publish.yml")["jobs"]
    calls = _image_calls(jobs)
    phases = {job["with"]["verification_phase"]: name for name, job in calls.items()}
    assert sorted(phases) == ["artifact", "recovery"]
    for phase, name in phases.items():
        assert "assemble" in _ancestors(jobs, name)
        prepared = "prepare-recovery-images" in _ancestors(jobs, name)
        # Only host recovery consumes the prepared recovery runtimes.
        assert prepared == (phase == "recovery"), name
        assert bool(calls[name]["with"].get("recovery_images_artifact")) == prepared
    # Preparation still overlaps the application build instead of following it.
    assert "assemble" not in _ancestors(jobs, "prepare-recovery-images")
    writer_name, _, step = _writer()
    required = _ancestors(jobs, writer_name)
    for name in calls:
        assert name in required
        assert f"${{{{ needs.{name}.result }}}}" in step["env"].values()
        assert (
            f"${{{{ needs.{name}.outputs.qualified_image }}}}" in step["env"].values()
        )


CONSUMERS = ("integration-ci", "omnigent-exact-artifact", "omnigent-host-recovery")


def _consumer_context(candidate, producer, *, selected="true"):
    selection = {
        key: selected
        for key in (
            "exact_artifact",
            "host_recovery",
            "integration_fresh_journey",
            "integration_upgrade_journey",
            "integration_controller_journey",
        )
    }
    return {
        "inputs": {
            "candidate_image": candidate,
            "recovery_images_artifact": "recovery-images-1-1" if candidate else "",
        },
        "needs": {
            "select-test-suites": {"result": "success", "outputs": selection},
            "prepare-app-image": {"result": producer},
            "prepare-recovery-images": {
                "result": "skipped" if candidate else "success"
            },
        },
    }


@pytest.mark.parametrize("name", CONSUMERS)
def test_supplied_image_runs_each_consumer_without_a_pass_through_job(name):
    jobs = _workflow("pytest-unit-tests.yml")["jobs"]
    producer, consumer = jobs["prepare-app-image"], jobs[name]
    # The caller's immutable manifest is not re-echoed by another runner.
    assert not _job_runs(producer, _consumer_context(CANDIDATE, "success"))
    assert _job_runs(producer, _consumer_context("", "success"))
    assert _job_runs(consumer, _consumer_context(CANDIDATE, "skipped"))
    assert _job_runs(consumer, _consumer_context("", "success"))
    # A missing or failed PR candidate still stops every consumer.
    for result in ("skipped", "failure", "cancelled"):
        assert not _job_runs(consumer, _consumer_context("", result))
    assert not _job_runs(consumer, _consumer_context(CANDIDATE, "failure"))
    assert not _job_runs(
        consumer, _consumer_context(CANDIDATE, "skipped", selected="false")
    )
    local = "localhost:5001/moonmind-test-qualification@sha256:" + "d" * 64
    supplied = {
        "inputs": {"candidate_image": CANDIDATE, "candidate_oci_artifact": ""},
        "needs": {"prepare-app-image": {"outputs": {}}},
    }
    built = {
        "inputs": {"candidate_image": "", "candidate_oci_artifact": ""},
        "needs": {
            "prepare-app-image": {
                "outputs": {"image_ref": local, "artifact_name": "app-image-1-1"}
            }
        },
    }
    assert _evaluate(consumer["env"]["CANDIDATE_IMAGE"], supplied) == CANDIDATE
    assert _evaluate(consumer["env"]["CANDIDATE_ARCHIVE"], supplied) == ""
    assert _evaluate(consumer["env"]["CANDIDATE_IMAGE"], built) == local
    assert _evaluate(consumer["env"]["CANDIDATE_ARCHIVE"], built) == "app-image-1-1"


def _pull_step(job):
    pulls = [
        step
        for step in job["steps"]
        if 'docker pull "$CANDIDATE_IMAGE"' in step.get("run", "")
    ]
    assert len(pulls) == 1
    return pulls[0]


def _run_pull(tmp_path, step, image, *, label="c" * 40, pull_status=0):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / "docker-calls"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        f'if [ "$1" = pull ]; then exit {pull_status}; fi\n'
        f"printf '%s\\n' '{label}'\n"
    )
    docker.chmod(0o755)
    output = tmp_path / "outputs"
    output.write_text("")
    result = subprocess.run(
        ["bash", "-c", step["run"]],
        env={
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "GITHUB_OUTPUT": str(output),
            "CANDIDATE_IMAGE": image,
            "MOONMIND_CI_REVISION": "c" * 40,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    pulled = calls.read_text().splitlines() if calls.exists() else []
    return result, output.read_text(), pulled


def test_qualified_image_is_reported_by_the_consumer_that_validated_it(tmp_path):
    config = _workflow("pytest-unit-tests.yml")
    events = config.get("on", config.get(True))
    value = events["workflow_call"]["outputs"]["qualified_image"]["value"]
    assert "prepare-app-image" not in value
    owners = dict(re.findall(r"jobs\.([\w-]+)\.outputs\.(\w+)", value))
    assert set(owners) == {"omnigent-exact-artifact", "omnigent-host-recovery"}
    jobs = config["jobs"]
    for phase, owner in (
        ("artifact", "omnigent-exact-artifact"),
        ("recovery", "omnigent-host-recovery"),
    ):
        # Each release phase reports the image its own consumer validated.
        reported = {"jobs": {owner: {"outputs": {owners[owner]: CANDIDATE}}}}
        assert _evaluate(value, reported) == CANDIDATE, phase
        step_id, key = re.fullmatch(
            r"\$\{\{\s*steps\.([\w-]+)\.outputs\.(\w+)\s*\}\}",
            jobs[owner]["outputs"][owners[owner]],
        ).groups()
        step = _pull_step(jobs[owner])
        assert step["id"] == step_id
        (tmp_path / owner).mkdir()
        result, written, pulled = _run_pull(tmp_path / owner, step, CANDIDATE)
        assert result.returncode == 0, result.stderr
        assert written.splitlines() == [f"{key}={CANDIDATE}"]
        assert pulled[0] == f"pull {CANDIDATE}"


@pytest.mark.parametrize(
    ("image", "label", "pull_status"),
    [
        ("", "c" * 40, 0),
        (IMAGE + ":latest", "c" * 40, 0),
        (CANDIDATE, "d" * 40, 0),
        (CANDIDATE, "", 0),
        (CANDIDATE, "c" * 40, 1),
    ],
)
@pytest.mark.parametrize("name", CONSUMERS)
def test_missing_unavailable_or_wrong_image_is_not_reported_as_qualified(
    tmp_path, name, image, label, pull_status
):
    step = _pull_step(_workflow("pytest-unit-tests.yml")["jobs"][name])
    result, written, pulled = _run_pull(
        tmp_path, step, image, label=label, pull_status=pull_status
    )
    assert result.returncode != 0
    assert written == ""
    if not image.endswith(CANDIDATE.rsplit("@", 1)[1]):
        assert pulled == []


def test_one_pr_candidate_is_consumed_by_every_installed_lane():
    jobs = _workflow("pytest-unit-tests.yml")["jobs"]
    assert "prepare-app-image" in jobs
    for name in ("integration-ci", "omnigent-exact-artifact"):
        assert "prepare-app-image" in _ancestors(jobs, name)
        assert not any(
            step.get("with", {}).get("file", "").removeprefix("./")
            == "api_service/Dockerfile"
            for step in jobs[name]["steps"]
        )
    assert "prepare-app-image" not in _ancestors(jobs, "integration-hermetic")


def test_review_readiness_keeps_the_existing_same_head_verification():
    for name in ("pytest-unit-tests.yml", "alembic-migration-gate.yml"):
        workflow = _workflow(name)
        events = workflow.get("on", workflow.get(True))
        assert {"opened", "synchronize", "reopened"} <= set(
            events["pull_request"]["types"]
        )
        assert "ready_for_review" not in events["pull_request"]["types"]


def test_explicit_release_reruns_cannot_cancel_current_publication():
    config = _workflow("docker-publish.yml")["concurrency"]

    def values(run_id, *, event="push", attempt=1):
        context = {
            "github": {
                "event_name": event,
                "ref": "refs/heads/main",
                "run_id": run_id,
                "run_attempt": str(attempt),
            }
        }
        return (
            _concurrency_value(config["group"], context),
            _concurrency_value(config["cancel-in-progress"], context) == "True",
        )

    current_group, current_cancels = values("current-run")
    older_group, older_cancels = values("older-run")
    assert current_group == older_group and current_cancels and older_cancels
    for arguments in ({"attempt": 2}, {"event": "workflow_dispatch"}):
        group, cancels = values("older-run", **arguments)
        assert group != current_group
        assert not cancels


def _timing_jobs(jobs: dict) -> set[str]:
    return {
        name
        for name, job in jobs.items()
        if any(
            "fetch_public_job_timings" in step.get("run", "")
            for step in job.get("steps", [])
        )
    }


def _condition(expression: str, context: dict) -> bool:
    """Evaluate the `&&`-joined status checks and comparisons of job conditions."""
    match = re.fullmatch(r"\$\{\{\s*(.*?)\s*\}\}", expression.strip(), re.S)
    accepted = True
    for term in (match[1] if match else expression).split("&&"):
        term = term.strip()
        if term == "always()":
            continue
        comparison = re.fullmatch(r"([\w.]+)\s*(==|!=)\s*'([^']*)'", term)
        assert comparison, f"Unsupported job condition: {term}"
        value = context
        for key in comparison[1].split("."):
            value = value.get(key) if isinstance(value, dict) else None
        accepted &= (value == comparison[3]) == (comparison[2] == "==")
    return accepted


def test_timing_reports_stay_advisory_and_outside_publication():
    release = _workflow("docker-publish.yml")["jobs"]
    suite = _workflow("pytest-unit-tests.yml")["jobs"]
    writer = _writer()[0]
    path = _ancestors(release, writer) | {writer}
    assert len(_timing_jobs(release)) == 1
    assert not _timing_jobs(release) & path
    assert not _timing_jobs(suite) & _ancestors(suite, "ci-required")
    # Reusable calls share the release run ID, so the release report already
    # covers their jobs; their own timing jobs must not run on that path.
    calls = [
        name
        for name in path
        if release[name].get("uses", "").endswith("/pytest-unit-tests.yml")
    ]
    assert len(calls) == 3
    for name in calls:
        inputs = {"verification_phase": release[name]["with"]["verification_phase"]}
        for timing in _timing_jobs(suite):
            assert not _condition(suite[timing]["if"], {"inputs": inputs}), name
    # Standalone pull request, merge queue, schedule and manual runs keep one.
    for inputs in ({}, {"verification_phase": "full"}):
        assert [
            name
            for name in _timing_jobs(suite)
            if _condition(suite[name]["if"], {"inputs": inputs})
        ] == ["ci-timings"]
    # A failed, canceled or unavailable timing report never fails a run.
    for jobs in (release, suite):
        for timing in _timing_jobs(jobs):
            assert jobs[timing]["continue-on-error"] is True
