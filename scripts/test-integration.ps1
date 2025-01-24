$env:TEST_TYPE = "integration"
docker-compose -f docker-compose.pytest.yaml up --abort-on-container-exit