import logging
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
