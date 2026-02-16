# Run pre-commit checks first
Write-Host "Running pre-commit checks..." -ForegroundColor Cyan
pre-commit run --all-files

if ($LASTEXITCODE -ne 0) {
    Write-Host "Pre-commit checks failed. Please fix formatting issues and commit changes." -ForegroundColor Red
    exit 1
}

Write-Host "Pre-commit checks passed!" -ForegroundColor Green
Write-Host ""

# Run unit tests
if (!(Test-Path ".env")) {
    Copy-Item ".env-template" ".env"
}

docker-compose -f docker-compose.test.yaml build pytest
docker-compose -f docker-compose.test.yaml run --rm pytest
