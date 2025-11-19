import unittest
from pathlib import Path
from moonmind.workflows.speckit_celery.storage import ArtifactStorage
import tempfile
import shutil

class TestArtifactStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.artifact_root = Path(self.temp_dir) / "artifacts"
        self.storage = ArtifactStorage(str(self.artifact_root))

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_get_run_path(self):
        run_id = "test_run_123"
        expected_path = self.artifact_root / run_id
        self.assertEqual(self.storage.get_run_path(run_id), expected_path)

    def test_store_and_get_artifact_metadata(self):
        run_id = "test_run_456"
        artifact_name = "test_artifact.txt"

        # Create a dummy artifact file
        source_file = Path(self.temp_dir) / "source.txt"
        with open(source_file, "w") as f:
            f.write("This is a test artifact.")

        # Store the artifact
        metadata = self.storage.store_artifact(run_id, source_file, artifact_name)

        # Verify the metadata
        self.assertEqual(metadata["name"], artifact_name)
        self.assertTrue(metadata["path"].endswith(artifact_name))
        self.assertGreater(metadata["size"], 0)
        self.assertIsNotNone(metadata["created_at"])
        self.assertIsNotNone(metadata["modified_at"])
        self.assertTrue(metadata["digest"].startswith("sha256:"))

        # Verify the file was stored
        stored_path = self.storage.get_run_path(run_id) / artifact_name
        self.assertTrue(stored_path.exists())

if __name__ == '__main__':
    unittest.main()
