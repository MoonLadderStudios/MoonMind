from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import User
from api_service.services.profile_service import ProfileService

from .providers import AuthProvider
from .utils import RedactedSecret, manifest_key_to_profile_field


class ProfileAuthProvider(AuthProvider):
    def __init__(self, db: AsyncSession, profile_svc: ProfileService) -> None:
        self.db = db
        self.profile_svc = profile_svc

    async def get_secret(self, *, key: str, user: User | None, **kwargs) -> str | None:
        if not user:
            return None
        try:
            profile = await self.profile_svc.get_profile_by_user_id(self.db, user.id)
            if profile is None:
                await self.profile_svc.get_or_create_profile(self.db, user.id)
                profile = await self.profile_svc.get_profile_by_user_id(
                    self.db, user.id
                )
        except Exception as exc:  # pragma: no cover - graceful fallback on DB errors
            import logging

            logging.warning("Profile lookup failed: %s", exc)
            return None

        field = manifest_key_to_profile_field(key)
        value = getattr(profile, field, None)
        return RedactedSecret(value) if value else None
