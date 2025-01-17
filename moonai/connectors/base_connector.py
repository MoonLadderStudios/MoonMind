import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional


class BaseDocument:
    """Standardized document format across all connectors"""
    def __init__(
        self,
        text: str,
        metadata: Dict[str, Any],
        id: Optional[str] = None,
        source: Optional[str] = None,
        timestamp: Optional[str] = None
    ):
        self.text = text
        self.metadata = {
            **metadata,
            "source": source or metadata.get("source", "unknown"),
            "ingestion_timestamp": timestamp or datetime.utcnow().isoformat(),
        }
        self.id = id

    def to_ndjson(self) -> str:
        """Convert document to NDJSON format"""
        return json.dumps({
            "text": self.text,
            "metadata": self.metadata,
            "id": self.id
        }) + "\n"

    @classmethod
    def from_llama_document(cls, doc, source: str = None):
        """Convert a LlamaIndex document to BaseDocument"""
        return cls(
            text=doc.text,
            metadata=doc.metadata or {},
            id=doc.id_ if hasattr(doc, 'id_') else None,
            source=source
        )

class BaseConnector(ABC):
    """Base class for all document connectors"""

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(self.__class__.__name__)

    # @abstractmethod
    # def stream_documents(self, **kwargs) -> Generator[BaseDocument, None, None]:
    #     """Stream documents from the source"""
    #     pass

    def to_ndjson_stream(self, **kwargs) -> Generator[str, None, None]:
        """Convert document stream to NDJSON format"""
        for doc in self.stream_documents(**kwargs):
            yield doc.to_ndjson()

    def batch_documents(
        self,
        batch_size: int = 10,
        **kwargs
    ) -> Generator[List[BaseDocument], None, None]:
        """Batch documents from the stream"""
        batch = []
        for doc in self.stream_documents(**kwargs):
            batch.append(doc)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    def test_connection(self) -> tuple[bool, Optional[str]]:
        """Test connection to the source"""
        try:
            # Try to get one document to test connection
            next(self.stream_documents())
            return True, None
        except Exception as e:
            return False, str(e)

    def _log_progress(self, count: int, interval: int = 10):
        """Log progress at specified intervals"""
        if count % interval == 0:
            self.logger.info(f"Processed {count} documents")