import uuid
from typing import Optional
from pydantic import BaseModel


class UserProfileBaseSchema(BaseModel):
    google_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    # Add other profile fields here as they are defined in the UserProfile model

    class Config:
        orm_mode = (
            True  # Kept for compatibility, though Pydantic V2 prefers model_validate
        )


class UserProfileRead(
    UserProfileBaseSchema
):  # Renamed UserProfileSchema to UserProfileRead
    id: int  # Assuming 'id' is the primary key of UserProfile model
    user_id: uuid.UUID
    # google_api_key is inherited from UserProfileBaseSchema
    # Configuration for ORM mode is inherited


class UserProfileUpdate(UserProfileBaseSchema):
    # Inherits fields from UserProfileBaseSchema, e.g., google_api_key
    # No additional fields needed for update beyond what's in base, unless specified
    pass


# UserProfileCreateSchema remains as is, it was already defined and seems okay.
class UserProfileCreateSchema(UserProfileBaseSchema):
    # This schema might be used if creation requires specific fields or is different from update.
    # For now, it's similar to UserProfileBaseSchema.
    pass
