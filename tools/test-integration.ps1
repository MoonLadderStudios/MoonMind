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

if ($test_file) {
    $env:TEST_TYPE = "integration/$test_file"
} else {
    $env:TEST_TYPE = "integration"
}

docker-compose -f docker-compose.test.yaml build
docker-compose -f docker-compose.test.yaml up --abort-on-container-exit