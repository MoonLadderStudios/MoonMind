import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from moonmind.schemas import Manifest, export_schema


SAMPLE_PATH = Path("samples/github_manifest.yaml")


def test_happy_path():
    manifest = Manifest.model_validate_yaml(SAMPLE_PATH)
    assert manifest.spec.readers[0].type == "GithubRepositoryReader"


def test_missing_readers():
    bad_yaml = """
apiVersion: moonmind/v1
kind: Readers
metadata: {}
spec: {}
"""
    with pytest.raises(ValidationError):
        Manifest.model_validate_yaml(bad_yaml)


def test_auth_xor_rule():
    bad_yaml = """
apiVersion: moonmind/v1
kind: Readers
metadata: {}
spec:
  auth:
    token:
      value: abc
      secretRef:
        provider: env
        key: TOKEN
  readers: []
"""
    with pytest.raises(ValidationError):
        Manifest.model_validate_yaml(bad_yaml)


def test_export_schema(tmp_path):
    out = tmp_path / "out.json"
    export_schema(out)
    data = json.loads(out.read_text())
    assert data.get("title") == "Manifest"
