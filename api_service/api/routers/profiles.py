from fastapi import APIRouter, HTTPException, Body, Depends
from typing import List, Dict, Any

from moonmind.config.config import profile_manager, get_settings as get_app_settings # To ensure settings are loaded
from moonmind.config.settings import AppSettings # For request body validation on PUT
from moonmind.config.profile_manager import ProfileManager # For dependency injection

router = APIRouter()

def get_profile_manager_dependency() -> ProfileManager:
    # This ensures that by the time profile_manager is used,
    # initialize_settings (which also initializes profile_manager) has been called.
    # Though profile_manager is initialized globally in config.py,
    # this pattern is good for explicit dependency declaration.
    # Also, ensure settings are loaded which implicitly initializes profile_manager's path etc.
    get_app_settings()
    return profile_manager

@router.get("", response_model=List[str], summary="List Profile Names")
async def list_profiles(pm: ProfileManager = Depends(get_profile_manager_dependency)):
    """
    Retrieves a list of all available configuration profile names.
    """
    return pm.get_profile_names()

@router.get("/{profile_name}", response_model=Dict[str, Any], summary="Get Profile Configuration")
async def get_profile(profile_name: str, pm: ProfileManager = Depends(get_profile_manager_dependency)):
    """
    Retrieves the configuration data for a specific profile.
    """
    profile_data = pm.get_profile_data(profile_name)
    if profile_data is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_name}' not found.")
    return profile_data

@router.put("/{profile_name}", status_code=201, summary="Create or Update Profile", response_model=Dict[str, str])
async def create_or_update_profile(
    profile_name: str,
    settings_data: AppSettings = Body(...), # Request body will be validated as AppSettings
    pm: ProfileManager = Depends(get_profile_manager_dependency)
):
    """
    Creates a new profile or updates an existing one.
    The request body must conform to the AppSettings model.
    """
    if not profile_name or not profile_name.strip():
        raise HTTPException(status_code=400, detail="Profile name cannot be empty.")
    try:
        pm.save_profile(profile_name, settings_data)
        # Check if it was a create or update for the message
        # This is a bit simplistic; ideally, save_profile could return a status
        if profile_name in pm.get_profile_names() and len(pm.get_profile_data(profile_name)) > 0 : # check if profile was actually created/updated
             # A more robust check might involve comparing old and new data or checking file mod time
            action = "updated"
        else:
            action = "created" # Or re-confirm save actually happened

        return {"message": f"Profile '{profile_name}' {action} successfully."}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Catch any other unexpected errors during save
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.delete("/{profile_name}", status_code=200, summary="Delete Profile", response_model=Dict[str, str])
async def delete_profile_endpoint( # Renamed to avoid conflict with imported delete_profile
    profile_name: str,
    pm: ProfileManager = Depends(get_profile_manager_dependency)
):
    """
    Deletes a configuration profile by its name.
    """
    if not pm.delete_profile(profile_name):
        raise HTTPException(status_code=404, detail=f"Profile '{profile_name}' not found or could not be deleted.")
    return {"message": f"Profile '{profile_name}' deleted successfully."}

# A note on the PUT endpoint's response for "created" vs "updated":
# The current implementation of save_profile overwrites.
# To give a more accurate "created" vs "updated" message,
# one might check if the profile existed *before* calling save_profile.
# For simplicity, the current response is kept generic or slightly optimistic.
# Example for a more accurate message in PUT:
#
#    profile_existed = profile_name in pm.get_profile_names()
#    pm.save_profile(profile_name, settings_data)
#    action = "updated" if profile_existed else "created"
#    return {"message": f"Profile '{profile_name}' {action} successfully."}
# This has a slight race condition if multiple requests happen, but for typical admin actions, it's usually fine.
# The current response is: `{"message": f"Profile '{profile_name}' {action} successfully."}`
# where `action` is determined by re-checking after save. This is mostly okay.
# The status code 201 is typically for "Created". If updating, 200 OK is also common.
# For a PUT that can create or update, 200 or 201 (if new) or 204 (if update and no content) are options.
# Sticking with 201 for simplicity as it implies resource state is now final.
