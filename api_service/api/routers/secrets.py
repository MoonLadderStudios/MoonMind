import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.base import get_async_session
from api_service.api.schemas import (
    SecretCreateRequest,
    SecretListResponse,
    SecretMetadataResponse,
    SecretStatusUpdateRequest,
    SecretUpdateRequest,
)
from api_service.db.models import SecretStatus
from api_service.services.secrets import SecretsService

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.post(
    "",
    response_model=SecretMetadataResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new managed secret",
    tags=["Secrets"],
)
async def create_secret(
    request: SecretCreateRequest,
    db: AsyncSession = Depends(get_async_session),
) -> SecretMetadataResponse:
    # Check if exists first? Or rely on DB constraints?
    # Usually slug is unique, let's just try to get it.
    existing = await SecretsService.get_secret(db, request.slug)
    # The get_secret only returns ACTIVE ones, let's list to see if any exists with this slug.
    # Actually wait get_secret is fine. If it exists, we shouldn't create it.
    
    # We will try to create it. Any IntegrityError from unique(slug) will bubble up, but we can prevent it.
    metadata = await SecretsService.list_metadata(db)
    if any(m.slug == request.slug for m in metadata):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Secret with slug '{request.slug}' already exists.",
        )

    secret = await SecretsService.create_secret(
        db=db,
        slug=request.slug,
        plaintext=request.plaintext,
        details=request.details,
    )
    return SecretMetadataResponse.model_validate(secret)


@router.get(
    "",
    response_model=SecretListResponse,
    summary="List metadata for all managed secrets",
    tags=["Secrets"],
)
async def list_secrets(
    db: AsyncSession = Depends(get_async_session),
) -> SecretListResponse:
    metadata = await SecretsService.list_metadata(db)
    items = [SecretMetadataResponse.model_validate(m) for m in metadata]
    return SecretListResponse(items=items)


@router.put(
    "/{slug}",
    response_model=SecretMetadataResponse,
    summary="Update an existing secret",
    tags=["Secrets"],
)
async def update_secret(
    slug: str,
    request: SecretUpdateRequest,
    db: AsyncSession = Depends(get_async_session),
) -> SecretMetadataResponse:
    secret = await SecretsService.update_secret(db, slug, request.plaintext)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
        )
    return SecretMetadataResponse.model_validate(secret)


@router.post(
    "/{slug}/rotate",
    response_model=SecretMetadataResponse,
    summary="Rotate an existing secret",
    tags=["Secrets"],
)
async def rotate_secret(
    slug: str,
    request: SecretUpdateRequest,
    db: AsyncSession = Depends(get_async_session),
) -> SecretMetadataResponse:
    secret = await SecretsService.rotate_secret(db, slug, request.plaintext)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
        )
    return SecretMetadataResponse.model_validate(secret)


@router.put(
    "/{slug}/status",
    response_model=SecretMetadataResponse,
    summary="Update the status of a secret",
    tags=["Secrets"],
)
async def update_secret_status(
    slug: str,
    request: SecretStatusUpdateRequest,
    db: AsyncSession = Depends(get_async_session),
) -> SecretMetadataResponse:
    new_status = SecretStatus(request.status)
    secret = await SecretsService.set_status(db, slug, new_status)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
        )
    return SecretMetadataResponse.model_validate(secret)


@router.delete(
    "/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a secret entirely",
    tags=["Secrets"],
)
async def delete_secret(
    slug: str,
    db: AsyncSession = Depends(get_async_session),
) -> None:
    deleted = await SecretsService.delete_secret(db, slug)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
        )


@router.get(
    "/{slug}/validate",
    summary="Validate that a secret reference is resolvable",
    tags=["Secrets"],
)
async def validate_secret(
    slug: str,
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, bool]:
    # Check if the secret exists and is active
    val = await SecretsService.get_secret(db, slug)
    return {"valid": val is not None}
