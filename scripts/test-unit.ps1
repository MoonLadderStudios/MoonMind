$env:TEST_TYPE = "unit"

docker-compose -f docker-compose.pytest.yaml build
docker-compose -f docker-compose.pytest.yaml up --abort-on-container-exit