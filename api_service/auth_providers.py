import uuid

from fastapi import Depends, HTTPException, Security
from fastapi_keycloak import FastAPIKeycloak
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy.future import select

from moonmind.config.settings import settings

from .db.base import get_async_session
from .db.models import User


def get_current_user():
    provider = settings.oidc.AUTH_PROVIDER
    if provider == "disabled":
        from api_service.auth import current_active_user

        return current_active_user

    # Default to keycloak if auth is not disabled
    return _keycloak_dep()


def _keycloak_dep():
    if not settings.oidc.OIDC_ISSUER_URL or not settings.oidc.OIDC_CLIENT_ID:
        # This case should ideally be prevented by startup validation if Keycloak is the provider
        async def misconfigured_dep():
            raise HTTPException(
                status_code=500, detail="Keycloak OIDC provider is not configured properly."
            )

        return misconfigured_dep

    kc = FastAPIKeycloak(
        server_url=settings.oidc.OIDC_ISSUER_URL,
        realm="moonmind",  # This is specified in the docker-compose and realm export
        client_id=settings.oidc.OIDC_CLIENT_ID,
        client_secret=settings.oidc.OIDC_CLIENT_SECRET or None,  # Handles optional secret
        verify=True,
        # algorithm="RS256", # Default is RS256, explicitly setting if needed
        # audience=settings.oidc.OIDC_CLIENT_ID # Usually client_id is the audience
    )
    scheme = kc.get_auth_scheme()

    async def dep(token: str = Security(scheme), db=Depends(get_async_session)):
        try:
            # FastAPIKeycloak's get_user already decodes and validates the token
            # It returns a Pydantic model with claims based on the token
            claims = kc.get_user(token)  # contains .sub, .email, etc.
            if not claims or not hasattr(claims, "sub") or not hasattr(claims, "email"):
                raise HTTPException(
                    status_code=401, detail="Invalid token claims from Keycloak."
                )

            user = await db.get(
                User, uuid.UUID(claims.sub)
            )  # Keycloak sub is usually a UUID
            if not user:
                user = User(
                    id=uuid.UUID(claims.sub),
                    email=claims.email,
                    is_active=True,
                    is_verified=True,  # Assume active and verified from IdP
                    oidc_provider="keycloak",
                    oidc_subject=claims.sub,
                )
                db.add(user)
                await db.commit()
                await db.refresh(user)  # Ensure relationship attributes are loaded if any
            return user
        except JWTError as e:
            raise HTTPException(
                status_code=401, detail=f"Keycloak token error: {str(e)}"
            )
        except Exception as e:
            # Catch other potential errors during user retrieval or creation
            raise HTTPException(
                status_code=500,
                detail=f"Error processing Keycloak authentication: {str(e)}",
            )

    return dep
