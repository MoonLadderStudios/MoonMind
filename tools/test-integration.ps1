# Run pre-commit checks first
Write-Host "Running pre-commit checks..." -ForegroundColor Cyan
pre-commit run --all-files

if ($LASTEXITCODE -ne 0) {
    Write-Host "Pre-commit checks failed. Please fix formatting issues and commit changes." -ForegroundColor Red
    exit 1
}

Write-Host "Pre-commit checks passed!" -ForegroundColor Green
Write-Host ""

# Run integration tests
$test_file = $args[0]
$networkName = if ($env:MOONMIND_DOCKER_NETWORK) { $env:MOONMIND_DOCKER_NETWORK } else { "local-network" }

if (!(Test-Path ".env")) {
    Copy-Item ".env-template" ".env"
}

docker network inspect $networkName *> $null
if ($LASTEXITCODE -ne 0) {
    docker network create $networkName | Out-Null
    Write-Host "Created Docker network: $networkName" -ForegroundColor Cyan
}

if ($test_file) {
    docker-compose -f docker-compose.test.yaml run --rm -e TEST_TYPE="integration/$test_file" pytest
    docker-compose -f docker-compose.test.yaml down --remove-orphans
} else {
    docker-compose -f docker-compose.test.yaml build pytest
    docker-compose -f docker-compose.test.yaml run --rm pytest bash -lc "pytest tests/integration -m 'integration_ci' -q --tb=short"
    docker-compose -f docker-compose.test.yaml down --remove-orphans
}
