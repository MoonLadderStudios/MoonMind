$test_file = $args[0]

if ($test_file) {
    $env:TEST_TYPE = "integration/$test_file"
} else {
    $env:TEST_TYPE = "integration"
}

docker-compose -f docker-compose.test.yaml build
docker-compose -f docker-compose.test.yaml up --abort-on-container-exit