import os

import pytest
from airflow.models import DagBag


def test_dag_structure():
    dag_bag = DagBag(dag_folder="/opt/airflow/dags", include_examples=False)

    # Get all DAGs that start with 'confluence_ingest_'
    confluence_dags = [dag for dag_id, dag in dag_bag.dags.items()
                      if dag_id.startswith('confluence_ingest_')]

    # Log all found DAGs
    print("\nFound Confluence DAGs:")
    for dag in confluence_dags:
        print(f"- {dag.dag_id}")

    assert len(confluence_dags) > 0, "No Confluence ingestion DAGs found"

    # Test structure of each Confluence DAG
    for dag in confluence_dags:
        assert "ingest_confluence_space" in dag.task_ids, f"Required task not found in DAG: {dag.dag_id}"


# Get actual space keys from environment
space_keys = [key.strip() for key in os.environ.get('CONFLUENCE_SPACE_KEYS', '').split(',') if key.strip()]
print(f"\nTesting with space keys: {space_keys}")

@pytest.mark.parametrize("space_key", space_keys)
def test_ingest_task_creation(space_key):
    from ingest_confluence_dag import create_ingest_task

    print(f"\nTesting task creation for space key: {space_key}")
    # Test that the task callable can be created without errors
    task_callable = create_ingest_task(space_key)
    assert callable(task_callable), f"Task creation failed for space key: {space_key}"


class MockTaskInstance:
    """Mock Airflow TaskInstance for testing"""
    def __init__(self):
        self.log = MockLogger()


class MockLogger:
    """Mock Logger for testing"""
    def info(self, msg):
        pass

    def error(self, msg, exc_info=False):
        pass
