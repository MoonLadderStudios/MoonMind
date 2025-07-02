import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, AliasChoices


class UserProfileBaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    google_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("google_api_key", "google_api_key_encrypted"),
    )
    openai_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("openai_api_key", "openai_api_key_encrypted"),
    )
    # Add other profile fields here as they are defined in the UserProfile model


class UserProfileRead(UserProfileBaseSchema):  # Renamed UserProfileSchema to UserProfileRead
    id: int  # Assuming 'id' is the primary key of UserProfile model
    user_id: uuid.UUID
    # google_api_key is inherited from UserProfileBaseSchema
    # Configuration for ORM mode is inherited
    # google_api_key and openai_api_key are inherited from UserProfileBaseSchema
    # and will be present in this schema, suitable for internal use or when keys are needed.


# New schema for sanitized output, excluding sensitive API keys.
class UserProfileReadSanitized(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: uuid.UUID
    # Exclude sensitive fields by not including them in the schema


class UserProfileUpdate(UserProfileBaseSchema):
    # Inherits fields from UserProfileBaseSchema, e.g., google_api_key
    # No additional fields needed for update beyond what's in base, unless specified
    pass


# UserProfileCreateSchema remains as is, it was already defined and seems okay.
class UserProfileCreateSchema(UserProfileBaseSchema):
    # This schema might be used if creation requires specific fields or is different from update.
    # For now, it's similar to UserProfileBaseSchema.
    pass


class ApiKeyStatus(BaseModel):
    """Schema for displaying API key status."""
    model_config = ConfigDict(from_attributes=True)

    openai_api_key_set: bool = False
    # anthropic_api_key_set: bool = False # Example for other keys
    # Add other keys as needed
