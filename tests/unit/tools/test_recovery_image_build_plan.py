"""Validate the real Buildx plan without building or contacting a daemon."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / "tools/ci/recovery-images.hcl"


@pytest.mark.parametrize("write_cache", [False, True])
def test_shared_graph_retains_both_images_and_source_provenance(write_cache):
    if not shutil.which("docker"):
        pytest.skip("Buildx CLI unavailable; hosted image owner validates this plan")
    result = subprocess.run(
        ["docker", "buildx", "bake", "--print", "--file", str(PLAN)],
        cwd=ROOT,
        env={
            **os.environ,
            "UPSTREAM_COMMIT": "a" * 40,
            "CACHE_EXPORTS": str(write_cache).lower(),
        },
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert set(plan["group"]["default"]["targets"]) == {"server", "host-base"}
    for name, tag, target in (
        ("server", "moonmind-test-omnigent-server:candidate", "runtime"),
        ("host-base", "moonmind-test-omnigent-host-base:candidate", "host"),
    ):
        build = plan["target"][name]
        assert build["context"] == "omnigent"
        assert build["dockerfile"] == "deploy/docker/Dockerfile"
        assert build["target"] == target
        assert build["tags"] == [tag]
        assert build["labels"] == {"org.opencontainers.image.revision": "a" * 40}
        assert build["output"] == [{"type": "docker"}]
        assert {cache["scope"] for cache in build["cache-from"]} == {
            "credential-free-recovery-server",
            "credential-free-recovery-host",
        }
        exports = build.get("cache-to", [])
        assert bool(exports) is write_cache
        if exports:
            assert len(exports) == 1
            assert exports[0]["mode"] == "max"
            assert exports[0]["scope"] == "credential-free-recovery-" + (
                "server" if name == "server" else "host"
            )


def test_only_first_automatic_main_attempt_exports_recovery_cache():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/prepare-recovery-images.yml").read_text()
    )
    builds = [
        step
        for step in workflow["jobs"]["prepare"]["steps"]
        if step.get("uses", "").startswith("docker/bake-action@")
    ]
    assert len(builds) == 1
    build = builds[0]
    assert (
        build["env"]["CACHE_EXPORTS"]
        == "${{ github.event_name == 'push' && github.ref == 'refs/heads/main' && github.run_attempt == 1 }}"
    )
    assert (
        build["env"]["UPSTREAM_COMMIT"] == "${{ steps.recovery-source.outputs.commit }}"
    )
    assert build["with"]["files"] == "tools/ci/recovery-images.hcl"
    assert build["with"]["source"] == "."


def _steps(workflow: str, job: str) -> list[dict]:
    loaded = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    return loaded["jobs"][job]["steps"]


def _index(steps: list[dict], text: str) -> int:
    matches = [
        index
        for index, step in enumerate(steps)
        if text in step.get("run", "") or text == step.get("name")
    ]
    assert len(matches) == 1, text
    return matches[0]


def test_prepared_registry_storage_is_the_recovery_transport():
    producer = _steps("prepare-recovery-images.yml", "prepare")
    scripts = "\n".join(step.get("run", "") for step in producer)
    # Compressed, layer-deduplicated registry blobs replace a gzip export.
    assert "docker save" not in scripts and "images.tar" not in scripts
    pushed = _index(producer, "pin_local_image() {")
    assert (
        '-v "$RUNNER_TEMP/recovery-images/registry:/var/lib/registry"'
        in producer[pushed]["run"]
    )
    released = _index(producer, "Release temporary recovery build storage")
    recorded = _index(producer, "release_image_fixture.py save-recovery")
    uploaded = next(
        index
        for index, step in enumerate(producer)
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    # PR #5219's cleanup still stops the registry and prunes the job builder
    # before the transported storage is recorded and uploaded.
    assert "docker rm -fv moonmind-test-recovery-registry" in producer[released]["run"]
    assert "docker buildx prune" in producer[released]["run"]
    assert pushed < released < recorded < uploaded
    assert (
        '--registry "$RUNNER_TEMP/recovery-images/registry"'
        in producer[recorded]["run"]
    )
    assert producer[uploaded]["with"]["path"] == "${{ runner.temp }}/recovery-images"
    assert producer[uploaded]["with"]["compression-level"] == 0

    consumer = _steps("pytest-unit-tests.yml", "omnigent-host-recovery")
    scripts = "\n".join(step.get("run", "") for step in consumer)
    assert "docker load" not in scripts and "docker push" not in scripts
    served = _index(consumer, "release_image_fixture.py restore-recovery")
    run = consumer[served]["run"]
    assert consumer[served]["id"] == "recovery-images"
    mount = '-v "$RUNNER_TEMP/recovery-images/registry:/var/lib/registry:ro"'
    assert run.index(mount) < run.index("restore-recovery")
    assert '>> "$GITHUB_OUTPUT"' in run.split("restore-recovery", 1)[1]
    downloaded = next(
        index
        for index, step in enumerate(consumer)
        if step.get("uses", "").startswith("actions/download-artifact@")
        and step["with"]["path"] == "${{ runner.temp }}/recovery-images"
    )
    recovered = _index(consumer, "tools/ci/run_credential_free_host_recovery.py")
    assert downloaded < served < recovered
