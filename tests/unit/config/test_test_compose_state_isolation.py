"""Test startup must not consume or overwrite deployment runtime evidence."""

from pathlib import Path

import yaml


def test_pytest_masks_repository_runtime_state_with_anonymous_storage() -> None:
    compose = yaml.safe_load(Path("docker-compose.test.yaml").read_text())
    # A target-only volume is anonymous and removed with the bounded test run.
    # It masks the same directory beneath the enclosing repository bind mount.
    assert "/app/var" in compose["services"]["pytest"]["volumes"]
