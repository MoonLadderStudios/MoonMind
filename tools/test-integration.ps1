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

if (!(Test-Path ".env")) {
    Copy-Item ".env-template" ".env"
}

if ($test_file) {
    docker-compose -f docker-compose.test.yaml run --rm -e TEST_TYPE="integration/$test_file" pytest
} else {
    docker-compose -f docker-compose.test.yaml build orchestrator-tests
    docker-compose -f docker-compose.test.yaml run --rm orchestrator-tests
}
