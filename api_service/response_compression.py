"""Response compression for dashboard assets and JSON."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware

# GZip holds bytes in the compressor until it has enough to emit, which would
# delay live output. SSE is already excluded by Starlette; live and merged log
# streams are ``text/plain``. WebSockets bypass HTTP middleware entirely.
STREAMING_CONTENT_TYPES = ("text/plain",)


def add_response_compression(app: FastAPI) -> None:
    """Gzip static assets and API responses for clients that accept it."""

    app.add_middleware(
        GZipMiddleware,
        exclude_content_types=(
            *DEFAULT_EXCLUDED_CONTENT_TYPES,
            *STREAMING_CONTENT_TYPES,
        ),
    )
