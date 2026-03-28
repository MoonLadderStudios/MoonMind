import json
import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken

from api_service.core.encryption import get_encryption_key
from moonmind.auth.secrets import MasterSecretResolver
from api_service.db.base import get_async_session
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["proxy"], prefix="/proxy")

async def _verify_and_decode_proxy_token(token: str) -> dict:
    try:
        if token.startswith("mm-proxy-token:"):
            token = token[len("mm-proxy-token:"):]
        fernet = Fernet(get_encryption_key().encode("utf-8"))
        payload_bytes = fernet.decrypt(token.encode("utf-8"))
        return json.loads(payload_bytes.decode("utf-8"))
    except (InvalidToken, json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Failed to decode proxy token: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired proxy token")

async def _resolve_provider_key(provider: str, secret_refs: dict, session: AsyncSession) -> str:
    # Handle known keys mapping
    ref = None
    if provider == "anthropic" or provider == "minimax":
        ref = secret_refs.get("anthropic_api_key") or secret_refs.get("provider_api_key")
    elif provider == "openai":
        ref = secret_refs.get("openai_api_key") or secret_refs.get("provider_api_key")
    
    # Generic fallback
    if not ref:
        for k, v in secret_refs.items():
            if v.startswith("db_encrypted:"):
                ref = v
                break

    if not ref:
        raise HTTPException(status_code=400, detail=f"No secret reference found for proxy provider {provider}")

    resolver = MasterSecretResolver()
    return await resolver.resolve_secret(session, ref)


@router.api_route("/{provider_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_pass_through(
    provider_id: str,
    path: str,
    request: Request,
    session: AsyncSession = Depends(get_async_session)
):
    """
    Transparent proxy endpoint for agent runtimes executing in proxy-first mode.
    Takes a symmetric-encrypted token (MOONMIND_PROXY_TOKEN), extracts the secret reference,
    resolves the real secret via the database, and proxies to the upstream URL.
    """
    auth_header = request.headers.get("Authorization") or request.headers.get("x-api-key")
    
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing proxy credential header")

    token = auth_header
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
        
    payload = await _verify_and_decode_proxy_token(token)
    
    if payload.get("provider") != provider_id:
        raise HTTPException(status_code=403, detail="Token provider mismatch")

    provider_secret = await _resolve_provider_key(provider_id, payload.get("secret_refs", {}), session)

    provider_id = provider_id.lower()
    if provider_id == "anthropic":
        target_url = f"https://api.anthropic.com/{path}"
    elif provider_id == "openai":
        # Usually path already includes /v1 if they replaced OPENAI_BASE_URL to proxy/openai/v1
        target_url = f"https://api.openai.com/{path}"
    elif provider_id == "minimax":
        target_url = f"https://api.minimax.io/{path}"
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported proxy provider {provider_id}")

    # Forward headers safely
    headers = dict(request.headers)
    headers.pop("host", None)
    
    if provider_id == "anthropic":
        headers["x-api-key"] = provider_secret
        headers.pop("authorization", None)
    elif provider_id in ("openai", "minimax"):
        headers["authorization"] = f"Bearer {provider_secret}"
        headers.pop("x-api-key", None)

    body = await request.body()
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            upstream_response = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
                params=request.query_params
            )
        except httpx.RequestError as exc:
            logger.error(f"Proxy pass-through failed to {target_url}: {exc}")
            raise HTTPException(status_code=502, detail="Error communicating with upstream provider")

    # Pass the upstream response back to the client
    response_headers = dict(upstream_response.headers)
    for exclude_header in ["content-encoding", "content-length", "transfer-encoding"]:
        response_headers.pop(exclude_header, None)
        
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type")
    )
