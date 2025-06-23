$test_file = $args[0]

if ($test_file) {
    $env:TEST_TYPE = "e2e/$test_file"
} else {
    $env:TEST_TYPE = "e2e"
}

docker-compose -f docker-compose.test.yaml build
docker-compose -f docker-compose.test.yaml up --abort-on-container-exit