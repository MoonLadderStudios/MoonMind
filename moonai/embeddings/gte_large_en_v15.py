import logging
import os
from pathlib import Path
from typing import List, Optional

import torch
import torch.nn.functional as F
from llama_index.core.embeddings import BaseEmbedding
from pydantic import Field
from sentence_transformers import SentenceTransformer


class GTELargeEnV15Embedding(BaseEmbedding):
    model: Optional[SentenceTransformer] = Field(default=None, exclude=True)
    embed_dim: int = Field(default=1024)
    device: str = Field(default=None)

    def __init__(self, model_name='Alibaba-NLP/gte-large-en-v1.5', device=None, logger=None):
        super().__init__()

        self._logger = logger or logging.getLogger(__name__)
        self._logger.info(f"Initializing model_name={model_name}")

        # Set up cache directory
        MODEL_DIRECTORY = os.getenv("MODEL_DIRECTORY")
        cache_dir = Path(MODEL_DIRECTORY)
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Initialize the SentenceTransformer model with pinned revision
        self.model = SentenceTransformer(
            model_name,
            trust_remote_code=True,
            revision="104333d6af6f97649377c2afbde10a7704870c7b",
            cache_folder=str(cache_dir)  # Add cache directory
        )
        self.model.max_seq_length = 8192

        # Set device
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)

    def _generate_embeddings(self, texts: List[str], normalize: bool = True) -> List[List[float]]:
        """Generate embeddings for a list of texts."""
        self._logger.debug(f"Generating embeddings for {len(texts)} texts.")
        embeddings = self.model.encode(
            texts,
            batch_size=16,
            show_progress_bar=False,
            device=self.device,
            convert_to_tensor=True
        )
        if normalize:
            embeddings = F.normalize(embeddings, p=2, dim=1)
        return embeddings.cpu().tolist()

    def get_query_embedding(self, query: str) -> List[float]:
        """Get embedding for a single query."""
        return self._generate_embeddings([query])[0]

    def get_text_embedding(self, text: str) -> List[float]:
        """Get embedding for a single text."""
        return self._generate_embeddings([text])[0]

    def get_query_embeddings(self, queries: List[str]) -> List[List[float]]:
        """Get embeddings for a list of queries."""
        return self._generate_embeddings(queries)

    def get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings for a list of texts."""
        return self._generate_embeddings(texts)

    async def _aget_query_embedding(self, query: str) -> List[float]:
        """Async method for getting a query embedding."""
        return self.get_query_embedding(query)

    def _get_query_embedding(self, query: str) -> List[float]:
        """Sync method for getting a query embedding (required by BaseEmbedding)."""
        return self.get_query_embedding(query)

    def _get_text_embedding(self, text: str) -> List[float]:
        """Sync method for getting a text embedding (required by BaseEmbedding)."""
        return self.get_text_embedding(text)
