import logging
from typing import List

import torch
from llama_index.core.embeddings import BaseEmbedding
from sentence_transformers import SentenceTransformer


class NVEmbedV2Embedding(BaseEmbedding):
    def __init__(self, model_name='nvidia/NV-Embed-v2', device=None, logger=None):
        super().__init__()
        self._logger = logger or logging.getLogger(__name__)
        self._logger.info(f"Initializing NVEmbedV2Embedding with model_name={model_name}")
        self.model = SentenceTransformer(model_name, trust_remote_code=True)
        self.embed_dim = 4096
        self.model.max_seq_length = 32768
        self.model.tokenizer.padding_side = "right"
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.model.to(self.device)
        if self.device == 'cuda':
            self._logger.info("Casting model to FP16")
            self.model = self.model.half()

    def _get_query_embedding(self, query: str) -> List[float]:
        """Required by BaseEmbedding"""
        return self.get_query_embedding(query)

    def _get_text_embedding(self, text: str) -> List[float]:
        """Required by BaseEmbedding"""
        return self.get_text_embedding(text)

    async def _aget_query_embedding(self, query: str) -> List[float]:
        """Required by BaseEmbedding for async operations"""
        return self.get_query_embedding(query)

    async def _aget_text_embedding(self, text: str) -> List[float]:
        """Required by BaseEmbedding for async operations"""
        return self.get_text_embedding(text)

    def add_eos(self, texts):
        return [text + self.model.tokenizer.eos_token for text in texts]

    def get_query_embedding(self, query: str) -> List[float]:
        return self.get_query_embeddings([query])[0]

    def get_text_embedding(self, text: str) -> List[float]:
        return self.get_text_embeddings([text])[0]

    def get_query_embeddings(self, queries: List[str]) -> List[List[float]]:
        task_instruction = "Given a question, retrieve passages that answer the question"
        query_prefix = "Instruct: " + task_instruction + "\nQuery: "
        queries = [query_prefix + q for q in queries]
        queries = self.add_eos(queries)
        with torch.cuda.amp.autocast(enabled=self.device == 'cuda'):
            embeddings = self.model.encode(
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
        with torch.cuda.amp.autocast(enabled=self.device == 'cuda'):
            embeddings = self.model.encode(
                texts,
                batch_size=16,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_tensor=True,
                device=self.device,
            )
        return embeddings.cpu().tolist()
