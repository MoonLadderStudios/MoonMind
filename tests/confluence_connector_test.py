import os
import sys
from pathlib import Path

# Add the dags directory to the Python path
dags_dir = Path(__file__).parent.parent / "dags"
sys.path.append(str(dags_dir))

import pytest
from moon_ai.confluence_connector import ConfluenceConnector


def test_confluence_connection():
    # Get configuration from environment variables
    base_url = os.getenv('CONFLUENCE_BASE_URL')
    api_token = os.getenv('CONFLUENCE_API_TOKEN')
    user_name = os.getenv('CONFLUENCE_USER_NAME')

    # Just test with the first space key for now
    space_key = os.getenv('CONFLUENCE_SPACE_KEYS').split(',')[0]

    assert base_url, "CONFLUENCE_BASE_URL environment variable not set"
    assert api_token, "CONFLUENCE_API_TOKEN environment variable not set"
    assert user_name, "CONFLUENCE_USERNAME environment variable not set"
    assert space_key, "CONFLUENCE_SPACE_KEY environment variable not set"

    connector = ConfluenceConnector(
        base_url=base_url,
        api_token=api_token,
        user_name=user_name
    )

    try:
        # Test connection
        connector.connect()
        assert connector.reader is not None, "Confluence reader was not initialized"
    except Exception as e:
        pytest.fail(f"Failed to connect to Confluence: {e}")


def test_load_documents():
    # Get configuration from environment variables
    base_url = os.getenv('CONFLUENCE_BASE_URL')
    api_token = os.getenv('CONFLUENCE_API_TOKEN')
    user_name = os.getenv('CONFLUENCE_USER_NAME')
    space_key = os.getenv('CONFLUENCE_SPACE_KEYS').split(',')[0]

    # Debug print (will be masked in logs)
    print(f"\nDebug connection info:")
    print(f"Base URL: {base_url}")
    print(f"Username: {user_name}")
    print(f"Token length: {len(api_token) if api_token else 'None'}")
    print(f"Space Key: {space_key}")

    # Create a test connection using the standard Confluence library
    from atlassian import Confluence
    try:
        print("\nTesting direct Confluence connection...")
        # Remove /wiki suffix for direct connection test
        test_base_url = base_url
        print(f"Using base URL: {test_base_url}")

        confluence = Confluence(
            url=test_base_url,
            username=user_name,
            password=api_token,
            cloud=True
        )

        print("Attempting to get spaces...")
        spaces = confluence.get_all_spaces()
        print(f"Direct connection successful! Found {len(spaces)} spaces")

        print("\nTesting space access...")
        pages = confluence.get_all_pages_from_space(space_key)
        print(f"Successfully accessed space {space_key}. Found {len(pages)} pages")

    except Exception as e:
        print(f"\nDirect connection failed: {str(e)}")
        if hasattr(e, 'response'):
            print(f"Response status code: {e.response.status_code}")
            print(f"Response headers: {dict(e.response.headers)}")
            try:
                print(f"Response body: {e.response.json()}")
            except:
                print(f"Raw response text: {e.response.text}")

    print("\nNow testing LlamaIndex ConfluenceReader...")
    connector = ConfluenceConnector(
        base_url=base_url,
        api_token=api_token,
        user_name=user_name,
        cloud=True
    )

    try:
        documents = connector.load_documents(space_key)
        assert documents is not None, "No documents returned"
        assert len(documents) > 0, "No documents found in the specified space"
        print(f"\nRetrieved {len(documents)} documents from space: {space_key}")
    except Exception as e:
        print(f"\nLlamaIndex connection failed: {str(e)}")
        if hasattr(e, 'response'):
            print(f"Response status code: {e.response.status_code}")
            print(f"Response headers: {dict(e.response.headers)}")
            try:
                print(f"Response body: {e.response.json()}")
            except:
                print(f"Raw response text: {e.response.text}")
        pytest.fail(f"Failed to load documents: {e}")
