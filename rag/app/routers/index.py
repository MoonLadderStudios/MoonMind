import asyncio
import json
import time
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from moonai.config.logging import logger
from moonai.models.models import (DocumentRequest, EmbeddingRequest,
                                  IndexResponse)

from .common import get_qdrant

router = APIRouter(tags=["index"])


async def process_batch(docs):
    batch_start = time.time()
    logger.info(f"Processing batch of {len(docs)} documents")
    logger.debug(f"Batch contents: {docs}")

    # Validate that all documents have valid integer-parseable IDs
    for doc in docs:
        if not doc.get("id"):
            raise HTTPException(
                status_code=400,
                detail="All documents must have an ID"
            )
        try:
            int(doc["id"])  # Just validate, don't store
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail=f"Document ID must be parseable as an integer. Got: {doc['id']}"
            )

    base_docs = [
        BaseDocument(
            text=doc["text"],
            metadata=doc.get("metadata", {}),
            id=str(doc["id"])  # Store as string
        ) for doc in docs
    ]

    logger.info(f"Sending {len(base_docs)} documents to Qdrant")

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        get_qdrant().index_documents,
        base_docs,
        len(base_docs)
    )

    batch_time = time.time() - batch_start
    logger.info(f"Batch processing completed in {batch_time:.2f}s")
    return len(docs)

# TODO: I think this is out of date
@router.post("/index/documents", response_model=IndexResponse)
async def index_documents(documents: List[DocumentRequest]):
    try:
        # Convert API documents to LlamaIndex documents
        from llama_index.core import Document as LlamaDocument

        llama_docs = [
            LlamaDocument(
                text=doc.text,
                metadata=doc.metadata.dict() if doc.metadata else {},
                id_=doc.id
            ) for doc in documents
        ]

        # Process documents
        get_qdrant().index_documents(llama_docs)

        return IndexResponse(
            success=True,
            message="Documents successfully indexed",
            documents_processed=len(documents)
        )
    except Exception as e:
        print(f"Error in index_documents: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={"error": str(e)}
        )

@router.post("/index/documents/stream")
async def stream_documents(request: Request):
    """
    Streams documents for indexing, processing them in batches.
    Expects newline-delimited JSON (NDJSON) in the request body.
    """
    try:
        logger.info(f"Request received for /index/documents/stream - Headers: {request.headers}")
        content_length = request.headers.get("content-length", "0")
        logger.info(f"Content length: {content_length} bytes")

        if content_length == "0":
            logger.warning("Received empty request")
            return StreamingResponse(
                iter(["No documents provided for processing\n"]),
                media_type="text/plain"
            )

        batch = []
        batch_size = 1
        total_processed = 0
        start_time = time.time()

        body_bytes = await request.body()
        lines = body_bytes.split(b'\n')

        async def generate():
            nonlocal batch, total_processed
            line_count = 0
            for line in lines:
                line_count += 1
                if line:
                    try:
                        logger.debug(f"Processing line {line_count}: {line.decode()}")
                        doc = json.loads(line.decode())
                        batch.append(doc)
                        logger.debug(f"Current batch size: {len(batch)}/{batch_size}")

                        if len(batch) >= batch_size:
                            processed = await process_batch(batch)
                            total_processed += processed
                            elapsed = time.time() - start_time
                            status_msg = f"Processed batch of {processed} documents. Total: {total_processed} (Time elapsed: {elapsed:.2f}s)\n"
                            logger.info(status_msg.strip())
                            yield status_msg
                            batch = []

                    except json.JSONDecodeError as e:
                        error_msg = f"Error parsing document at line {line_count}: {str(e)}"
                        logger.error(error_msg)
                        logger.debug(f"Problematic line content: {line.decode()}")
                        yield f"{error_msg}\n"
                        continue

            # Process any remaining documents
            if batch:
                logger.info(f"Processing final batch of {len(batch)} documents")
                processed = await process_batch(batch)
                total_processed += processed
                elapsed = time.time() - start_time
                status_msg = f"Processed final batch of {processed} documents. Total: {total_processed} (Time elapsed: {elapsed:.2f}s)\n"
                logger.info(status_msg.strip())
                yield status_msg

            final_msg = f"Completed processing {total_processed} documents in {elapsed:.2f}s"
            logger.info(final_msg)
            yield f"{final_msg}\n"

        logger.info("Initializing StreamingResponse")
        return StreamingResponse(
            generate(),
            media_type="text/plain"
        )

    except Exception as e:
        error_msg = f"Error processing document stream: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=error_msg
        )

@router.get("/collections/stats")
async def get_collection_stats() -> Dict[str, Any]:
    """Get statistics for all Qdrant collections."""
    try:
        logger.info("Starting Qdrant collections check")
        collection_stats = {}

        if not get_qdrant():
            raise HTTPException(
                status_code=500,
                detail="Qdrant connector not initialized"
            )

        # Get collections and their statistics
        collections = get_qdrant().list_collections()

        for collection in collections:
            try:
                collection_info = get_qdrant().client.get_collection(collection)
                points_count = collection_info.points_count
                collection_stats[collection] = {
                    'points_count': points_count,
                    'vectors_config': collection_info.config.params.vectors,
                }
            except Exception as e:
                logger.warning(f"Could not get stats for collection {collection}: {e}")
                collection_stats[collection] = {'error': str(e)}

        logger.info(f"Collection statistics: {collection_stats}")
        return collection_stats

    except Exception as e:
        logger.error(f"Error getting collection stats: {e}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@router.post("/embeddings")
@router.post("/v1/embeddings")
async def embeddings(request: EmbeddingRequest):
    try:
        # Placeholder response
        return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
