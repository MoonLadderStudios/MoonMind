import os
from pathlib import Path
from typing import Optional, Any

# Import AppSettings from the existing settings.py and ProfileManager from the new profile_manager.py
from .settings import AppSettings
from .profile_manager import ProfileManager

# --- Configuration for Profiles ---
# Determine the directory for profiles.json.
# Defaults to /app/config inside the container.
# Can be overridden by the MOONMIND_PROFILES_DIR environment variable.
PROFILES_DIR_STR = os.getenv("MOONMIND_PROFILES_DIR", "/app/config")
PROFILES_DIR = Path(PROFILES_DIR_STR)
PROFILES_FILE_PATH = PROFILES_DIR / "profiles.json"

# --- Global instances ---
# These will be initialized by the `initialize_settings` function.
# Application code will import `settings` and `profile_manager` from this module.

# Initialize ProfileManager globally
profile_manager = ProfileManager(PROFILES_FILE_PATH)

# Global settings object, to be populated at startup
settings: Optional[AppSettings] = None


def initialize_settings() -> AppSettings:
    """
    Initializes the global settings object based on the ACTIVE_PROFILE environment variable.
    This function should be called once at application startup.

    It loads configuration from a profile specified by ACTIVE_PROFILE.
    If the profile doesn't exist, it initializes AppSettings from environment
    variables and default values, then saves this configuration as the new profile.
    """
    global settings

    # 1. Determine which profile to load. Default to "development" if not set.
    active_profile_name = os.getenv("ACTIVE_PROFILE", "development")
    if not active_profile_name.strip():
        print("Warning: ACTIVE_PROFILE environment variable is empty. Using 'default' profile name.")
        active_profile_name = "default"


    # 2. Get the profile data from the manager
    profile_data = profile_manager.get_profile_data(active_profile_name)

    if profile_data:
        print(f"Loading settings from profile: '{active_profile_name}' using data: {profile_data}")
        # 3. Instantiate AppSettings with the profile data.
        # Pydantic will use this data first, then fall back to env vars and defaults for missing fields.
        current_settings = AppSettings(**profile_data)
    else:
        print(f"Profile '{active_profile_name}' not found in '{PROFILES_FILE_PATH}'.")
        print("Loading settings from environment variables and defaults, then saving as new profile.")
        # If profile doesn't exist, fall back to the original behavior (env vars, defaults)
        current_settings = AppSettings()
        # Save this configuration as the new profile
        try:
            profile_manager.save_profile(active_profile_name, current_settings)
            print(f"Successfully saved initial settings as profile '{active_profile_name}'.")
        except Exception as e:
            print(f"Error saving profile '{active_profile_name}': {e}")
            # Continue with settings loaded from env/defaults even if save fails

    settings = current_settings
    return settings

# --- Helper for dependency injection ---
# It's good practice to have a function that FastAPI can depend on.
def get_settings() -> AppSettings:
    """
    Dependency injector for FastAPI to get the global AppSettings object.
    Ensures that settings have been initialized.
    """
    if settings is None:
        # This case should ideally not be reached if initialize_settings() is called at startup.
        # However, as a fallback, initialize if not already done.
        print("Warning: get_settings() called before global settings initialized. Initializing now.")
        return initialize_settings()
    return settings

def get_profile_manager() -> ProfileManager:
    """
    Dependency injector for FastAPI to get the global ProfileManager object.
    """
    return profile_manager

# To make these available for direct import if needed, though get_settings is preferred for FastAPI
# Example: from moonmind.config.config import settings, profile_manager
# However, the initialize_settings() must be called first.
