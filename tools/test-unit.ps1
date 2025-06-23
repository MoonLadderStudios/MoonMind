$env:TEST_TYPE = "unit"

docker-compose -f docker-compose.test.yaml build
docker-compose -f docker-compose.test.yaml up --abort-on-container-exit