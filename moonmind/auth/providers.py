from typing import Any, Protocol


class AuthProvider(Protocol):
    async def get_secret(
        self, *, key: str, user: Any | None = None, **kwargs: Any
    ) -> str | None: ...
