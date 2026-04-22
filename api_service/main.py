# main.py
# Configure logging at the very beginning
import logging

from moonmind.config.logging import configure_logging

configure_logging()
logger = logging.getLogger(__name__)  # Get logger after configuration

import os  # For path operations
import time
from contextlib import asynccontextmanager
from pathlib import Path

# Now proceed with other imports
from uuid import uuid4

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from llama_index.core import VectorStoreIndex, load_index_from_storage
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

from api_service.api.routers import retrieval_gateway as retrieval_router
from api_service.api.routers import (
    summarization as summarization_router,  # Added import for summarization router
)
from api_service.api.routers.provider_profiles import router as provider_profiles_router
from api_service.api.routers.chat import router as chat_router
from api_service.api.routers.context_protocol import router as context_protocol_router
from api_service.api.routers.documents import router as documents_router
from api_service.api.routers.execution_integrations import (
    router as execution_integrations_router,
)
from api_service.api.routers.executions import router as executions_router
from api_service.api.routers.manifests import router as manifests_router
from api_service.api.routers.mcp_tools import router as mcp_tools_router
from api_service.api.routers.jira_browser import router as jira_browser_router
from api_service.api.routers.models import router as models_router
from api_service.api.routers.oauth_sessions import router as oauth_sessions_router
from api_service.api.routers.planning import router as planning_router
from api_service.api.routers.profile import router as profile_router
from api_service.api.routers.recurring_tasks import router as recurring_tasks_router
from api_service.api.routers.automation import router as automation_router
_ENABLE_TEST_UI_ROUTE = os.environ.get("MOONMIND_ENABLE_TEST_UI_ROUTE", "").lower() in ("1", "true", "yes")
if _ENABLE_TEST_UI_ROUTE:
    from api_service.test_ui_route import router as test_ui_router

from api_service.api.routers.task_dashboard import router as task_dashboard_router
from api_service.api.routers.task_runs import router as task_runs_router
from api_service.api.routers.task_proposals import router as task_proposals_router
from api_service.api.routers.task_step_templates import (
    router as task_step_templates_router,
)
from api_service.api.routers.temporal_artifacts import (
    router as temporal_artifacts_router,
)
from api_service.api.routers.workflows import router as workflows_router
from api_service.api.routers.secrets import router as secrets_router
from api_service.api.routers.proxy import router as proxy_router
from api_service.api.websockets import router as websockets_router
from api_service.api.schemas import UserProfileUpdate
from api_service.db.base import get_async_session_context
from api_service.services.task_templates.catalog import TaskTemplateCatalogService
from api_service.ui_assets import resolve_mission_control_dist_root

# Auth imports
from api_service.auth import (
    UserCreate,
    UserRead,
    UserUpdate,
    auth_backend,
    fastapi_users,
)
from moonmind.config.settings import settings
from moonmind.factories.embed_model_factory import build_embed_model

# Removed unused import: build_indexers
from moonmind.factories.service_context_factory import build_service_context
from moonmind.factories.storage_context_factory import build_storage_context
from moonmind.factories.vector_store_factory import build_vector_store
from moonmind.rag.service import ContextRetrievalService
from moonmind.rag.settings import RagRuntimeSettings
from moonmind.utils.logging import SecretRedactor

logger.info("Starting FastAPI...")

_TASK_TEMPLATE_SEED_DIR = (
    Path(__file__).resolve().parent / "data" / "task_step_templates"
)
_LEGACY_TASK_TEMPLATE_SLUGS_TO_DEACTIVATE = ("speckit-orchestrate",)


def _initialize_embedding_model(app_state, app_settings):
    """Initializes the embedding model and records its dimensionality on app_state."""
    logger.info("Initializing embedding model...")

    # Build the model and get any dimension configured in settings
    try:
        app_state.embed_model, configured_dims = build_embed_model(app_settings)
    except Exception as e:
        logger.error("Embedding model initialization failed: %s", e)
        app_state.embed_model = None
        app_state.embed_dimensions = None
        return

    # -------------------------------------------------------
    # Detect the true dimensionality produced by the model
    # -------------------------------------------------------
    detected_dims = None
    try:
        if hasattr(app_state.embed_model, "embed_query"):
            test_vec = app_state.embed_model.embed_query("dim_check")
        elif hasattr(app_state.embed_model, "get_query_embedding"):
            test_vec = app_state.embed_model.get_query_embedding("dim_check")
        else:
            raise AttributeError(
                "Embedding model lacks 'embed_query' and 'get_query_embedding'."
            )
        detected_dims = len(test_vec)
    except Exception as e:  # pragma: no cover – best-effort probe
        logger.error("Failed to detect embedding dimensions: %s", e)

    # -------------------------------------------------------
    # Decide which dimension to keep
    # -------------------------------------------------------
    if configured_dims and configured_dims > 0:
        # Settings override, but warn if they disagree with what we probed
        if detected_dims and detected_dims != configured_dims:
            logger.warning(
                "Configured embedding dimension %s ≠ detected %s. "
                "Using detected dimension.",
                configured_dims,
                detected_dims,
            )
            final_dims = detected_dims
        else:
            final_dims = configured_dims
    else:
        # No valid configured dimension → rely on detection
        final_dims = detected_dims

    if not final_dims or final_dims <= 0:
        raise RuntimeError(
            "Embedding dimension could not be determined. "
            "Please provide a valid value in configuration."
        )

    app_state.embed_dimensions = final_dims
    logger.info("Embedding model initialized with dimensions: %s", final_dims)


def _initialize_vector_store(app_state, app_settings):
    """Initializes and sets the vector store on app_state."""
    logger.info("Initializing vector store...")
    try:
        app_state.vector_store = build_vector_store(
            app_settings, app_state.embed_model, app_state.embed_dimensions
        )
        logger.info("Vector store initialized successfully.")
    except ValueError as e:
        logger.error(f"Failed to build vector store: {e}. This is a critical error.")
        raise


def _initialize_contexts(app_state, app_settings):
    """Initializes and sets storage and service contexts on app_state."""
    logger.info("Initializing storage and service contexts...")
    app_state.storage_context = build_storage_context(
        app_settings, app_state.vector_store
    )
    app_state.settings = build_service_context(
        app_settings, app_state.embed_model
    )  # settings is used as service_context
    logger.info("Storage and service contexts initialized successfully.")


async def _sync_env_managed_secrets() -> int:
    """Seed or refresh managed secrets from environment values."""

    def _read_value_from_dotenv(name: str) -> str | None:
        from moonmind.config.paths import ENV_FILE
        env_file = ENV_FILE

        try:
            if not env_file.exists():
                return None
            for raw_line in env_file.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line.removeprefix("export ").strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() != name:
                    continue
                value = value.strip()
                if (value.startswith("\"") and value.endswith("\"")) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    return value[1:-1]
                return value
        except Exception:
            logger.debug(
                "Failed to read candidate managed secret from .env file",
                slug=name,
                env_file=str(env_file),
                exc_info=True,
            )
            return None
        return None

    github_token_slugs = ("GITHUB_TOKEN", "GITHUB_PAT")

    def _env_value(name: str) -> str | None:
        value = os.environ.get(name)
        if value is None:
            value = _read_value_from_dotenv(name)
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    from api_service.services.secrets import SecretsService

    candidate_env_secrets: dict[str, str] = {}
    preferred_github_token: str | None = None
    for slug in github_token_slugs:
        value = _env_value(slug)
        if value:
            candidate_env_secrets[slug] = value
            if preferred_github_token is None:
                preferred_github_token = value

    if (
        preferred_github_token
        and candidate_env_secrets.get("GITHUB_TOKEN") != preferred_github_token
    ):
        # Keep the canonical managed-secret slug aligned with the token that
        # managed-runtime resolution would prefer so alias changes cannot leave a
        # stale GITHUB_TOKEN record active in the store.
        candidate_env_secrets["GITHUB_TOKEN"] = preferred_github_token

    atlassian_key = _env_value("ATLASSIAN_API_KEY")
    if atlassian_key:
        candidate_env_secrets["ATLASSIAN_API_KEY"] = atlassian_key

    if not candidate_env_secrets:
        logger.debug("No managed secret values found in environment; skipping sync.")
        return 0

    try:
        async with get_async_session_context() as session:
            imported = await SecretsService.import_from_env(
                session,
                candidate_env_secrets,
                overwrite_active=True,
            )
            if imported:
                logger.info(
                    "Synced managed secrets from environment on startup: imported_count=%s",
                    imported,
                )
            return imported
    except Exception as exc:
        # Keep startup resilient: this is convenience migration behavior, not a hard
        # startup dependency.
        redacted_error = SecretRedactor.from_environ(
            extra_secrets=list(candidate_env_secrets.values())
        ).scrub(str(exc))
        logger.warning(
            "Failed to sync managed secrets from environment during startup: %s",
            redacted_error,
        )
        return 0


async def _initialize_oidc_provider(app: FastAPI):
    """Initializes the OIDC provider by fetching discovery documents if needed."""
    provider = settings.oidc.AUTH_PROVIDER
    if provider == "google":
        logger.info("Initializing Google OIDC provider...")
        try:
            discovery_url = (
                f"{settings.oidc.OIDC_ISSUER_URL}/.well-known/openid-configuration"
            )
            async with httpx.AsyncClient() as client:
                response = await client.get(discovery_url, follow_redirects=True)
            response.raise_for_status()
            discovery_doc = response.json()
            jwks_uri = discovery_doc.get("jwks_uri")
            if not jwks_uri:
                logger.error("JWKS URI not found in Google OIDC discovery document.")
                raise RuntimeError(
                    "JWKS URI not found in Google OIDC discovery document."
                )
            app.state.jwks_uri = jwks_uri
            logger.info("Successfully fetched and stored Google JWKS URI.")
        except httpx.HTTPStatusError as e:
            logger.error(
                "Failed to fetch Google OIDC discovery document, status code %s: %s",
                e.response.status_code,
                e,
            )
            raise RuntimeError(
                f"Failed to fetch Google OIDC discovery document: {e}"
            ) from e
        except httpx.RequestError as e:
            logger.error("Failed to fetch Google OIDC discovery document: %s", e)
            raise RuntimeError(
                f"Failed to fetch Google OIDC discovery document: {e}"
            ) from e
        except Exception as e:
            logger.error(f"Error processing Google OIDC discovery document: {e}")
            raise RuntimeError(f"Error processing Google OIDC discovery document: {e}")


def _load_or_create_vector_index(app_state):
    """Loads an existing vector index or creates a new one if loading fails."""
    logger.info("Attempting to load VectorStoreIndex from storage_context...")
    try:
        app_state.vector_index = load_index_from_storage(
            storage_context=app_state.storage_context,
        )
        if not app_state.vector_index.docstore.docs:
            logger.warning(
                "Loaded index appears to be empty (no documents in docstore)."
            )
        else:
            logger.info("Successfully loaded VectorStoreIndex from storage.")
    except ValueError as e_load_idx:
        logger.warning(
            f"Could not load VectorStoreIndex from storage (it might be new or empty): {e_load_idx}. "
            "Creating a new empty VectorStoreIndex."
        )
        if app_state.storage_context and app_state.settings:
            app_state.vector_index = VectorStoreIndex.from_documents(
                [],
                storage_context=app_state.storage_context,
                service_context=app_state.settings,
            )
            logger.info("Created a new empty VectorStoreIndex.")
        else:
            logger.error(
                "Cannot create new VectorStoreIndex because storage_context or service_context is not available."
            )
            raise RuntimeError(
                "Failed to initialize critical components (storage_context or service_context) for VectorStoreIndex."
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    await startup_event()
    yield
    # Shutdown logic
    teardown_providers()

app = FastAPI(
    title="MoonMind API",
    description="API for MoonMind - LLM-powered documentation search and chat interface",
    version="0.1.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Setup templates
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
MISSION_CONTROL_STATIC_DIST_DIR = resolve_mission_control_dist_root()

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Mount Mission Control's Vite output separately so compose bind mounts of
# /app/api_service do not hide the image-baked fallback bundle.
if MISSION_CONTROL_STATIC_DIST_DIR.is_dir():
    app.mount(
        "/static/task_dashboard/dist",
        StaticFiles(directory=str(MISSION_CONTROL_STATIC_DIST_DIR)),
        name="task-dashboard-dist",
    )

# Mount static files directory (optional, if you have CSS/JS files). Create the
# static directory if it doesn't exist, or ensure your deployment process does.
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# Healthz router
health_router = APIRouter()

_api_start_time = time.monotonic()


@health_router.get("/healthz")
async def health_check():
    """Health endpoint with database connectivity probe."""
    uptime = int(time.monotonic() - _api_start_time)
    try:
        async with get_async_session_context() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok", "db": "connected", "uptime_seconds": uptime}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": "unreachable", "uptime_seconds": uptime},
        )


@app.get("/", include_in_schema=False)
async def docs_redirect() -> RedirectResponse:
    """Redirect root path to Swagger UI."""
    return RedirectResponse(url=app.docs_url)


app.include_router(health_router, tags=["health"])

# Include all routers
app.include_router(chat_router, prefix="/v1/chat", tags=["Chat"])
app.include_router(models_router, prefix="/v1/models", tags=["Models"])
app.include_router(documents_router, prefix="/v1/documents", tags=["Documents"])
app.include_router(
    summarization_router.router,
    prefix="/summarization",
    tags=["Summarization"],
)  # Added summarization router
app.include_router(planning_router, prefix="/v1/planning", tags=["Planning"])
app.include_router(
    context_protocol_router, tags=["Context Protocol"]
)  # Removed prefix="/context"
app.include_router(retrieval_router.router)
app.include_router(mcp_tools_router)
app.include_router(jira_browser_router)
app.include_router(manifests_router)
app.include_router(
    profile_router, prefix="", tags=["Profile"]
)  # Include profile router
app.include_router(workflows_router)
app.include_router(provider_profiles_router, prefix="/api/v1")
app.include_router(oauth_sessions_router, prefix="/api/v1")
app.include_router(secrets_router, prefix="/api/v1/secrets")
app.include_router(proxy_router, prefix="/api/v1")
app.include_router(executions_router)
app.include_router(execution_integrations_router)
app.include_router(automation_router)

app.include_router(task_proposals_router)
app.include_router(recurring_tasks_router)
app.include_router(task_runs_router, prefix="/api")
app.include_router(task_dashboard_router)
app.include_router(task_step_templates_router)
app.include_router(temporal_artifacts_router)
app.include_router(websockets_router, prefix="/ws/v1", tags=["WebSockets"])
if _ENABLE_TEST_UI_ROUTE:
    app.include_router(test_ui_router)

# Auth routers
API_AUTH_PREFIX = "/api/v1/auth"  # Defined a constant for clarity

if settings.oidc.AUTH_PROVIDER != "keycloak":
    logger.info(
        f"AUTH_PROVIDER is '{settings.oidc.AUTH_PROVIDER}'. Including fastapi-users auth routers."
    )
    app.include_router(
        fastapi_users.get_auth_router(auth_backend),
        prefix=API_AUTH_PREFIX,
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix=API_AUTH_PREFIX,
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_reset_password_router(),
        prefix=API_AUTH_PREFIX,
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_verify_router(UserRead),
        prefix=API_AUTH_PREFIX,
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix=f"{API_AUTH_PREFIX}/users",
        tags=["users"],
    )
else:
    logger.info("AUTH_PROVIDER is 'keycloak'. Skipping fastapi-users auth routers.")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.middleware("http")
async def add_debug_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Debug-Stream"] = str(getattr(request.state, "stream", False))
    response.headers["X-Debug-ContentType"] = response.headers.get(
        "content-type", "none"
    )
    return response


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


_CODEX_OPENROUTER_QWEN36_PLUS_MODEL = "qwen/qwen3.6-plus"
_LEGACY_CODEX_OPENROUTER_QWEN36_PLUS_FREE_MODEL = "qwen/qwen3.6-plus:free"


def _codex_openrouter_qwen36_plus_file_templates(
    model: str,
) -> list[dict[str, object]]:
    return [
        {
            "path": "{{runtime_support_dir}}/codex-home/config.toml",
            "format": "toml",
            "merge_strategy": "replace",
            "content_template": {
                "model_provider": "openrouter",
                "model_reasoning_effort": "high",
                "model": model,
                "profile": "openrouter_qwen36_plus",
                "model_providers": {
                    "openrouter": {
                        "name": "OpenRouter",
                        "base_url": "https://openrouter.ai/api/v1",
                        "env_key": "OPENROUTER_API_KEY",
                        "wire_api": "responses",
                    },
                },
                "profiles": {
                    "openrouter_qwen36_plus": {
                        "model_provider": "openrouter",
                        "model": model,
                    }
                },
            },
            "permissions": "0600",
        }
    ]


def _legacy_codex_openrouter_qwen36_plus_file_templates() -> list[dict[str, object]]:
    return [
        {
            "path": "{{runtime_support_dir}}/codex-home/config.toml",
            "format": "toml",
            "merge_strategy": "replace",
            "content_template": {
                "model_provider": "openrouter",
                "profile": "openrouter_qwen36_plus",
                "model_providers": {
                    "openrouter": {
                        "name": "OpenRouter",
                        "base_url": "https://openrouter.ai/api/v1",
                        "env_key": "OPENROUTER_API_KEY",
                        "wire_api": "responses",
                    },
                },
                "profiles": {
                    "openrouter_qwen36_plus": {
                        "model_provider": "openrouter",
                        "model": _LEGACY_CODEX_OPENROUTER_QWEN36_PLUS_FREE_MODEL,
                    }
                },
            },
            "permissions": "0600",
        }
    ]


def _should_reconcile_openrouter_codex_file_templates(
    profile_id: str,
    current_file_templates,
    desired_file_templates,
) -> bool:
    if profile_id != "codex_openrouter_qwen36_plus":
        return False
    if desired_file_templates is None:
        return False
    if current_file_templates == desired_file_templates:
        return False
    deprecated_seed_templates = _codex_openrouter_qwen36_plus_file_templates(
        _LEGACY_CODEX_OPENROUTER_QWEN36_PLUS_FREE_MODEL
    )
    return current_file_templates in (
        deprecated_seed_templates,
        _legacy_codex_openrouter_qwen36_plus_file_templates(),
    )


async def _auto_seed_provider_profiles() -> list[str]:
    """Seed well-known provider profiles that are missing from the DB.

    Each profile is checked individually by ``profile_id`` so that:
    - On a fresh install all defaults are created.
    - When ``MINIMAX_API_KEY`` is added to the environment after the initial
      seed, the ``claude_minimax`` profile is inserted.
    - Existing profiles have their missing `default_model` values backfilled.

    Returns the list of ``runtime_id`` values for profiles that were actually
    inserted, or an empty list when nothing was seeded.
    """
    from api_service.db.base import get_async_session_context
    from sqlalchemy import select
    from api_service.db.models import (
        ManagedAgentProviderProfile,
        ProviderCredentialSource,
        RuntimeMaterializationMode,
        ManagedAgentRateLimitPolicy,
    )
    from api_service.services.provider_profile_service import (
        normalize_runtime_default_profile,
    )
    from moonmind.workflows.temporal.runtime.providers.registry import (
        get_provider_default,
    )

    if os.environ.get("MOONMIND_SKIP_PROVIDER_PROFILE_SEED", "").lower() in ("1", "true", "yes"):
        logger.info("Provider profile auto-seeding disabled via MOONMIND_SKIP_PROVIDER_PROFILE_SEED.")
        return []

    # Well-known runtime defaults matching docker-compose.yaml conventions.
    _DEFAULT_PROFILES = [
        {
            "profile_id": "gemini_default",
            "runtime_id": "gemini_cli",
            "is_default": True,
            "provider_id": "google",
            "provider_label": "Google",
            "default_model": None,  # inherits runtime default: gemini-3.1-pro-preview
            "credential_source": ProviderCredentialSource.OAUTH_VOLUME,
            "runtime_materialization_mode": RuntimeMaterializationMode.OAUTH_HOME,
            "volume_ref": get_provider_default("gemini_cli", "volume_ref"),
            "volume_mount_path": get_provider_default(
                "gemini_cli", "volume_mount_path"
            ),
            "account_label": "Gemini CLI (auto-seeded)",
        },
        {
            "profile_id": "codex_default",
            "runtime_id": "codex_cli",
            "is_default": True,
            "provider_id": "openai",
            "provider_label": "OpenAI",
            "default_model": None,  # inherits runtime default: gpt-5.4
            "credential_source": ProviderCredentialSource.OAUTH_VOLUME,
            "runtime_materialization_mode": RuntimeMaterializationMode.OAUTH_HOME,
            "volume_ref": get_provider_default("codex_cli", "volume_ref"),
            "volume_mount_path": get_provider_default("codex_cli", "volume_mount_path"),
            "account_label": "Codex CLI (auto-seeded)",
        },
        {
            "profile_id": "claude_anthropic",
            "runtime_id": "claude_code",
            "is_default": True,
            "provider_id": "anthropic",
            "provider_label": "Anthropic",
            "default_model": None,  # inherits runtime default: Sonnet 4.6
            "credential_source": ProviderCredentialSource.OAUTH_VOLUME,
            "runtime_materialization_mode": RuntimeMaterializationMode.OAUTH_HOME,
            "volume_ref": get_provider_default("claude_code", "volume_ref"),
            "volume_mount_path": get_provider_default(
                "claude_code", "volume_mount_path"
            ),
            "clear_env_keys": [
                "ANTHROPIC_API_KEY",
                "CLAUDE_API_KEY",
                "OPENAI_API_KEY",
            ],
            "account_label": "Claude Code (auto-seeded)",
        },

    ]

    # Conditionally include MiniMax profile when the API key is available.
    if os.environ.get("MINIMAX_API_KEY"):
        _DEFAULT_PROFILES.append({
            "profile_id": "claude_minimax",
            "runtime_id": "claude_code",
            "is_default": False,
            "provider_id": "minimax",
            "provider_label": "MiniMax",
            "default_model": "MiniMax-M2.7",
            "credential_source": ProviderCredentialSource.SECRET_REF,
            "runtime_materialization_mode": RuntimeMaterializationMode.ENV_BUNDLE,
            "secret_refs": {
                "ANTHROPIC_AUTH_TOKEN": "env://MINIMAX_API_KEY"
            },
            "clear_env_keys": [
                "ANTHROPIC_API_KEY",
                "OPENAI_API_KEY",
            ],
            "env_template": {
                "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
                "ANTHROPIC_MODEL": "MiniMax-M2.7",
                "ANTHROPIC_SMALL_FAST_MODEL": "MiniMax-M2.7",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M2.7",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "MiniMax-M2.7",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "MiniMax-M2.7",
                "API_TIMEOUT_MS": "3000000",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
            "volume_ref": None,
            "volume_mount_path": None,
            "account_label": "Claude Code via MiniMax (auto-seeded)",
        })

    if os.environ.get("OPENROUTER_API_KEY"):
        _DEFAULT_PROFILES.append({
            "profile_id": "codex_openrouter_qwen36_plus",
            "runtime_id": "codex_cli",
            "is_default": False,
            "provider_id": "openrouter",
            "provider_label": "OpenRouter",
            "default_model": _CODEX_OPENROUTER_QWEN36_PLUS_MODEL,
            "credential_source": ProviderCredentialSource.SECRET_REF,
            "runtime_materialization_mode": RuntimeMaterializationMode.COMPOSITE,
            "secret_refs": {
                "provider_api_key": "env://OPENROUTER_API_KEY",
            },
            "clear_env_keys": [
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "OPENAI_ORG_ID",
                "OPENAI_PROJECT",
                "OPENROUTER_API_KEY",
            ],
            "env_template": {
                "OPENROUTER_API_KEY": {
                    "from_secret_ref": "provider_api_key",
                },
            },
            "file_templates": _codex_openrouter_qwen36_plus_file_templates(
                _CODEX_OPENROUTER_QWEN36_PLUS_MODEL
            ),
            "home_path_overrides": {
                "CODEX_HOME": "{{runtime_support_dir}}/codex-home",
            },
            "command_behavior": {
                "suppress_default_model_flag": True,
            },
            "max_parallel_runs": 4,
            "cooldown_after_429_seconds": 300,
            "rate_limit_policy": ManagedAgentRateLimitPolicy.BACKOFF,
            "max_lease_duration_seconds": 7200,
            "volume_ref": None,
            "volume_mount_path": None,
            "account_label": "Codex CLI via OpenRouter (auto-seeded)",
        })

    seeded: list[str] = []
    try:
        async with get_async_session_context() as session:
            from sqlalchemy import update
            existing_result = await session.execute(
                select(
                    ManagedAgentProviderProfile.profile_id,
                    ManagedAgentProviderProfile.provider_id,
                    ManagedAgentProviderProfile.provider_label,
                    ManagedAgentProviderProfile.default_model,
                    ManagedAgentProviderProfile.clear_env_keys,
                    ManagedAgentProviderProfile.file_templates,
                )
            )
            existing_rows = existing_result.all()
            existing_by_id = {
                row.profile_id: {
                    "provider_id": row.provider_id,
                    "provider_label": row.provider_label,
                    "default_model": row.default_model,
                    "clear_env_keys": row.clear_env_keys,
                    "file_templates": row.file_templates,
                }
                for row in existing_rows
            }
            existing_ids: set[str] = set(existing_by_id)

            to_insert = [p for p in _DEFAULT_PROFILES if p["profile_id"] not in existing_ids]

            needs_commit = False
            for profile_def in _DEFAULT_PROFILES:
                profile_id = profile_def["profile_id"]
                desired_default_model = profile_def.get("default_model")
                if profile_id in existing_by_id:
                    if profile_id == "codex_default":
                        current_provider_id = str(
                            existing_by_id[profile_id]["provider_id"] or ""
                        ).strip()
                        if current_provider_id == "moonladder":
                            stmt = (
                                update(ManagedAgentProviderProfile)
                                .where(
                                    ManagedAgentProviderProfile.profile_id == profile_id
                                )
                                .values(
                                    provider_id=profile_def["provider_id"],
                                    provider_label=profile_def.get("provider_label"),
                                )
                            )
                            await session.execute(stmt)
                            needs_commit = True
                    current_model = existing_by_id[profile_id]["default_model"]
                    # Only reconcile when the seeded profile has an explicit desired model
                    # (non-None) and the existing row is blank or contains an old
                    # deprecated seed value; never clear user-set values.
                    current_model_text = str(current_model or "").strip()
                    legacy_openrouter_model = (
                        _LEGACY_CODEX_OPENROUTER_QWEN36_PLUS_FREE_MODEL
                    )
                    should_reconcile_deprecated_model = (
                        profile_id == "codex_openrouter_qwen36_plus"
                        and current_model_text == legacy_openrouter_model
                    )
                    if desired_default_model is not None and (
                        not current_model_text or should_reconcile_deprecated_model
                    ):
                        stmt = (
                            update(ManagedAgentProviderProfile)
                            .where(ManagedAgentProviderProfile.profile_id == profile_id)
                            .values(default_model=desired_default_model)
                        )
                        await session.execute(stmt)
                        needs_commit = True
                    desired_clear_env_keys = profile_def.get("clear_env_keys")
                    if profile_id == "claude_anthropic" and desired_clear_env_keys:
                        current_clear_env_keys = list(
                            existing_by_id[profile_id]["clear_env_keys"] or []
                        )
                        reconciled_clear_env_keys = list(current_clear_env_keys)
                        for env_key in desired_clear_env_keys:
                            if env_key not in reconciled_clear_env_keys:
                                reconciled_clear_env_keys.append(env_key)
                        if reconciled_clear_env_keys != current_clear_env_keys:
                            stmt = (
                                update(ManagedAgentProviderProfile)
                                .where(
                                    ManagedAgentProviderProfile.profile_id == profile_id
                                )
                                .values(clear_env_keys=reconciled_clear_env_keys)
                            )
                            await session.execute(stmt)
                            needs_commit = True
                    desired_file_templates = profile_def.get("file_templates")
                    current_file_templates = existing_by_id[profile_id]["file_templates"]
                    if _should_reconcile_openrouter_codex_file_templates(
                        profile_id=profile_id,
                        current_file_templates=current_file_templates,
                        desired_file_templates=desired_file_templates,
                    ):
                        stmt = (
                            update(ManagedAgentProviderProfile)
                            .where(ManagedAgentProviderProfile.profile_id == profile_id)
                            .values(file_templates=desired_file_templates)
                        )
                        await session.execute(stmt)
                        needs_commit = True

            if not to_insert:
                if needs_commit:
                    await session.commit()
                return []

            logger.info(
                "Auto-seeding %d missing provider profile(s)",
                len(to_insert),
            )

            touched_runtime_ids: set[str] = set()
            for profile_def in to_insert:
                profile = ManagedAgentProviderProfile(
                    profile_id=profile_def["profile_id"],
                    runtime_id=profile_def["runtime_id"],
                    provider_id=profile_def["provider_id"],
                    provider_label=profile_def.get("provider_label"),
                    default_model=profile_def.get("default_model"),
                    credential_source=profile_def["credential_source"],
                    runtime_materialization_mode=profile_def["runtime_materialization_mode"],
                    volume_ref=profile_def.get("volume_ref"),
                    volume_mount_path=profile_def.get("volume_mount_path"),
                    account_label=profile_def.get("account_label"),
                    secret_refs=profile_def.get("secret_refs"),
                    clear_env_keys=profile_def.get("clear_env_keys"),
                    env_template=profile_def.get("env_template"),
                    file_templates=profile_def.get("file_templates"),
                    home_path_overrides=profile_def.get("home_path_overrides"),
                    command_behavior=profile_def.get("command_behavior"),
                    max_parallel_runs=profile_def.get("max_parallel_runs", 1),
                    cooldown_after_429_seconds=profile_def.get(
                        "cooldown_after_429_seconds", 900
                    ),
                    rate_limit_policy=profile_def.get(
                        "rate_limit_policy",
                        ManagedAgentRateLimitPolicy.BACKOFF,
                    ),
                    enabled=True,
                    is_default=bool(profile_def.get("is_default", False)),
                    max_lease_duration_seconds=profile_def.get(
                        "max_lease_duration_seconds", 7200
                    ),
                )
                session.add(profile)
                runtime_id = profile_def["runtime_id"]
                touched_runtime_ids.add(runtime_id)
                seeded.append(runtime_id)

            await session.flush()
            for runtime_id in touched_runtime_ids:
                await normalize_runtime_default_profile(
                    session=session,
                    runtime_id=runtime_id,
                )

            await session.commit()
            logger.info(
                "Committed %d auto-seeded managed-agent provider profile row(s).",
                len(seeded),
            )
    except Exception as e:
        logger.error("Failed to auto-seed provider profiles: %s", e, exc_info=True)

    return seeded


async def ensure_provider_profile_managers_started():
    """Ensure ProviderProfileManager workflows are running for all distinct runtime families."""
    from api_service.db.base import get_async_session_context
    from sqlalchemy import select
    from api_service.db.models import ManagedAgentProviderProfile
    from moonmind.workflows.temporal.client import TemporalClientAdapter
    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        WORKFLOW_NAME,
        ProviderProfileManagerInput,
        workflow_id_for_runtime,
    )
    from temporalio.exceptions import WorkflowAlreadyStartedError

    # Auto-seed default profiles if table is empty.
    await _auto_seed_provider_profiles()

    logger.info("Ensuring ProviderProfileManager workflows are started...")
    try:
        async with get_async_session_context() as session:
            stmt = select(ManagedAgentProviderProfile.runtime_id).distinct()
            result = await session.execute(stmt)
            runtime_ids = result.scalars().all()
            
        if not runtime_ids:
            logger.info("No managed agent provider profiles found. Skipping ProviderProfileManager startup.")
            return

        temporal_adapter = TemporalClientAdapter()
        temporal_client = await temporal_adapter.get_client()
        
        for runtime_id in runtime_ids:
            workflow_id = workflow_id_for_runtime(runtime_id)
            try:
                await temporal_client.start_workflow(
                    WORKFLOW_NAME,
                    ProviderProfileManagerInput(runtime_id=runtime_id),
                    id=workflow_id,
                    task_queue="mm.workflow",
                )
                logger.info(f"Started ProviderProfileManager for runtime: {runtime_id}")
            except WorkflowAlreadyStartedError:
                logger.debug(f"ProviderProfileManager already running for runtime: {runtime_id}")
            except Exception as e:
                logger.error(f"Failed to start ProviderProfileManager for {runtime_id}: {e}", exc_info=True)

            try:
                from api_service.services.provider_profile_service import sync_provider_profile_manager
                async with get_async_session_context() as session:
                    await sync_provider_profile_manager(session=session, runtime_id=runtime_id)
                logger.debug(f"Synced ProviderProfileManager for runtime: {runtime_id}")
            except Exception as e:
                logger.error(f"Failed to sync ProviderProfileManager for {runtime_id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error ensuring ProviderProfileManager workflows: {e}", exc_info=True)


async def startup_event():
    """Defines the application's startup events."""
    logger.info("Executing application startup events...")
    # Initialize state object if it doesn't exist
    if not hasattr(app, "state"):
        # In modern FastAPI, `app.state` exists by default, but this is a safeguard.
        from starlette.datastructures import State

        app.state = State()

    _initialize_embedding_model(app.state, settings)
    _initialize_vector_store(app.state, settings)
    _initialize_contexts(app.state, settings)
    _load_or_create_vector_index(app.state)
    await _initialize_oidc_provider(app)  # OIDC provider init like Keycloak discovery
    try:
        app.state.retrieval_service = ContextRetrievalService(
            settings=RagRuntimeSettings.from_env()
        )
    except Exception as exc:
        logger.warning(
            "Retrieval service startup initialization skipped: %s",
            exc,
        )
    await _sync_task_template_seed_catalog()
    await _sync_env_managed_secrets()

    # Ensure default user and profile exist if auth is disabled
    if settings.oidc.AUTH_PROVIDER == "disabled":
        logger.info(
            "Auth provider is 'disabled'. Ensuring default user and profile exist on startup."
        )
        from api_service.auth import (
            _DEFAULT_USER_ID,
            get_or_create_default_user,
            get_user_manager_context,
        )
        from api_service.db.base import get_async_session_context
        from api_service.services.profile_service import ProfileService

        async with get_async_session_context() as db_session:
            async with get_user_manager_context(db_session) as user_manager:
                try:
                    if (
                        not settings.oidc.DEFAULT_USER_ID
                        or not settings.oidc.DEFAULT_USER_EMAIL
                    ):
                        logger.warning(
                            "DEFAULT_USER_ID or DEFAULT_USER_EMAIL not configured. Using built-in defaults."
                        )
                    logger.info(
                        f"Attempting to get/create default user ID: {settings.oidc.DEFAULT_USER_ID or _DEFAULT_USER_ID} on startup."
                    )
                    default_user = await get_or_create_default_user(
                        db_session=db_session, user_manager=user_manager
                    )
                    if default_user:
                        logger.info(
                            f"Default user {default_user.email} (ID: {default_user.id}) ensured."
                        )
                        profile_service = ProfileService()
                        existing_profile = await profile_service.get_profile_by_user_id(
                            db_session=db_session, user_id=default_user.id
                        )
                        if existing_profile:
                            logger.info(
                                f"Profile for default user {default_user.email} already exists (Profile ID: {existing_profile.id})."
                            )
                        else:
                            profile_update = UserProfileUpdate(
                                google_api_key=settings.google.google_api_key,
                                openai_api_key=settings.openai.openai_api_key,
                                anthropic_api_key=settings.anthropic.anthropic_api_key,
                            )
                            profile = await profile_service.update_profile(
                                db_session=db_session,
                                user_id=default_user.id,
                                profile_data=profile_update,
                            )
                            logger.info(
                                f"Created profile for default user {default_user.email} (Profile ID: {profile.id}) from env keys."
                            )
                        from moonmind.models_cache import refresh_model_cache_for_user

                        await refresh_model_cache_for_user(default_user, db_session)
                    else:
                        logger.error("Failed to get or create default user on startup.")
                except ValueError as ve:
                    logger.error(
                        f"Configuration error during default user setup on startup: {ve}"
                    )
                except Exception as e:
                    redacted_error = SecretRedactor.from_environ().scrub(str(e))
                    logger.error(
                        "Error ensuring default user/profile on startup: %s",
                        redacted_error,
                    )
    else:
        logger.info(
            f"Auth provider is '{settings.oidc.AUTH_PROVIDER}'. Skipping default user creation on startup."
        )

    # Wait for the Temporal client to be available and initialize provider profile managers
    await ensure_provider_profile_managers_started()
    logger.info("Application startup events completed.")


def teardown_providers():
    """
    Optional: If your providers need explicit cleanup, do it here.
    """
    pass


async def _sync_task_template_seed_catalog() -> None:
    """Ensure YAML-backed default task presets exist in the catalog."""

    if not settings.feature_flags.task_template_catalog_enabled:
        return
    if not _TASK_TEMPLATE_SEED_DIR.exists():
        logger.info(
            "Task template seed sync skipped: seed directory missing at %s",
            _TASK_TEMPLATE_SEED_DIR,
        )
        return

    try:
        async with get_async_session_context() as session:
            service = TaskTemplateCatalogService(session)
            result = await service.sync_seed_templates(seed_dir=_TASK_TEMPLATE_SEED_DIR)
            deactivated = await service.deactivate_templates(
                slugs=_LEGACY_TASK_TEMPLATE_SLUGS_TO_DEACTIVATE,
                scope="global",
                scope_ref=None,
            )
    except (OperationalError, ProgrammingError) as exc:
        logger.warning(
            "Task template seed sync skipped because preset tables are unavailable: %s",
            exc,
        )
        return
    except Exception as exc:
        logger.warning("Task template seed sync failed: %s", exc, exc_info=True)
        return

    if result.created or result.updated or deactivated:
        logger.info(
            "Task template seeds synchronized from %s (created=%s updated=%s deactivated=%s).",
            _TASK_TEMPLATE_SEED_DIR,
            result.created,
            result.updated,
            deactivated,
        )
