from typing import Protocol

from api_service.db.models import User


class AuthProvider(Protocol):
    async def get_secret(
        self, *, key: str, user: User | None, **kwargs
    ) -> str | None: ...
