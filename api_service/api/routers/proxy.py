import json
import logging
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken

from api_service.core.encryption import get_encryption_key
from api_service.db.base import get_async_session
from sqlalchemy.ext.asyncio import AsyncSession
from moonmind.config.providers import PROVIDERS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["proxy"], prefix="/proxy")

async def _verify_and_decode_proxy_token(token: str) -> dict:
    try:
        if token.startswith("mm-proxy-token:"):
            token = token[len("mm-proxy-token:"):]
        fernet = Fernet(get_encryption_key().encode("utf-8"))
        payload_bytes = fernet.decrypt(token.encode("utf-8"))
        payload = json.loads(payload_bytes.decode("utf-8"))
        
        # Validate expiration
        if "exp" in payload:
            if time.time() > payload["exp"]:
                raise ValueError("Token is expired")
        
        return payload
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
            if str(v).find("://") != -1: # Support typed backends <backend>://<locator>
                ref = str(v)
                break

    if not ref:
        raise HTTPException(status_code=400, detail=f"No secret reference found for proxy provider {provider}")

    from moonmind.workflows.temporal.runtime.managed_api_key_resolve import resolve_managed_api_key_reference
    try:
        return await resolve_managed_api_key_reference(str(ref))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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
    
    config = PROVIDERS.get(provider_id)
    if not config:
        raise HTTPException(status_code=400, detail=f"Unsupported proxy provider {provider_id}")

    target_url = f"{config['base_url']}/{path}"

    # Forward headers safely
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("authorization", None)
    headers.pop("x-api-key", None)
    
    header_key = config["auth_header_key"]
    header_val = config["auth_header_format"].format(token=provider_secret)
    headers[header_key] = header_val

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


for _method in ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]:
    router.add_api_route(
        "/{provider_id}/{path:path}",
        proxy_pass_through,
        methods=[_method],
        operation_id=f"proxy_pass_through_{_method.lower()}",
    )
