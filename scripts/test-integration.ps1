$env:TEST_TYPE = "integration"
docker-compose -f docker-compose.tests.yaml up --abort-on-container-exit