import os
from unittest.mock import Mock, patch

import pytest
from airflow.models import DagBag


def test_dag_structure():
    dag_bag = DagBag(dag_folder="/opt/airflow/dags", include_examples=False)
    dag = dag_bag.get_dag("check_qdrant_collections_dag")
    assert dag is not None, "DAG is not loaded"
    assert "check_qdrant_collections" in dag.task_ids, "Task not found in DAG"


@pytest.fixture
def mock_qdrant_response():
    collection_info = Mock()
    collection_info.points_count = 100
    collection_info.config.params.vectors = {"size": 1536, "distance": "Cosine"}
    return collection_info


@pytest.mark.parametrize("task_id", ["check_qdrant_collections"])
def test_task_run(task_id, mock_qdrant_response):
    from check_qdrant_collections_dag import check_qdrant_collections

    # Mock the QdrantConnector
    with patch('moon_ai.qdrant_connector.QdrantConnector') as mock_connector:
        # Configure the mock
        instance = mock_connector.return_value
        instance.list_collections.return_value = ["test_collection"]
        instance.client.get_collection.return_value = mock_qdrant_response

        # Mock the task instance for XCom
        mock_task_instance = Mock()
        mock_context = {'task_instance': mock_task_instance}

        # Run the task
        result = check_qdrant_collections(**mock_context)

        # Verify the results
        assert isinstance(result, dict), "Result should be a dictionary"
        assert "test_collection" in result, "Should contain test collection"
        assert result["test_collection"]["points_count"] == 100
        assert result["test_collection"]["vectors_config"] == {"size": 1536, "distance": "Cosine"}

        # Verify XCom push was called
        mock_task_instance.xcom_push.assert_called_once_with(
            key='collection_stats',
            value=result
        )
