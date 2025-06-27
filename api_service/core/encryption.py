from moonmind.config.settings import settings

def get_encryption_key() -> str:
    """
    Retrieves the encryption master key from Pydantic settings.
    Raises a ValueError if the key is not set.
    """
    encryption_key = settings.security.ENCRYPTION_MASTER_KEY
    if not encryption_key:
        raise ValueError("ENCRYPTION_MASTER_KEY is not set in the application settings.")
    return encryption_key
