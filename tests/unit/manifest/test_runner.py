import logging
from types import SimpleNamespace
from unittest.mock import patch

from moonmind.manifest.runner import ManifestRunner
from moonmind.schemas import Manifest

yaml_str = """
apiVersion: moonmind/v1
kind: Readers
metadata: {}
spec:
  readers:
    - type: DummyReader
      enabled: true
      init:
        token: 123
      load_data:
        - foo: bar
"""


class DummyReader:
    def __init__(self, token: str):
        self.token = token

    def load_data(self, foo: str):
        return [f"{self.token}-{foo}"]


def test_runner_instantiates_and_runs():
    manifest = Manifest.model_validate_yaml(yaml_str)

    with patch(
        "moonmind.manifest.runner.download_loader", return_value=DummyReader
    ) as dl:
        runner = ManifestRunner(manifest, logger=logging.getLogger("test"))
        results = runner.run()

    dl.assert_called_once_with("DummyReader")
    assert results["DummyReader"][0] == ["123-bar"]


def test_runner_handles_errors():
    class BadReader(DummyReader):
        def load_data(self, foo: str):
            raise RuntimeError("boom")

    manifest = Manifest.model_validate_yaml(yaml_str)

    with patch("moonmind.manifest.runner.download_loader", return_value=BadReader):
        runner = ManifestRunner(manifest, logger=logging.getLogger("test"))
        results = runner.run()

    # Error during load_data should result in empty list for the reader
    assert results["DummyReader"] == []


class DummyChild:
    def __init__(self, value: str):
        self.value = value


class ParentReader:
    def __init__(self, child: DummyChild):
        self.child = child

    def load_data(self):
        return [self.child.value]


def test_runner_recursive_initialization():
    yaml_nested = """
apiVersion: moonmind/v1
kind: Readers
metadata: {}
spec:
  readers:
    - type: ParentReader
      enabled: true
      init:
        child:
          _type: tests.unit.manifest.test_runner.DummyChild
          _init:
            value: hello
      load_data:
        - {}
"""

    manifest = Manifest.model_validate_yaml(yaml_nested)

    with patch("moonmind.manifest.runner.download_loader", return_value=ParentReader):
        runner = ManifestRunner(manifest, logger=logging.getLogger("test"))
        results = runner.run()

    assert results["ParentReader"][0] == ["hello"]


def test_runner_falls_back_when_download_loader_is_unavailable(monkeypatch):
    manifest = Manifest.model_validate_yaml(yaml_str)

    monkeypatch.setattr(
        "moonmind.manifest.runner.download_loader",
        lambda _type_name: (_ for _ in ()).throw(ImportError("missing")),
    )

    def fake_import_module(name: str):
        if name == "llama_index.readers.dummyreader":
            return SimpleNamespace(DummyReader=DummyReader)
        raise AssertionError(f"unexpected import: {name}")

    with patch("moonmind.manifest.runner.import_module", side_effect=fake_import_module):
        runner = ManifestRunner(manifest, logger=logging.getLogger("test"))
        results = runner.run()

    assert results["DummyReader"][0] == ["123-bar"]
