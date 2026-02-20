"""Embedding helpers for worker-side retrieval."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional

import google.generativeai as genai
from openai import OpenAI

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """Raised when embedding generation fails."""


@dataclass(slots=True)
class EmbeddingConfig:
    provider: str
    model: str
    google_api_key: Optional[str]
    openai_api_key: Optional[str]
    ollama_model: Optional[str]


class EmbeddingClient:
    """Simple synchronous embedding adapter."""

    def __init__(self, config: EmbeddingConfig) -> None:
        self._config = config
        self._provider = config.provider.lower()
        self._cached_dimension: Optional[int] = None
        if self._provider == "google":
            api_key = config.google_api_key
            if not api_key:
                raise EmbeddingError("GOOGLE_API_KEY is required for google embeddings")
            genai.configure(api_key=api_key)
        elif self._provider == "openai":
            api_key = config.openai_api_key
            if not api_key:
                raise EmbeddingError("OPENAI_API_KEY is required for openai embeddings")
            self._openai = OpenAI(api_key=api_key)
        elif self._provider == "ollama":
            try:
                import ollama  # type: ignore
            except ImportError as exc:  # pragma: no cover - optional dep missing
                raise EmbeddingError("ollama package missing") from exc
            self._ollama = ollama
        else:
            raise EmbeddingError(f"Unsupported embedding provider: {self._provider}")

    def embed(self, text: str) -> List[float]:
        text = (text or "").strip()
        if not text:
            raise EmbeddingError("Query text cannot be empty")
        if self._provider == "google":
            try:
                response = genai.embed_content(model=self._config.model, content=text)
            except Exception as exc:  # pragma: no cover - network failure
                raise EmbeddingError(f"Google embedding failed: {exc}") from exc
            result = response.get("embedding") if isinstance(response, dict) else None
            if isinstance(result, dict) and "values" in result:
                result = result["values"]
            if not isinstance(result, Iterable):
                raise EmbeddingError("Google embedding response missing 'embedding'")
            return [float(value) for value in result]
        if self._provider == "openai":
            try:
                result = self._openai.embeddings.create(
                    model=self._config.model, input=text
                )
            except Exception as exc:  # pragma: no cover
                raise EmbeddingError(f"OpenAI embedding failed: {exc}") from exc
            data = result.data[0].embedding
            return list(data)
        if self._provider == "ollama":
            response = self._ollama.embeddings(
                model=self._config.ollama_model or self._config.model, prompt=text
            )
            return list(response["embedding"])
        raise EmbeddingError(f"Unsupported provider {self._provider}")

    def embed_many(self, texts: Iterable[str]) -> List[List[float]]:
        return [self.embed(text) for text in texts]

    def embedding_dimension(self) -> int:
        """Return the embedding dimension for the configured provider."""

        if self._cached_dimension is not None:
            return self._cached_dimension
        vector = self.embed("MoonMind embedding dimension probe.")
        if not vector:
            raise EmbeddingError("Embedding provider returned an empty vector")
        self._cached_dimension = len(vector)
        return self._cached_dimension
