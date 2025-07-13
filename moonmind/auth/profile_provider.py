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
        profile = await self.profile_svc.get_or_create_profile(self.db, user.id)
        field = manifest_key_to_profile_field(key)
        value = getattr(profile, field, None)
        if value is None and hasattr(profile, "id"):
            from api_service.db.models import UserProfile

            db_profile = await self.db.get(UserProfile, profile.id)
            if db_profile is not None:
                value = getattr(db_profile, field, None)
        return RedactedSecret(value) if value else None
