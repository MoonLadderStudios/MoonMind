import os
import pytest
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http.models import CountRequest # Added import
from fastapi.testclient import TestClient
from api_service.main import app
# from moonmind.config.settings import settings # If needed directly
# from moonmind.models.documents_models import ConfluenceLoadRequest # If needed for payload construction outside TestClient json

# Integration tests for Confluence document loading and querying

load_dotenv()

ATLASSIAN_URL = os.getenv("ATLASSIAN_URL")
ATLASSIAN_USERNAME = os.getenv("ATLASSIAN_USERNAME")
ATLASSIAN_API_KEY = os.getenv("ATLASSIAN_API_KEY")
TEST_CONFLUENCE_SPACE_KEY = os.getenv("TEST_CONFLUENCE_SPACE_KEY")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333")) # Ensure string default for int conversion
QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "moonmind_documents") # Ensure this matches your actual collection name from settings

skip_if_missing_env_vars = pytest.mark.skipif(
    not all([ATLASSIAN_URL, ATLASSIAN_USERNAME, ATLASSIAN_API_KEY, TEST_CONFLUENCE_SPACE_KEY]),
    reason="Required Confluence environment variables are not set in .env"
)

@pytest.fixture(scope="module")
def e2e_setup():
    # ATLASSIAN_URL, ATLASSIAN_USERNAME, ATLASSIAN_API_KEY, TEST_CONFLUENCE_SPACE_KEY,
    # QDRANT_HOST, QDRANT_PORT, QDRANT_COLLECTION_NAME are loaded at module level.
    # load_dotenv() is also called at module level.

    qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    test_client = TestClient(app) # app is imported from moonmind.application at module level

    # No Qdrant cleanup or recreation logic in this iteration.
    # Assume the collection QDRANT_COLLECTION_NAME exists.

    yield {
        "test_client": test_client,
        "qdrant_client": qdrant_client,
        "collection_name": QDRANT_COLLECTION_NAME
    }

    # Teardown: close the qdrant client
    qdrant_client.close()
    # print("Qdrant client closed.") # Optional: for debugging during test runs

@skip_if_missing_env_vars
def test_load_and_query_confluence_documents(e2e_setup):
    test_client = e2e_setup["test_client"]
    qdrant_client = e2e_setup["qdrant_client"]
    collection_name = e2e_setup["collection_name"]

    # Ensure TEST_CONFLUENCE_SPACE_KEY is loaded at module level
    payload = {
        "space_key": TEST_CONFLUENCE_SPACE_KEY,
        # Consider using a small "max_num_results" for test speed if space is large
        # "max_num_results": 5
    }

    response = test_client.post("/documents/confluence/load", json=payload)

    assert response.status_code == 200, f"API call failed: {response.text}"
    response_json = response.json()
    assert response_json["status"] == "success"
    assert "total_nodes_indexed" in response_json
    assert response_json["total_nodes_indexed"] > 0

    # Optional: Give a moment for indexing if there's any async behavior not awaited by the endpoint
    # import time
    # time.sleep(2) # Usually not needed if endpoint is synchronous

    # Query Qdrant to verify documents were inserted
    # Ensure the collection name used by the qdrant_client matches the one used by the app
    # The collection_name from e2e_setup should be correct if derived from settings.

    # It's possible the collection might not exist if no documents were indexed
    # or if the endpoint failed silently before indexing.
    # A robust test might first check collection existence or rely on the count.
    try:
        count_response = qdrant_client.count(
            collection_name=collection_name,
            exact=True
        )
        assert count_response.count > 0
        # Optionally, assert count_response.count == response_json["total_nodes_indexed"]
        # This requires careful consideration of node vs. document count.
        # For now, just checking > 0 is a good start.

    except Exception as e:
        # This might happen if the collection doesn't exist or Qdrant is unavailable
        # For this test, we assume Qdrant is up and the collection should be created by the load process.
        pytest.fail(f"Qdrant query failed: {e}")
