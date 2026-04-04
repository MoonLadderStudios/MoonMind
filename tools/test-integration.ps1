# Run pre-commit checks first
Write-Host "Running pre-commit checks..." -ForegroundColor Cyan
pre-commit run --all-files

if ($LASTEXITCODE -ne 0) {
    Write-Host "Pre-commit checks failed. Please fix formatting issues and commit changes." -ForegroundColor Red
    exit 1
}

Write-Host "Pre-commit checks passed!" -ForegroundColor Green
Write-Host ""

param(
    [switch]$ProviderVerification,
    [string]$TestFile
)

if (!(Test-Path ".env")) {
    Copy-Item ".env-template" ".env"
}

if ($TestFile) {
    docker-compose -f docker-compose.test.yaml run --rm -e TEST_TYPE="integration/$TestFile" pytest
} elseif ($ProviderVerification) {
    # Run provider verification tests (real credentials required)
    Write-Host "Running provider verification tests (requires credentials)..." -ForegroundColor Yellow
    docker-compose -f docker-compose.test.yaml build pytest
    docker-compose -f docker-compose.test.yaml run --rm pytest bash -lc "pytest tests/integration -m 'provider_verification and jules' -v --tb=short"
} else {
    # Run hermetic integration CI tests only (no external credentials)
    Write-Host "Running hermetic integration CI tests..." -ForegroundColor Cyan
    docker-compose -f docker-compose.test.yaml build pytest
    docker-compose -f docker-compose.test.yaml run --rm pytest bash -lc "pytest tests/integration -m 'integration_ci' -q --tb=short"
}
