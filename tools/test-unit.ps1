$testComposeProjectName = if ($env:MOONMIND_TEST_COMPOSE_PROJECT_NAME) { $env:MOONMIND_TEST_COMPOSE_PROJECT_NAME } else { "moonmind-test" }
if ($testComposeProjectName -notmatch '^moonmind-test(?:-[a-z0-9][a-z0-9_-]*)?$') {
    Write-Error "MOONMIND_TEST_COMPOSE_PROJECT_NAME must be 'moonmind-test' or start with 'moonmind-test-'."
    exit 2
}

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

docker-compose --project-name $testComposeProjectName -f docker-compose.test.yaml build pytest
docker-compose --project-name $testComposeProjectName -f docker-compose.test.yaml run --rm pytest
$testExitCode = $LASTEXITCODE
docker-compose --project-name $testComposeProjectName -f docker-compose.test.yaml down --remove-orphans
exit $testExitCode
