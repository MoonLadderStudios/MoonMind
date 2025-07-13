import moonmind.config as config_module
from moonmind.config.settings import AppSettings
from moonmind.manifest.loader import ManifestLoader
from moonmind.schemas import Manifest


def test_manifest_loader_merges_defaults(tmp_path, monkeypatch):
    manifest_content = """
apiVersion: moonmind/v1
kind: Readers
metadata: {}
spec:
  defaults:
    openai:
      openai_enabled: false
  readers: []
"""
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(manifest_content)

    monkeypatch.setattr(config_module, "settings", AppSettings())

    loader = ManifestLoader(str(manifest_path))
    manifest = loader.load()

    assert isinstance(manifest, Manifest)
    assert config_module.settings.openai.openai_enabled is False
