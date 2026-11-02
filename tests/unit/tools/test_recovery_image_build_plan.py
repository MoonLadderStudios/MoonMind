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
