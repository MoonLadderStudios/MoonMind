from enum import Enum
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, HttpUrl


class SummaryType(str, Enum):
    README = "readme"

class RepositorySummarizationRequest(BaseModel):
    repo_url: HttpUrl
    summary_type: SummaryType = Field(default=SummaryType.README, description="The type of summary to generate.")
    model: Optional[str] = Field(default=None, description="The language model to use for generation.")

class RepositorySummarizationResponse(BaseModel):
    summary_content: str
    summary_type: SummaryType

# Imports for helper functions and upcoming endpoint
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import User  # Assuming User model path

logger = logging.getLogger(__name__)

async def get_user_github_token(user: User, db: AsyncSession) -> Optional[str]:
    """
    Retrieves the user's GitHub token.
    Placeholder logic: This should eventually fetch from user.profile and decrypt.
    """
    logger.info(f"Attempting to retrieve GitHub token for user {user.id}")
    # Simulate checking user profile; replace with actual db/profile access
    # Example: if hasattr(user, 'profile') and user.profile.github_access_token_encrypted:
    #     # Decrypt logic here
    #     return "decrypted_github_token_placeholder"
    if user.email == "user_with_gh_token@example.com": # Example condition
        logger.warning("Using placeholder logic for GitHub token retrieval.")
        return "ghp_placeholder_github_token"
    logger.warning(f"No GitHub token configured for user {user.id} in placeholder logic.")
    return None

async def get_user_llm_api_key(user: User, provider: str, db: AsyncSession) -> Optional[str]:
    """
    Retrieves the API key for a given user and LLM provider.
    Placeholder logic: This should eventually fetch from user.profile and decrypt.
    """
    logger.info(f"Attempting to retrieve API key for user {user.id} and provider {provider}")
    provider_lower = provider.lower()
    # Simulate checking user profile; replace with actual db/profile access
    # Example: if hasattr(user, 'profile'):
    #     if provider_lower == "openai" and user.profile.openai_api_key_encrypted:
    #         return "decrypted_openai_key_placeholder"
    #     elif provider_lower == "google" and user.profile.google_api_key_encrypted:
    #         return "decrypted_google_key_placeholder"
    #     # etc. for other providers

    # Placeholder keys based on provider for testing
    if provider_lower == "openai":
        logger.warning("Using placeholder logic for OpenAI API key retrieval.")
        return "sk-placeholder-openai-key-summarization"
    elif provider_lower == "google":
        logger.warning("Using placeholder logic for Google API key retrieval.")
        return "google-placeholder-api-key-summarization"
    elif provider_lower == "anthropic":
        logger.warning("Using placeholder logic for Anthropic API key retrieval.")
        return "anthropic-placeholder-api-key-summarization"
    # Ollama typically doesn't require an API key, so return None or a specific marker if needed.
    elif provider_lower == "ollama":
        logger.info(f"No API key needed for Ollama provider for user {user.id}.")
        return None # Or some other indicator if your logic expects it, e.g. "ollama_no_key"

    logger.warning(f"No API key logic defined for provider: {provider} in placeholder function for user {user.id}.")
    return None

import os
import tempfile

# Set environment variable to suppress git warnings before importing
os.environ['GIT_PYTHON_REFRESH'] = 'quiet'

try:
    import git  # GitPython
except ImportError as e:
    raise ImportError("GitPython is required but not properly installed. Please install git and GitPython.") from e

# FastAPI and other necessary imports for the router
from fastapi import APIRouter, Depends, HTTPException

from api_service.auth_providers import get_current_user  # Updated import
from api_service.db.base import get_async_session
# MoonMind specific imports
from moonmind.config.settings import settings
from moonmind.models_cache import model_cache
from moonmind.summarization.readme_generator import ReadmeAiGenerator

router = APIRouter()

@router.post("/repository", response_model=RepositorySummarizationResponse)
async def summarize_repository(
    request: RepositorySummarizationRequest,
    user: User = Depends(get_current_user), # Updated dependency
    db: AsyncSession = Depends(get_async_session),
):
    """
    Summarizes a code repository.
    Currently supports generating a README.md file.
    """
    logger.info(f"Received repository summarization request for URL: {request.repo_url}, type: {request.summary_type}, model: {request.model} by user {user.id}")

    # 1. Model and Provider Resolution
    model_to_use = request.model or settings.get_default_chat_model()
    logger.info(f"Model to use (after considering default): {model_to_use}")

    provider = model_cache.get_model_provider(model_to_use)
    if not provider:
        logger.warning(f"Provider not found for model {model_to_use} in cache. Refreshing cache.")
        model_cache.refresh_models_sync() # Consider if async refresh is available/needed
        provider = model_cache.get_model_provider(model_to_use)
        if not provider:
            logger.error(f"Provider still not found for model {model_to_use} after cache refresh.")
            raise HTTPException(status_code=404, detail=f"Model '{model_to_use}' not found or provider unknown.")
    logger.info(f"Resolved provider: {provider} for model {model_to_use}")

    user_llm_api_key = await get_user_llm_api_key(user, provider, db)
    if not user_llm_api_key and provider.lower() != "ollama": # Ollama might not need a key
        logger.error(f"API key for provider {provider} not found for user {user.id}")
        raise HTTPException(
            status_code=400,
            detail=f"API key for {provider} not found in your profile. Please add it to use this model.",
        )

    # 2. Temporary Directory and Cloning
    repo_url_str = str(request.repo_url)  # Convert HttpUrl to string for git operations
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger.info(f"Created temporary directory: {temp_dir}")
            cloned_successfully = False
            try:
                logger.info(f"Attempting to clone {repo_url_str} into {temp_dir}")
                git.Repo.clone_from(repo_url_str, temp_dir)
                logger.info(f"Successfully cloned {repo_url_str} anonymously.")
                cloned_successfully = True
            except git.exc.GitCommandError as e_clone:
                logger.warning(f"Anonymous clone failed for {repo_url_str}: {e_clone}. Attempting with token.")
                github_token = await get_user_github_token(user, db)
                if github_token:
                    # Construct authenticated URL: https://oauth2:{token}@github.com/owner/repo.git
                    # This format is common for GitHub. Other providers might vary.
                    parsed_url = urlparse(repo_url_str)
                    if parsed_url.hostname == "github.com":
                        repo_part = parsed_url.path.lstrip("/")
                        authenticated_url = f"https://oauth2:{github_token}@github.com/{repo_part}"
                        logger.info(f"Attempting to clone with authenticated URL: {authenticated_url.replace(github_token, '***TOKEN***')}")
                        try:
                            git.Repo.clone_from(authenticated_url, temp_dir)
                            logger.info(f"Successfully cloned {repo_url_str} using user's GitHub token.")
                            cloned_successfully = True
                        except git.exc.GitCommandError as e_auth_clone:
                            logger.error(f"Authenticated clone failed for {repo_url_str}: {e_auth_clone}")
                            raise HTTPException(status_code=400, detail=f"Failed to clone repository (even with authentication): {e_auth_clone}")
                    else:
                        logger.warning("Cannot construct authenticated URL for non-GitHub repo automatically.")
                        raise HTTPException(status_code=400, detail=f"Failed to clone repository. Non-GitHub URL, cannot use token auth automatically: {e_clone}")
                else:
                    logger.error(f"Anonymous clone failed and no GitHub token found for user {user.id}.")
                    raise HTTPException(status_code=400, detail=f"Failed to clone repository. Anonymous access failed and no GitHub token available: {e_clone}")

            if not cloned_successfully: # Should be caught by exceptions above, but as a safeguard
                raise HTTPException(status_code=500, detail="Repository cloning process failed unexpectedly.")

            # 3. Summarization
            summary_content = None
            if request.summary_type == SummaryType.README:
                logger.info(f"Preparing to generate README for repository in {temp_dir}")
                readme_config = {
                    "provider": provider.lower(), # readme-ai expects lowercase
                    "model": model_to_use,
                }
                if user_llm_api_key: # Only add api_key if it exists (e.g. not for Ollama)
                    readme_config["api_key"] = user_llm_api_key

                # Potentially add other readme-ai specific settings from a global config or request if needed
                # readme_config["badge_style"] = "flat-square"

                generator = ReadmeAiGenerator(config=readme_config)
                logger.debug(f"ReadmeAiGenerator instantiated with config: {readme_config}")

                summary_content = await generator.generate(repo_path=temp_dir)
                if summary_content is None:
                    logger.error(f"README.md generation failed for {repo_url_str} in {temp_dir}")
                    raise HTTPException(status_code=500, detail="Failed to generate README.md summary.")
                logger.info(f"Successfully generated README.md for {repo_url_str}")
            else:
                logger.error(f"Unsupported summary_type requested: {request.summary_type}")
                raise HTTPException(status_code=400, detail=f"Unsupported summary_type: {request.summary_type}")

            # 4. Response
            return RepositorySummarizationResponse(
                summary_content=summary_content,
                summary_type=request.summary_type
            )
    except HTTPException: # Re-raise HTTPExceptions directly
        raise
    except git.exc.GitCommandError as e: # Catch specific git errors not handled above
        logger.exception(f"A GitCommandError occurred during repository operation for {repo_url_str}: {e}")
        raise HTTPException(status_code=500, detail=f"A Git error occurred: {e}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred while summarizing repository {repo_url_str}: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
    # Temporary directory 'temp_dir' is automatically cleaned up here due to 'with' statement
