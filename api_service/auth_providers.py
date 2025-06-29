import uuid

import requests  # For fetching Google OIDC discovery document
from fastapi import Depends, HTTPException, Request, Security
from fastapi_keycloak import FastAPIKeycloak
from jose import JWTError
from jwt import PyJWKClient
from jwt import decode as jwt_decode
from jwt.exceptions import PyJWTError
from pydantic import BaseModel
from sqlalchemy.future import select

from moonmind.config.settings import settings

from .db.base import get_async_session
from .db.models import User


class GoogleClaims(BaseModel):
    sub: str
    email: str
    # Add other claims as needed

def get_current_user():
    provider = settings.oidc.AUTH_PROVIDER
    if provider == "disabled":
        from api_service.auth import current_active_user
        return current_active_user

    if provider == "google":
        return _google_dep()
    # Default to local (keycloak) if AUTH_PROVIDER is "local" or any other unrecognized value
    return _keycloak_dep()


def _keycloak_dep():
    if not settings.oidc.OIDC_ISSUER_URL or not settings.oidc.OIDC_CLIENT_ID:
        # This case should ideally be prevented by startup validation if Keycloak is the provider
        async def misconfigured_dep():
            raise HTTPException(status_code=500, detail="Keycloak OIDC provider is not configured properly.")
        return misconfigured_dep

    kc = FastAPIKeycloak(
        server_url=settings.oidc.OIDC_ISSUER_URL,
        realm="moonmind", # This is specified in the docker-compose and realm export
        client_id=settings.oidc.OIDC_CLIENT_ID,
        client_secret=settings.oidc.OIDC_CLIENT_SECRET or None, # Handles optional secret
        verify=True,
        # algorithm="RS256", # Default is RS256, explicitly setting if needed
        # audience=settings.oidc.OIDC_CLIENT_ID # Usually client_id is the audience
    )
    scheme = kc.get_auth_scheme()

    async def dep(token: str = Security(scheme),
                  db=Depends(get_async_session)):
        try:
            # FastAPIKeycloak's get_user already decodes and validates the token
            # It returns a Pydantic model with claims based on the token
            claims = kc.get_user(token) # contains .sub, .email, etc.
            if not claims or not hasattr(claims, 'sub') or not hasattr(claims, 'email'):
                raise HTTPException(status_code=401, detail="Invalid token claims from Keycloak.")

            user = await db.get(User, uuid.UUID(claims.sub)) # Keycloak sub is usually a UUID
            if not user:
                user = User(id=uuid.UUID(claims.sub),
                            email=claims.email,
                            is_active=True, is_verified=True, # Assume active and verified from IdP
                            oidc_provider="keycloak",
                            oidc_subject=claims.sub)
                db.add(user)
                await db.commit()
                await db.refresh(user) # Ensure relationship attributes are loaded if any
            return user
        except JWTError as e:
            raise HTTPException(status_code=401, detail=f"Keycloak token error: {str(e)}")
        except Exception as e:
            # Catch other potential errors during user retrieval or creation
            raise HTTPException(status_code=500, detail=f"Error processing Keycloak authentication: {str(e)}")
    return dep


def _google_dep():
    if not settings.oidc.OIDC_ISSUER_URL or not settings.oidc.OIDC_CLIENT_ID:
        async def misconfigured_dep():
            raise HTTPException(status_code=500, detail="Google OIDC provider is not configured properly.")
        return misconfigured_dep


    # Define a Bearer scheme for Google (can be any standard OAuth2 compatible scheme)
    # We are not using FastAPIKeycloak here, so we need to handle token validation manually.
    from fastapi.security import \
        OAuth2PasswordBearer  # Or OAuth2AuthorizationCodeBearer if more appropriate

    # Using a generic name for the tokenUrl as it's not strictly used for validation here,
    # but required by OAuth2PasswordBearer. The actual token comes in the Authorization header.
    google_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/google/token") # Placeholder tokenUrl

    async def dep(request: Request,
                  token: str = Security(google_oauth2_scheme),
                  db=Depends(get_async_session)):
        try:
            # Manually validate Google token
            # 1. Get Google's public keys
            jwks_uri = "https://www.googleapis.com/oauth2/v3/certs"
            jwks_client = PyJWKClient(jwks_uri)
            signing_key = jwks_client.get_signing_key_from_jwt(token)

            # 2. Decode and validate the token
            decoded_token = jwt_decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=settings.oidc.OIDC_CLIENT_ID,
                issuer=settings.oidc.OIDC_ISSUER_URL,
                options={"verify_exp": True}
            )
            claims = GoogleClaims(**decoded_token)

            # 3. Process user based on claims
            user = await db.execute(select(User).where(User.oidc_subject == claims.sub, User.oidc_provider == "google"))
            user = user.scalars().first()

            if not user:
                user = User(id=uuid.uuid4(),
                            email=claims.email,
                            is_active=True, is_verified=True,
                            oidc_provider="google",
                            oidc_subject=claims.sub)
                db.add(user)
                await db.commit()
                await db.refresh(user)
            return user
        except PyJWTError as e:
            raise HTTPException(status_code=401, detail=f"Google token error: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error processing Google authentication: {str(e)}")
    return dep
