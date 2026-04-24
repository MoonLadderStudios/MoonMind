from enum import Enum
from typing import Optional
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, Field, HttpUrl

class SummaryType(str, Enum):
    README = "readme"

class RepositorySummarizationRequest(BaseModel):
    repo_url: HttpUrl
    summary_type: SummaryType = Field(
        default=SummaryType.README, description="The type of summary to generate."
    )
    model: Optional[str] = Field(
        default=None, description="The language model to use for generation."
    )

class RepositorySummarizationResponse(BaseModel):
    summary_content: str
    summary_type: SummaryType

# Imports for helper functions and upcoming endpoint
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import User  # Assuming User model path
from api_service.services.profile_service import ProfileService

logger = logging.getLogger(__name__)
profile_service = ProfileService()

async def get_user_github_token(user: User, db: AsyncSession) -> Optional[str]:
    """
    Retrieves the user's GitHub token.
    """
    if not isinstance(user, User):
        logger.warning(
            "get_user_github_token called with an object that is not of type User. Auth likely disabled."
        )
        return None
    logger.info(f"Attempting to retrieve GitHub token for user {user.id}")

    profile = await profile_service.get_profile_by_user_id(db, user.id)
    if profile and profile.github_token_encrypted:
        return profile.github_token_encrypted

    logger.warning(f"No GitHub token configured for user {user.id}.")
    return None

async def get_user_llm_api_key(
    user: User, provider: str, db: AsyncSession
) -> Optional[str]:
    """
    Retrieves the API key for a given user and LLM provider.
    """
    if not hasattr(user, "id"):
        logger.warning(
            f"get_user_llm_api_key called with a non-user object for provider {provider}. Auth likely disabled."
        )
        # For "ollama", no key is needed, so we can return None. For others, this will cause the calling function to raise an error if a key is required.
        return None
    logger.info(
        f"Attempting to retrieve API key for user {user.id} and provider {provider}"
    )
    provider_lower = provider.lower()

    # Ollama typically doesn't require an API key, so return None or a specific marker if needed.
    if provider_lower == "ollama":
        logger.info(f"No API key needed for Ollama provider for user {user.id}.")
        return None  # Or some other indicator if your logic expects it, e.g. "ollama_no_key"

    profile = await profile_service.get_profile_by_user_id(db, user.id)

    if profile:
        field_name = f"{provider_lower}_api_key_encrypted"
        if hasattr(profile, field_name) and getattr(profile, field_name):
            return getattr(profile, field_name)

    logger.warning(
        f"No API key configured for provider: {provider} for user {user.id}."
    )
    return None

def sanitize_repo_url(repo_url: str) -> str:
    """Redact URL credentials before logging or returning errors."""
    parsed = urlparse(repo_url)
    if "@" not in parsed.netloc:
        return repo_url

    host = parsed.netloc.rsplit("@", 1)[1]
    sanitized_netloc = f"***REDACTED***@{host}"
    return urlunparse(parsed._replace(netloc=sanitized_netloc))

def redact_sensitive_git_error(
    error_message: str,
    repo_url: str,
    sanitized_repo_url: str,
    github_token: Optional[str],
) -> str:
    """Redact known secrets from git-related error messages."""
    sanitized_error = error_message
    if repo_url != sanitized_repo_url:
        sanitized_error = sanitized_error.replace(repo_url, sanitized_repo_url)
    if github_token:
        sanitized_error = sanitized_error.replace(github_token, "***REDACTED***")
    return sanitized_error

import os
import tempfile

# Set environment variable to suppress git warnings before importing
os.environ["GIT_PYTHON_REFRESH"] = "quiet"

try:
    import git  # GitPython
except ImportError as e:
    raise ImportError(
        "GitPython is required but not properly installed. Please install git and GitPython."
    ) from e

# FastAPI and other necessary imports for the router
from fastapi import APIRouter, Depends, HTTPException

from api_service.auth_providers import get_current_user  # Updated import
from api_service.db.base import get_async_session

# MoonMind specific imports
from moonmind.config.settings import settings
from moonmind.models_cache import model_cache

try:
    from moonmind.summarization.readme_generator import ReadmeAiGenerator
except Exception as exc:  # pragma: no cover - optional dependency
    ReadmeAiGenerator = None
    logging.getLogger(__name__).warning("ReadmeAiGenerator unavailable: %s", exc)

router = APIRouter()

@router.post("/repository", response_model=RepositorySummarizationResponse)
async def summarize_repository(
    request: RepositorySummarizationRequest,
    user: User = Depends(get_current_user()),
    db: AsyncSession = Depends(get_async_session),
):
    """
    Summarizes a code repository.
    Currently supports generating a README.md file.
    """
    user_id = user.id if hasattr(user, "id") else "unauthenticated_user"
    repo_url_str = str(request.repo_url)  # Convert HttpUrl to string for git operations
    sanitized_repo_url = sanitize_repo_url(repo_url_str)
    logger.info(
        f"Received repository summarization request for URL: {sanitized_repo_url}, type: {request.summary_type}, model: {request.model} by user {user_id}"
    )

    # 1. Model and Provider Resolution
    model_to_use = request.model or settings.get_default_chat_model()
    logger.info(f"Model to use (after considering default): {model_to_use}")

    provider = model_cache.get_model_provider(model_to_use)
    if not provider:
        logger.warning(
            f"Provider not found for model {model_to_use} in cache. Refreshing cache."
        )
        model_cache.refresh_models_sync()  # Consider if async refresh is available/needed
        provider = model_cache.get_model_provider(model_to_use)
        if not provider:
            logger.error(
                f"Provider still not found for model {model_to_use} after cache refresh."
            )
            raise HTTPException(
                status_code=404,
                detail=f"Model '{model_to_use}' not found or provider unknown.",
            )
    logger.info(f"Resolved provider: {provider} for model {model_to_use}")

    user_llm_api_key = await get_user_llm_api_key(user, provider, db)
    if (
        not user_llm_api_key and provider.lower() != "ollama"
    ):  # Ollama might not need a key
        logger.error(f"API key for provider {provider} not found for user {user_id}")
        raise HTTPException(
            status_code=400,
            detail=f"API key for {provider} not found in your profile. Please add it to use this model.",
        )

    # 2. Temporary Directory and Cloning
    github_token = await get_user_github_token(user, db)
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger.info(f"Created temporary directory: {temp_dir}")
            cloned_successfully = False
            try:
                logger.info(f"Attempting to clone {sanitized_repo_url} into {temp_dir}")
                git.Repo.clone_from(repo_url_str, temp_dir)
                logger.info(f"Successfully cloned {sanitized_repo_url} anonymously.")
                cloned_successfully = True
            except git.exc.GitCommandError as e_clone:
                error_msg = redact_sensitive_git_error(
                    str(e_clone), repo_url_str, sanitized_repo_url, github_token
                )
                logger.warning(
                    f"Anonymous clone failed for {sanitized_repo_url}: {error_msg}. Attempting with token."
                )
                if github_token:
                    # Construct authenticated URL: https://oauth2:{token}@github.com/owner/repo.git
                    # This format is common for GitHub. Other providers might vary.
                    parsed_url = urlparse(repo_url_str)
                    if parsed_url.hostname == "github.com":
                        repo_part = parsed_url.path.lstrip("/")
                        authenticated_url = (
                            f"https://oauth2:{github_token}@github.com/{repo_part}"
                        )
                        logger.info(
                            f"Attempting to clone with authenticated URL: {authenticated_url.replace(github_token, '***TOKEN***')}"
                        )
                        try:
                            git.Repo.clone_from(authenticated_url, temp_dir)
                            logger.info(
                                f"Successfully cloned {repo_url_str} using user's GitHub token."
                            )
                            cloned_successfully = True
                        except git.exc.GitCommandError as e_auth_clone:
                            error_msg = redact_sensitive_git_error(
                                str(e_auth_clone),
                                repo_url_str,
                                sanitized_repo_url,
                                github_token,
                            )
                            logger.error(
                                f"Authenticated clone failed for {sanitized_repo_url}: {error_msg}"
                            )
                            raise HTTPException(
                                status_code=400,
                                detail=f"Failed to clone repository (even with authentication): {error_msg}",
                            )
                    else:
                        error_msg = redact_sensitive_git_error(
                            str(e_clone), repo_url_str, sanitized_repo_url, github_token
                        )
                        logger.warning(
                            "Cannot construct authenticated URL for non-GitHub repo automatically."
                        )
                        raise HTTPException(
                            status_code=400,
                            detail=f"Failed to clone repository. Non-GitHub URL, cannot use token auth automatically: {error_msg}",
                        )
                else:
                    error_msg = redact_sensitive_git_error(
                        str(e_clone), repo_url_str, sanitized_repo_url, github_token
                    )
                    logger.error(
                        f"Anonymous clone failed and no GitHub token found for user {user_id}."
                    )
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to clone repository. Anonymous access failed and no GitHub token available: {error_msg}",
                    )

            if (
                not cloned_successfully
            ):  # Should be caught by exceptions above, but as a safeguard
                raise HTTPException(
                    status_code=500,
                    detail="Repository cloning process failed unexpectedly.",
                )

            # 3. Summarization
            summary_content = None
            if request.summary_type == SummaryType.README:
                logger.info(
                    f"Preparing to generate README for repository in {temp_dir}"
                )
                readme_config = {
                    "provider": provider.lower(),  # readme-ai expects lowercase
                    "model": model_to_use,
                }
                if (
                    user_llm_api_key
                ):  # Only add api_key if it exists (e.g. not for Ollama)
                    readme_config["api_key"] = user_llm_api_key

                # Potentially add other readme-ai specific settings from a global config or request if needed
                # readme_config["badge_style"] = "flat-square"

                if ReadmeAiGenerator is None:
                    logger.error("ReadmeAiGenerator not available")
                    raise HTTPException(
                        status_code=500,
                        detail="readme-ai library is not installed",
                    )

                generator = ReadmeAiGenerator(config=readme_config)
                debug_readme_config = dict(readme_config)
                if "api_key" in debug_readme_config:
                    debug_readme_config["api_key"] = "***REDACTED***"

                logger.debug(
                    "ReadmeAiGenerator instantiated with config: %s",
                    debug_readme_config,
                )

                summary_content = await generator.generate(repo_path=temp_dir)
                if summary_content is None:
                    logger.error(
                        f"README.md generation failed for {sanitized_repo_url} in {temp_dir}"
                    )
                    raise HTTPException(
                        status_code=500, detail="Failed to generate README.md summary."
                    )
                logger.info(
                    f"Successfully generated README.md for {sanitized_repo_url}"
                )
            else:
                logger.error(
                    f"Unsupported summary_type requested: {request.summary_type}"
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported summary_type: {request.summary_type}",
                )

            # 4. Response
            return RepositorySummarizationResponse(
                summary_content=summary_content, summary_type=request.summary_type
            )
    except HTTPException:  # Re-raise HTTPExceptions directly
        raise
    except git.exc.GitCommandError as e:  # Catch specific git errors not handled above
        error_msg = redact_sensitive_git_error(
            str(e), repo_url_str, sanitized_repo_url, github_token
        )
        logger.error(
            f"A GitCommandError occurred during repository operation for {sanitized_repo_url}: {error_msg}"
        )
        raise HTTPException(
            status_code=500, detail=f"A Git error occurred: {error_msg}"
        )
    except Exception as e:
        error_msg = redact_sensitive_git_error(
            str(e), repo_url_str, sanitized_repo_url, github_token
        )
        logger.error(
            f"An unexpected error occurred while summarizing repository {sanitized_repo_url}: {error_msg}"
        )
        raise HTTPException(
            status_code=500, detail=f"An unexpected error occurred: {error_msg}"
        )
    # Temporary directory 'temp_dir' is automatically cleaned up here due to 'with' statement
