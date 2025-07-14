import os

from api_service.db.models import User

from .providers import AuthProvider
from .utils import RedactedSecret


class EnvAuthProvider(AuthProvider):
    async def get_secret(
        self, *, key: str, user: User | None = None, **kwargs
    ) -> str | None:
        value = os.getenv(key)
        return RedactedSecret(value) if value else None
