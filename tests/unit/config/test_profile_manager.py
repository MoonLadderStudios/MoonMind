import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Adjust import path based on project structure
# Assuming tests are run from the project root or PYTHONPATH is set up
from moonmind.config.profile_manager import ProfileManager
from moonmind.config.settings import AppSettings # Actual AppSettings for structure

# Minimal AppSettings mock for testing serialization
class MockAppSettings:
    def __init__(self, **kwargs):
        self.data = kwargs

    def model_dump(self, exclude_unset=True, mode='json'):
        # Simulate Pydantic's model_dump behavior for simple cases
        return self.data

class TestProfileManager(unittest.TestCase):

    def setUp(self):
        # Create a temporary directory for profiles.json
        self.test_dir = tempfile.mkdtemp()
        self.profiles_path = Path(self.test_dir) / "profiles.json"
        # Ensure AppSettings can be instantiated for tests that need it
        # We can patch specific AppSettings methods if they cause issues (like external calls)
        # For basic structure and model_dump, the real one should be fine if it has simple fields
        # or we can use a more sophisticated mock if needed.
        self.mock_settings_data = {
            "default_chat_provider": "test_provider",
            "default_embedding_provider": "test_embed_provider",
            "log_level": "DEBUG"
        }
        # Use the actual AppSettings to ensure model_dump compatibility
        # For this, we assume AppSettings can be instantiated with some basic data
        # or that its default factory methods don't cause side effects in a unit test context.
        # If AppSettings is complex, more specific mocking of its instantiation or model_dump might be needed.
        try:
            self.sample_app_settings = AppSettings(**self.mock_settings_data)
        except Exception as e:
            # Fallback to simple mock if AppSettings instantiation is problematic for unit tests
            print(f"Warning: Could not instantiate real AppSettings for ProfileManager tests due to: {e}. Using basic mock.")
            self.sample_app_settings = MockAppSettings(**self.mock_settings_data)


    def tearDown(self):
        # Remove the temporary directory after tests
        shutil.rmtree(self.test_dir)

    def test_init_no_existing_file(self):
        """Test ProfileManager initialization when profiles.json doesn't exist."""
        self.assertFalse(self.profiles_path.exists())
        pm = ProfileManager(self.profiles_path)
        self.assertEqual(pm.profiles, {})
        # ProfileManager should create the directory but not the file itself until save
        self.assertTrue(self.profiles_path.parent.exists())
        self.assertFalse(self.profiles_path.exists()) # _load_profiles doesn't create file, _save_profiles does

    def test_init_with_existing_valid_file(self):
        """Test ProfileManager initialization with a valid profiles.json."""
        initial_profiles = {"dev": {"setting1": "value1"}}
        with open(self.profiles_path, "w") as f:
            json.dump(initial_profiles, f)

        pm = ProfileManager(self.profiles_path)
        self.assertEqual(pm.profiles, initial_profiles)

    def test_init_with_existing_invalid_file(self):
        """Test ProfileManager initialization with a corrupted profiles.json."""
        with open(self.profiles_path, "w") as f:
            f.write("this is not json")

        pm = ProfileManager(self.profiles_path)
        self.assertEqual(pm.profiles, {}) # Should default to empty if JSON is invalid

    def test_save_and_load_profile(self):
        """Test saving a profile and then loading it."""
        pm = ProfileManager(self.profiles_path)
        profile_name = "test_profile"

        pm.save_profile(profile_name, self.sample_app_settings)

        self.assertTrue(self.profiles_path.exists())

        # Create a new ProfileManager instance to simulate fresh load
        pm_new = ProfileManager(self.profiles_path)
        loaded_data = pm_new.get_profile_data(profile_name)

        self.assertIsNotNone(loaded_data)
        # self.sample_app_settings.model_dump(exclude_unset=True, mode='json') is what's saved
        expected_data = self.sample_app_settings.model_dump(exclude_unset=True, mode='json')
        self.assertEqual(loaded_data, expected_data)

    def test_save_profile_empty_name(self):
        """Test saving a profile with an empty name."""
        pm = ProfileManager(self.profiles_path)
        with self.assertRaises(ValueError):
            pm.save_profile("", self.sample_app_settings)
        with self.assertRaises(ValueError):
            pm.save_profile("   ", self.sample_app_settings) # Whitespace only

    def test_get_profile_names(self):
        """Test retrieving profile names."""
        pm = ProfileManager(self.profiles_path)
        pm.save_profile("profile1", self.sample_app_settings)
        pm.save_profile("profile2", self.sample_app_settings)

        names = pm.get_profile_names()
        self.assertIn("profile1", names)
        self.assertIn("profile2", names)
        self.assertEqual(len(names), 2)

    def test_get_profile_data_non_existent(self):
        """Test retrieving data for a non-existent profile."""
        pm = ProfileManager(self.profiles_path)
        self.assertIsNone(pm.get_profile_data("non_existent_profile"))

    def test_delete_profile(self):
        """Test deleting an existing profile."""
        pm = ProfileManager(self.profiles_path)
        profile_name = "to_delete"
        pm.save_profile(profile_name, self.sample_app_settings)

        self.assertTrue(pm.delete_profile(profile_name))
        self.assertIsNone(pm.get_profile_data(profile_name))
        self.assertNotIn(profile_name, pm.get_profile_names())

        # Verify file content
        if self.profiles_path.exists():
            with open(self.profiles_path, "r") as f:
                file_content = json.load(f)
            self.assertNotIn(profile_name, file_content)

    def test_delete_profile_non_existent(self):
        """Test deleting a non-existent profile."""
        pm = ProfileManager(self.profiles_path)
        self.assertFalse(pm.delete_profile("non_existent_profile"))

    def test_atomic_save(self):
        """Test that _save_profiles performs an atomic write (move operation)."""
        pm = ProfileManager(self.profiles_path)
        profile_name = "atomic_test"

        # Mock shutil.move to verify it's called
        with patch('shutil.move') as mock_move:
            pm.save_profile(profile_name, self.sample_app_settings)
            mock_move.assert_called_once()
            # Check args: shutil.move(temp_file_path_str, profiles_path_str)
            args, _ = mock_move.call_args
            self.assertTrue(str(args[0]).endswith(".tmp"))
            self.assertEqual(str(args[1]), str(self.profiles_path))

        # Ensure the file was actually created correctly
        self.assertTrue(self.profiles_path.exists())
        with open(self.profiles_path, "r") as f:
            data = json.load(f)
        self.assertIn(profile_name, data)


    def test_save_multiple_profiles(self):
        """Test saving multiple profiles and ensuring they are all present."""
        pm = ProfileManager(self.profiles_path)
        profile1_name = "multi_profile1"
        profile2_name = "multi_profile2"

        settings1_data = {"default_chat_provider": "provider_A"}
        settings1 = AppSettings(**settings1_data) if isinstance(self.sample_app_settings, AppSettings) else MockAppSettings(**settings1_data)


        settings2_data = {"default_chat_provider": "provider_B"}
        settings2 = AppSettings(**settings2_data) if isinstance(self.sample_app_settings, AppSettings) else MockAppSettings(**settings2_data)

        pm.save_profile(profile1_name, settings1)
        pm.save_profile(profile2_name, settings2)

        # New manager to ensure loading from file
        pm_new = ProfileManager(self.profiles_path)
        self.assertIn(profile1_name, pm_new.get_profile_names())
        self.assertIn(profile2_name, pm_new.get_profile_names())

        profile1_data = pm_new.get_profile_data(profile1_name)
        profile2_data = pm_new.get_profile_data(profile2_name)

        self.assertEqual(profile1_data["default_chat_provider"], "provider_A")
        self.assertEqual(profile2_data["default_chat_provider"], "provider_B")

if __name__ == '__main__':
    unittest.main()
