import os

from .providers import AuthProvider
from .utils import RedactedSecret

class EnvAuthProvider(AuthProvider):
    async def get_secret(
        self, *, key: str
    ) -> str | None:
        value = os.getenv(key)
        return RedactedSecret(value) if value else None
