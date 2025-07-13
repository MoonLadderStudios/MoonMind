import pytest

from moonmind.schemas import Manifest
from moonmind.manifest import interpolate, InterpolationError


def test_interpolate_success():
    yaml_str = """
apiVersion: moonmind/v1
kind: Readers
metadata: {}
spec:
  defaults:
    verbose: true
    api_url: http://example.com
  auth:
    github_token:
      value: abc123
  readers:
    - type: Dummy
      enabled: true
      init:
        token: "${auth.github_token}"
        url: "${defaults.api_url}"
        home: "${env.HOME_VAR}"
"""
    env = {"HOME_VAR": "/tmp"}
    manifest = Manifest.model_validate_yaml(yaml_str)
    result = interpolate(manifest, env)
    init = result.spec.readers[0].init
    assert init["token"] == "abc123"
    assert init["url"] == "http://example.com"
    assert init["home"] == "/tmp"


def test_interpolate_unresolved():
    yaml_str = """
apiVersion: moonmind/v1
kind: Readers
metadata: {}
spec:
  auth:
    token:
      value: secret
  readers:
    - type: Dummy
      enabled: true
      init:
        token: "${auth.missing}"
"""
    manifest = Manifest.model_validate_yaml(yaml_str)
    with pytest.raises(InterpolationError):
        interpolate(manifest, {})
