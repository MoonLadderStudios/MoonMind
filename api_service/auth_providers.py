import uuid

import logging # Added logging
from fastapi import Depends, HTTPException, Security
from fastapi_keycloak import FastAPIKeycloak
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession # For type hinting
from sqlalchemy.future import select


from moonmind.config.settings import settings
from .db.base import get_async_session
from .db.models import User
from .auth import get_or_create_default_user, get_user_manager # Import new function and UserManager
from api_service.auth import UserManager # Ensure UserManager is available for type hinting

logger = logging.getLogger(__name__) # Added logger

def get_current_user(): # This function becomes a factory for the actual dependency
    provider = settings.oidc.AUTH_PROVIDER
    if provider == "disabled":
        logger.info("Auth provider is 'disabled'. Using default user dependency.")
        if not settings.oidc.DEFAULT_USER_ID or not settings.oidc.DEFAULT_USER_EMAIL:
            logger.error("DEFAULT_USER_ID or DEFAULT_USER_EMAIL not set for 'disabled' auth mode.")
            async def misconfigured_default_user_dep():
                raise HTTPException(
                    status_code=500,
                    detail="Default user not configured for disabled authentication mode."
                )
            return misconfigured_default_user_dep

        async def get_default_user_dependency(
            db: AsyncSession = Depends(get_async_session),
            user_manager: UserManager = Depends(get_user_manager)
        ) -> User:
            try:
                user = await get_or_create_default_user(db_session=db, user_manager=user_manager)
                if not user: # Should not happen if get_or_create_default_user raises error on failure
                    raise HTTPException(status_code=500, detail="Failed to get or create default user.")
                return user
            except ValueError as ve: # Catch specific errors from get_or_create_default_user
                logger.error(f"Configuration error for default user: {ve}")
                raise HTTPException(status_code=500, detail=str(ve))
            except HTTPException as he: # Re-raise HTTPExceptions
                raise he
            except Exception as e:
                logger.error(f"Unexpected error in default user dependency: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Error fetching default user: {e}")
        return get_default_user_dependency
    else: # Default to keycloak or other configured OIDC providers
        logger.info(f"Auth provider is '{provider}'. Using Keycloak OIDC dependency.")
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
