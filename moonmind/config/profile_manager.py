import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any

# Adjusted to import AppSettings from .settings
from .settings import AppSettings

class ProfileManager:
    """Manages loading, saving, and updating configuration profiles."""

    def __init__(self, profiles_path: Path):
        self.profiles_path = profiles_path
        self.profiles: Dict[str, Dict[str, Any]] = {}
        self._load_profiles()

    def _load_profiles(self) -> None:
        """Loads profiles from the JSON file if it exists."""
        if self.profiles_path.exists():
            try:
                with open(self.profiles_path, "r") as f:
                    self.profiles = json.load(f)
            except json.JSONDecodeError:
                # Handle cases where the file is corrupted or empty
                self.profiles = {}
                # Optionally, log this event
                print(f"Warning: Could not decode JSON from {self.profiles_path}. Starting with empty profiles.")
        else:
            self.profiles_path.parent.mkdir(parents=True, exist_ok=True)
            self.profiles = {}

    def _save_profiles(self) -> None:
        """Saves the current profiles dictionary to the JSON file atomically."""
        temp_file_path = self.profiles_path.with_suffix(f"{self.profiles_path.suffix}.tmp")
        self.profiles_path.parent.mkdir(parents=True, exist_ok=True) # Ensure directory exists
        with open(temp_file_path, "w") as f:
            json.dump(self.profiles, f, indent=2)
        shutil.move(str(temp_file_path), str(self.profiles_path))

    def get_profile_names(self) -> List[str]:
        """Returns a list of all available profile names."""
        return list(self.profiles.keys())

    def get_profile_data(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves the raw dictionary data for a given profile.
        Returns None if the profile does not exist.
        """
        return self.profiles.get(name)

    def save_profile(self, name: str, settings: AppSettings) -> None:
        """
        Saves or updates a profile using a Pydantic AppSettings object.
        The AppSettings object is converted to a dictionary for storage.
        """
        if not name or not name.strip():
            raise ValueError("Profile name cannot be empty or whitespace.")
        # Use model_dump to get a serializable dictionary, excluding unset fields
        # and ensuring models are converted to dicts.
        self.profiles[name] = settings.model_dump(exclude_unset=True, mode='json')
        self._save_profiles()

    def delete_profile(self, name: str) -> bool:
        """
        Deletes a profile by name. Returns True if successful, False otherwise.
        """
        if name in self.profiles:
            del self.profiles[name]
            self._save_profiles()
            return True
        return False
