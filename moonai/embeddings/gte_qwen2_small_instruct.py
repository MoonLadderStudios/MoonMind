import logging
import os
from pathlib import Path
from typing import List, Optional

import torch
from llama_index.core.embeddings import BaseEmbedding
from pydantic import Field
from sentence_transformers import SentenceTransformer


class GTEQwen2SmallInstructEmbedding(BaseEmbedding):
    model: Optional[SentenceTransformer] = Field(default=None, exclude=True)
    embed_dim: int = Field(default=1536)
    device: str = Field(default=None)

    def __init__(self, model_name='Alibaba-NLP/gte-Qwen2-1.5B-instruct', device=None, logger=None):
        super().__init__()
        self._logger = logger or logging.getLogger(__name__)
        self._logger.info(f"Initializing GTE Qwen2 1.5B Instruct with model_name={model_name}")

        # Set up cache directory
        MODEL_DIRECTORY = os.getenv("MODEL_DIRECTORY")
        cache_dir = Path(MODEL_DIRECTORY)
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Initialize the model with cache directory and pinned revision
        self._model = SentenceTransformer(
            model_name,
            trust_remote_code=True,
            revision="8fdde9cf9d708de7289a58a29877ddf6ae9b618c",
            cache_folder=str(cache_dir)  # Add cache directory
        )
        self._model.max_seq_length = 8192
        self._model.tokenizer.padding_side = "right"

        # Set device
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self._model.to(self.device)

    @property
    def model(self) -> SentenceTransformer:
        return self._model

    def add_eos(self, texts):
        return [text + self._model.tokenizer.eos_token for text in texts]

    def get_query_embedding(self, query: str) -> List[float]:
        return self.get_query_embeddings([query])[0]

    def get_text_embedding(self, text: str) -> List[float]:
        return self.get_text_embeddings([text])[0]

    def get_query_embeddings(self, queries: List[str]) -> List[List[float]]:
        task_instruction = "Given a question, retrieve passages that answer the question"
        query_prefix = "Instruct: " + task_instruction + "\nQuery: "
        queries = [query_prefix + q for q in queries]
        queries = self.add_eos(queries)
        # Use autocast for mixed precision
        with torch.cuda.amp.autocast(enabled=self.device == 'cuda'):
            embeddings = self._model.encode(
                queries,
                batch_size=16,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_tensor=True,
                device=self.device,
            )
        return embeddings.cpu().tolist()

    def get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        texts = self.add_eos(texts)
        # Use autocast for mixed precision
        with torch.cuda.amp.autocast(enabled=self.device == 'cuda'):
            embeddings = self._model.encode(
                texts,
                batch_size=16,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_tensor=True,
                device=self.device,
            )
        return embeddings.cpu().tolist()

    async def _aget_query_embedding(self, query: str) -> List[float]:
        # Implement async version by calling sync version
        return self.get_query_embedding(query)

    def _get_query_embedding(self, query: str) -> List[float]:
        # Implement required abstract method
        return self.get_query_embedding(query)

    def _get_text_embedding(self, text: str) -> List[float]:
        # Implement required abstract method
        return self.get_text_embedding(text)
