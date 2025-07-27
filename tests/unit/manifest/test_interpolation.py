import pytest

from moonmind.manifest import InterpolationError, interpolate
from moonmind.schemas import Manifest


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


def test_secretref_profile_provider_with_env_fallback():
    yaml_str = """
apiVersion: moonmind/v1
kind: Readers
metadata: {}
spec:
  auth:
    token:
      secretRef:
        provider: profile
        key: MY_TOKEN
  readers:
    - type: Dummy
      enabled: true
      init:
        token: "${auth.token}"
"""
    manifest = Manifest.model_validate_yaml(yaml_str)
    env = {"MY_TOKEN": "envtok"}
    profile = {"MY_TOKEN": "profiletok"}
    result = interpolate(manifest, env, profile)
    init = result.spec.readers[0].init
    assert init["token"] == "profiletok"


def test_secretref_env_provider():
    yaml_str = """
apiVersion: moonmind/v1
kind: Readers
metadata: {}
spec:
  auth:
    token:
      secretRef:
        provider: env
        key: ENV_ONLY
  readers:
    - type: Dummy
      enabled: true
      init:
        token: "${auth.token}"
"""
    manifest = Manifest.model_validate_yaml(yaml_str)
    env = {"ENV_ONLY": "envtok"}
    result = interpolate(manifest, env)
    assert result.spec.readers[0].init["token"] == "envtok"


def test_secretref_unsupported_provider():
    yaml_str = """
apiVersion: moonmind/v1
kind: Readers
metadata: {}
spec:
  auth:
    token:
      secretRef:
        provider: unknown
        key: X
  readers:
    - type: Dummy
      enabled: true
      init:
        token: "${auth.token}"
"""
    manifest = Manifest.model_validate_yaml(yaml_str)
    with pytest.raises(InterpolationError):
        interpolate(manifest, {})
