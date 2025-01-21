$env:TEST_TYPE = "unit"
docker-compose -f docker-compose.tests.yaml up --abort-on-container-exit