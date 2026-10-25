"""The default replacement cannot open the retained source data format."""

from pathlib import Path

import yaml


def test_default_storage_uses_a_separate_volume_and_consistent_test_image():
    root = Path(__file__).resolve().parents[4]
    production = yaml.safe_load((root / "docker-compose.yaml").read_text())
    store = production["services"]["minio"]
    assert "minio-data:/data" not in store["volumes"]
    assert "minio-data" in production["volumes"]
    for path in ("docker-compose.test.yaml", "tests/integration/reliability/compose.yaml"):
        test = yaml.safe_load((root / path).read_text())
        assert test["services"]["minio"]["image"] == store["image"]
