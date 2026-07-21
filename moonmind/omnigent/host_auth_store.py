"""Transactional persistence owner for safe embedded host-auth metadata."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update

from api_service.db.models import OmnigentHostAuthProfileRecord
from moonmind.omnigent.host_auth_profile import (
    HostAuthCredentialProfile,
    profile_from_metadata,
    profile_persistence_metadata,
    revoke_host_auth_profile,
    rotate_host_auth_profile,
)


class HostAuthProfileStore:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def get_active(self) -> HostAuthCredentialProfile | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(OmnigentHostAuthProfileRecord).where(
                    OmnigentHostAuthProfileRecord.active.is_(True)
                )
            )
            return profile_from_metadata(row.metadata_json) if row else None

    async def put(
        self,
        profile: HostAuthCredentialProfile,
        *,
        expected_generation: int | None = None,
    ) -> HostAuthCredentialProfile:
        if not profile.revoked:
            profile.validate()
        elif (
            not profile.profile_id
            or not profile.current_secret_ref
            or profile.current_generation < 1
        ):
            raise ValueError("revoked host-auth metadata is incomplete")
        async with self._session_factory() as session, session.begin():
            active = await session.scalar(
                select(OmnigentHostAuthProfileRecord)
                .where(OmnigentHostAuthProfileRecord.active.is_(True))
                .with_for_update()
            )
            active_generation = (
                int(active.metadata_json.get("currentGeneration", 0)) if active else 0
            )
            if (
                expected_generation is not None
                and active_generation != expected_generation
            ):
                raise RuntimeError("host-auth profile changed during lifecycle update")
            await session.execute(
                update(OmnigentHostAuthProfileRecord).values(active=False)
            )
            row = await session.get(OmnigentHostAuthProfileRecord, profile.profile_id)
            metadata = profile_persistence_metadata(profile)
            if row is None:
                session.add(OmnigentHostAuthProfileRecord(
                    profile_id=profile.profile_id, metadata_json=metadata, active=True
                ))
            else:
                row.metadata_json = metadata
                row.active = True
                row.updated_at = datetime.now().astimezone()
        return profile

    async def rotate(
        self, *, new_secret_ref: str, overlap: timedelta
    ) -> HostAuthCredentialProfile:
        current = await self.get_active()
        if current is None:
            raise LookupError("managed embedded host authentication is unconfigured")
        # Validation happens before put starts its transaction, so failure cannot
        # alter the durable current generation.
        return await self.put(
            rotate_host_auth_profile(
                current, new_secret_ref=new_secret_ref, overlap=overlap
            ),
            expected_generation=current.current_generation,
        )

    async def revoke(self) -> HostAuthCredentialProfile:
        current = await self.get_active()
        if current is None:
            raise LookupError("managed embedded host authentication is unconfigured")
        return await self.put(
            revoke_host_auth_profile(current),
            expected_generation=current.current_generation,
        )
