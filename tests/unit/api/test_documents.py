import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from api_service.main import app  # Assuming your FastAPI app instance is here
from moonmind.schemas.documents_models import ConfluenceLoadRequest
from api_service.api.dependencies import get_storage_context, get_service_context # Moved import

# Create a TestClient instance
client = TestClient(app)

@pytest.fixture
def mock_confluence_indexer():
    with patch("api_service.api.routers.documents.ConfluenceIndexer") as mock_indexer_class:
        mock_indexer_instance = MagicMock()
        mock_indexer_instance.index.return_value = {"total_nodes_indexed": 5}
        mock_indexer_class.return_value = mock_indexer_instance
        yield mock_indexer_instance

@pytest.fixture
def mock_dependencies():
    with patch("api_service.api.routers.documents.get_storage_context") as mock_storage_context, \
         patch("api_service.api.routers.documents.get_service_context") as mock_service_context, \
         patch("api_service.api.routers.documents.settings") as mock_settings:

        mock_storage_context.return_value = MagicMock()
        mock_service_context.return_value = MagicMock()
        # Mock settings for Confluence enabled
        mock_settings.confluence = MagicMock()
        mock_settings.confluence.confluence_enabled = True
        mock_settings.atlassian = MagicMock()
        mock_settings.atlassian.atlassian_url = "http://fake.confluence.com"
        mock_settings.atlassian.atlassian_username = "user"
        mock_settings.atlassian.atlassian_api_key = "key"

        # Store original dependencies
        original_storage_dependency = app.dependency_overrides.get(get_storage_context)
        original_service_dependency = app.dependency_overrides.get(get_service_context)

        # Override dependencies
        app.dependency_overrides[get_storage_context] = lambda: mock_storage_context()
        app.dependency_overrides[get_service_context] = lambda: mock_service_context()

        yield mock_settings

        # Restore original dependencies
        if original_storage_dependency:
            app.dependency_overrides[get_storage_context] = original_storage_dependency
        else:
            del app.dependency_overrides[get_storage_context]

        if original_service_dependency:
            app.dependency_overrides[get_service_context] = original_service_dependency
        else:
            del app.dependency_overrides[get_service_context]


def test_load_confluence_documents_by_page_id(mock_confluence_indexer, mock_dependencies):
    response = client.post(
        "/v1/documents/confluence/load",
        json={"page_id": "12345"}
    )
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["status"] == "success"
    assert "Successfully loaded 5 nodes from page ID '12345'." in json_response["message"]
    assert json_response["total_nodes_indexed"] == 5

    mock_confluence_indexer.index.assert_called_once()
    call_args = mock_confluence_indexer.index.call_args[1] # Get kwargs
    assert call_args["page_id"] == "12345"
    assert call_args["space_key"] is None
    assert call_args["page_title"] is None
    assert call_args["cql_query"] is None
    assert call_args["max_pages_to_fetch"] is None # Default from Pydantic model if not provided

def test_load_confluence_documents_by_space_and_title(mock_confluence_indexer, mock_dependencies):
    response = client.post(
        "/v1/documents/confluence/load",
        json={"space_key": "SPACE", "page_title": "My Page"}
    )
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["status"] == "success"
    assert "Successfully loaded 5 nodes from page title 'My Page' in space 'SPACE'." in json_response["message"]
    assert json_response["total_nodes_indexed"] == 5

    mock_confluence_indexer.index.assert_called_once()
    call_args = mock_confluence_indexer.index.call_args[1]
    assert call_args["space_key"] == "SPACE"
    assert call_args["page_title"] == "My Page"
    assert call_args["page_id"] is None
    assert call_args["cql_query"] is None

def test_load_confluence_documents_by_cql_query(mock_confluence_indexer, mock_dependencies):
    response = client.post(
        "/v1/documents/confluence/load",
        json={"cql_query": "label = 'test-label'"}
    )
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["status"] == "success"
    assert "Successfully loaded 5 nodes from Confluence using CQL query: 'label = 'test-label''." in json_response["message"]
    assert json_response["total_nodes_indexed"] == 5

    mock_confluence_indexer.index.assert_called_once()
    call_args = mock_confluence_indexer.index.call_args[1]
    assert call_args["cql_query"] == "label = 'test-label'"
    assert call_args["page_id"] is None
    assert call_args["space_key"] is None
    assert call_args["page_title"] is None

def test_load_confluence_documents_by_space_key(mock_confluence_indexer, mock_dependencies):
    response = client.post(
        "/v1/documents/confluence/load",
        json={"space_key": "BIGSPACE", "max_pages_to_fetch": 50}
    )
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["status"] == "success"
    assert "Successfully loaded 5 nodes from Confluence space 'BIGSPACE'." in json_response["message"]
    assert json_response["total_nodes_indexed"] == 5

    mock_confluence_indexer.index.assert_called_once()
    call_args = mock_confluence_indexer.index.call_args[1]
    assert call_args["space_key"] == "BIGSPACE"
    assert call_args["max_pages_to_fetch"] == 50
    assert call_args["page_id"] is None
    assert call_args["page_title"] is None
    assert call_args["cql_query"] is None

def test_load_confluence_documents_confluence_disabled(mock_dependencies):
    # Override confluence_enabled for this specific test
    mock_dependencies.confluence.confluence_enabled = False
    response = client.post(
        "/v1/documents/confluence/load",
        json={"page_id": "12345"}
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "Confluence is not enabled"

def test_load_confluence_documents_indexer_exception(mock_confluence_indexer, mock_dependencies):
    mock_confluence_indexer.index.side_effect = Exception("Indexer failed miserably")
    response = client.post(
        "/v1/documents/confluence/load",
        json={"page_id": "12345"}
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "Indexer failed miserably"

# Tests for Pydantic validation errors (handled by FastAPI automatically, but good to be aware)
def test_load_confluence_invalid_payload_no_identifiers(mock_dependencies):
    response = client.post(
        "/v1/documents/confluence/load",
        json={} # Missing any identifiers
    )
    assert response.status_code == 422 # Unprocessable Entity from Pydantic
    # Pydantic model's custom validator message:
    assert "A loading method must be specified" in response.text


def test_load_confluence_invalid_payload_multiple_identifiers(mock_dependencies):
    response = client.post(
        "/v1/documents/confluence/load",
        json={"page_id": "123", "cql_query": "label='test'"} # Multiple identifiers
    )
    assert response.status_code == 422
    assert "Only one loading method can be specified" in response.text

def test_load_confluence_page_title_without_space_key(mock_dependencies):
    response = client.post(
        "/v1/documents/confluence/load",
        json={"page_title": "My Page"} # page_title without space_key
    )
    assert response.status_code == 422
    assert "space_key is required if page_title is provided" in response.text
