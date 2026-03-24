# main.py
# Configure logging at the very beginning
import logging

from moonmind.config.logging import configure_logging

configure_logging()
logger = logging.getLogger(__name__)  # Get logger after configuration

import os  # For path operations
import time
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
from api_service.api.routers.auth_profiles import router as auth_profiles_router
from api_service.api.routers.chat import router as chat_router
from api_service.api.routers.context_protocol import router as context_protocol_router
from api_service.api.routers.documents import router as documents_router
from api_service.api.routers.execution_integrations import (
    router as execution_integrations_router,
)
from api_service.api.routers.executions import router as executions_router
from api_service.api.routers.manifests import router as manifests_router
from api_service.api.routers.mcp_tools import router as mcp_tools_router
from api_service.api.routers.models import router as models_router
from api_service.api.routers.oauth_sessions import router as oauth_sessions_router
from api_service.api.routers.planning import router as planning_router
from api_service.api.routers.profile import router as profile_router
from api_service.api.routers.recurring_tasks import router as recurring_tasks_router
from api_service.api.routers.automation import router as automation_router
_ENABLE_TEST_UI_ROUTE = os.environ.get("MOONMIND_ENABLE_TEST_UI_ROUTE", "").lower() in ("1", "true", "yes")
if _ENABLE_TEST_UI_ROUTE:
    from api_service.test_ui_route import router as test_ui_router

from api_service.api.routers.task_compatibility import (
    router as task_compatibility_router,
)
from api_service.api.routers.task_dashboard import router as task_dashboard_router
from api_service.api.routers.task_proposals import router as task_proposals_router
from api_service.api.routers.task_step_templates import (
    router as task_step_templates_router,
)
from api_service.api.routers.temporal_artifacts import (
    router as temporal_artifacts_router,
)
from api_service.api.routers.workflows import router as workflows_router
from api_service.api.schemas import UserProfileUpdate
from api_service.db.base import get_async_session_context
from api_service.services.task_templates.catalog import TaskTemplateCatalogService

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

logger.info("Starting FastAPI...")

_TASK_TEMPLATE_SEED_DIR = (
    Path(__file__).resolve().parent / "data" / "task_step_templates"
)


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


app = FastAPI(
    title="MoonMind API",
    description="API for MoonMind - LLM-powered documentation search and chat interface",
    version="0.1.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# Setup templates
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Mount static files directory (optional, if you have CSS/JS files)
# Create the static directory if it doesn't exist, or ensure your deployment process does.
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)
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
app.include_router(manifests_router)
app.include_router(
    profile_router, prefix="", tags=["Profile"]
)  # Include profile router
app.include_router(workflows_router)
app.include_router(auth_profiles_router, prefix="/api/v1")
app.include_router(oauth_sessions_router, prefix="/api/v1")
app.include_router(executions_router)
app.include_router(execution_integrations_router)
app.include_router(automation_router)

app.include_router(task_proposals_router)
app.include_router(recurring_tasks_router)
app.include_router(task_dashboard_router)
app.include_router(task_compatibility_router)
app.include_router(task_step_templates_router)
app.include_router(temporal_artifacts_router)
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


async def _auto_seed_auth_profiles() -> list[str]:
    """Auto-seed default auth profiles when the table is empty.

    Returns the list of runtime_ids that were seeded, or an empty list if
    seeding was skipped or the table already has data.
    """
    from api_service.db.base import get_async_session_context
    from sqlalchemy import select, func
    from api_service.db.models import (
        ManagedAgentAuthProfile,
        ManagedAgentAuthMode,
        ManagedAgentRateLimitPolicy,
    )

    if os.environ.get("MOONMIND_SKIP_AUTH_PROFILE_SEED", "").lower() in ("1", "true", "yes"):
        logger.info("Auth profile auto-seeding disabled via MOONMIND_SKIP_AUTH_PROFILE_SEED.")
        return []

    # Well-known runtime defaults matching docker-compose.yaml conventions.
    _DEFAULT_PROFILES = [
        {
            "profile_id": "gemini_default",
            "runtime_id": "gemini_cli",
            "auth_mode": ManagedAgentAuthMode.OAUTH,
            "volume_ref": os.environ.get("GEMINI_VOLUME_NAME", "gemini_auth_volume"),
            "volume_mount_path": os.environ.get("GEMINI_VOLUME_PATH", "/var/lib/gemini-auth"),
            "account_label": "Gemini CLI (auto-seeded)",
        },
        {
            "profile_id": "codex_default",
            "runtime_id": "codex_cli",
            "auth_mode": ManagedAgentAuthMode.OAUTH,
            "volume_ref": os.environ.get("CODEX_VOLUME_NAME", "codex_auth_volume"),
            "volume_mount_path": os.environ.get("CODEX_VOLUME_PATH", "/home/app/.codex"),
            "account_label": "Codex CLI (auto-seeded)",
        },
        {
            "profile_id": "claude_default",
            "runtime_id": "claude_code",
            "auth_mode": ManagedAgentAuthMode.API_KEY,
            "volume_ref": os.environ.get("CLAUDE_VOLUME_NAME", "claude_auth_volume"),
            "volume_mount_path": os.environ.get("CLAUDE_VOLUME_PATH", "/home/app/.claude"),
            "account_label": "Claude Code (auto-seeded)",
        },
    ]

    # Conditionally add MiniMax profile when the API key is available.
    if os.environ.get("MINIMAX_API_KEY"):
        _DEFAULT_PROFILES.append({
            "profile_id": "claude_minimax",
            "runtime_id": "claude_code",
            "auth_mode": ManagedAgentAuthMode.API_KEY,
            "api_key_ref": "MINIMAX_API_KEY",
            "api_key_env_var": "ANTHROPIC_AUTH_TOKEN",
            "runtime_env_overrides": {
                "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
                "ANTHROPIC_MODEL": "MiniMax-M2.7",
                "ANTHROPIC_SMALL_FAST_MODEL": "MiniMax-M2.7",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M2.7",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "MiniMax-M2.7",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "MiniMax-M2.7",
                "API_TIMEOUT_MS": "600000",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
            "volume_ref": None,
            "volume_mount_path": None,
            "account_label": "Claude Code via MiniMax (auto-seeded)",
        })

    seeded: list[str] = []
    try:
        async with get_async_session_context() as session:
            count_result = await session.execute(
                select(func.count()).select_from(ManagedAgentAuthProfile)
            )
            profile_count = count_result.scalar() or 0

            if profile_count > 0:
                return []

            logger.info(
                "No auth profiles found in DB. Auto-seeding %d default profile(s)...",
                len(_DEFAULT_PROFILES),
            )

            for profile_def in _DEFAULT_PROFILES:
                profile = ManagedAgentAuthProfile(
                    profile_id=profile_def["profile_id"],
                    runtime_id=profile_def["runtime_id"],
                    auth_mode=profile_def["auth_mode"],
                    volume_ref=profile_def.get("volume_ref"),
                    volume_mount_path=profile_def.get("volume_mount_path"),
                    account_label=profile_def.get("account_label"),
                    api_key_ref=profile_def.get("api_key_ref"),
                    api_key_env_var=profile_def.get("api_key_env_var"),
                    runtime_env_overrides=profile_def.get("runtime_env_overrides"),
                    max_parallel_runs=1,
                    cooldown_after_429_seconds=300,
                    rate_limit_policy=ManagedAgentRateLimitPolicy.BACKOFF,
                    enabled=True,
                )
                session.add(profile)
                seeded.append(profile_def["runtime_id"])

            await session.commit()
            logger.info(
                "Committed %d auto-seeded managed-agent auth profile row(s).",
                len(seeded),
            )
    except Exception as e:
        logger.error("Failed to auto-seed auth profiles: %s", e, exc_info=True)

    return seeded


async def ensure_auth_profile_managers_started():
    """Ensure AuthProfileManager workflows are running for all distinct runtime families."""
    from api_service.db.base import get_async_session_context
    from sqlalchemy import select
    from api_service.db.models import ManagedAgentAuthProfile
    from moonmind.workflows.temporal.client import TemporalClientAdapter
    from moonmind.workflows.temporal.workflows.auth_profile_manager import WORKFLOW_NAME, AuthProfileManagerInput
    from temporalio.exceptions import WorkflowAlreadyStartedError

    # Auto-seed default profiles if table is empty.
    await _auto_seed_auth_profiles()

    logger.info("Ensuring AuthProfileManager workflows are started...")
    try:
        async with get_async_session_context() as session:
            stmt = select(ManagedAgentAuthProfile.runtime_id).distinct()
            result = await session.execute(stmt)
            runtime_ids = result.scalars().all()
            
        if not runtime_ids:
            logger.info("No managed agent auth profiles found. Skipping AuthProfileManager startup.")
            return

        temporal_adapter = TemporalClientAdapter()
        temporal_client = await temporal_adapter.get_client()
        
        for runtime_id in runtime_ids:
            workflow_id = f"auth-profile-manager:{runtime_id}"
            try:
                await temporal_client.start_workflow(
                    WORKFLOW_NAME,
                    AuthProfileManagerInput(runtime_id=runtime_id),
                    id=workflow_id,
                    task_queue="mm.workflow",
                )
                logger.info(f"Started AuthProfileManager for runtime: {runtime_id}")
            except WorkflowAlreadyStartedError:
                logger.debug(f"AuthProfileManager already running for runtime: {runtime_id}")
            except Exception as e:
                logger.error(f"Failed to start AuthProfileManager for {runtime_id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error ensuring AuthProfileManager workflows: {e}", exc_info=True)


@app.on_event("startup")
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
                    logger.error(
                        f"Error ensuring default user/profile on startup: {e}",
                        exc_info=True,
                    )
    else:
        logger.info(
            f"Auth provider is '{settings.oidc.AUTH_PROVIDER}'. Skipping default user creation on startup."
        )

    await ensure_auth_profile_managers_started()
    logger.info("Application startup events completed.")


@app.on_event("shutdown")
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
    except (OperationalError, ProgrammingError) as exc:
        logger.warning(
            "Task template seed sync skipped because preset tables are unavailable: %s",
            exc,
        )
        return
    except Exception as exc:
        logger.warning("Task template seed sync failed: %s", exc, exc_info=True)
        return

    if result.created or result.updated:
        logger.info(
            "Task template seeds synchronized from %s (created=%s updated=%s).",
            _TASK_TEMPLATE_SEED_DIR,
            result.created,
            result.updated,
        )
