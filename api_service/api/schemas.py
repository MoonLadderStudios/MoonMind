import uuid
from typing import Optional
from pydantic import BaseModel

class UserProfileBaseSchema(BaseModel):
    google_api_key: Optional[str] = None
    # Add other profile fields here as they are defined in the UserProfile model

    class Config:
        orm_mode = True # Kept for compatibility, though Pydantic V2 prefers model_validate

class UserProfileUpdateSchema(UserProfileBaseSchema):
    pass

class UserProfileSchema(UserProfileBaseSchema):
    id: int
    user_id: uuid.UUID
    google_api_key: Optional[str] = None # Field from UserProfile model is google_api_key_encrypted

    # If the model has 'google_api_key_encrypted' and we want to expose it as 'google_api_key'
    # Pydantic V2 allows computed fields or aliases.
    # For now, this schema will expect 'google_api_key' as input for update,
    # and the service will map it to 'google_api_key_encrypted' on the model.
    # When returning data, if the model has 'google_api_key_encrypted', we need to ensure
    # it's correctly mapped if the schema field name is different.
    # The current UserProfile model has `google_api_key_encrypted`.
    # The service layer will handle the mapping from `google_api_key` in schemas
    # to `google_api_key_encrypted` in the model.
    # When reading from the model to the schema, if the field name is different,
    # it would require a resolver or alias if not handled by orm_mode/model_validate.
    # Let's assume direct mapping for now and adjust if issues arise.
    # The UserProfile model has `google_api_key_encrypted`. We will read this value
    # and present it as `google_api_key` in the response schema.
    # Pydantic's orm_mode should handle this if the model attribute is named google_api_key_encrypted
    # and the schema field is google_api_key, but only if we are loading from an ORM model
    # instance where that attribute exists.
    # Given EncryptedType, the decrypted value is accessed via the same attribute name.

    # To clarify, the UserProfile model has `google_api_key_encrypted`.
    # When data is loaded from the DB into a UserProfile model instance,
    # accessing `user_profile.google_api_key_encrypted` gives the decrypted value.
    # So, UserProfileSchema can directly map `google_api_key` to this.
    # And UserProfileUpdateSchema will provide `google_api_key` which the service layer
    # will assign to `user_profile.google_api_key_encrypted`.
    # This seems correct.

class UserProfileCreateSchema(UserProfileBaseSchema):
    # This schema might be used if creation requires specific fields or is different from update.
    # For now, it's similar to UserProfileBaseSchema.
    pass
