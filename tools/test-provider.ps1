# Run Jules provider verification tests.
# Requires JULES_API_KEY to be set in the environment.

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$composeFile = Join-Path $repoRoot "docker-compose.test.yaml"
$networkName = if ($env:MOONMIND_DOCKER_NETWORK) { $env:MOONMIND_DOCKER_NETWORK } else { "local-network" }

if (-not $env:JULES_API_KEY) {
    Write-Error "Error: JULES_API_KEY must be set to run live Jules provider verification."
    exit 1
}

# Detect Docker Compose CLI
$composeCmd = $null
if (Get-Command "docker" -ErrorAction SilentlyContinue) {
    $composeVersion = docker compose version 2>$null
    if ($LASTEXITCODE -eq 0) {
        $composeCmd = "docker compose"
    }
}
if (-not $composeCmd -and (Get-Command "docker-compose" -ErrorAction SilentlyContinue)) {
    $composeCmd = "docker-compose"
}
if (-not $composeCmd) {
    Write-Error "Error: docker compose CLI is not available."
    exit 127
}

# Ensure .env exists
$envFile = Join-Path $repoRoot ".env"
$envTemplate = Join-Path $repoRoot ".env-template"
if (-not (Test-Path $envFile)) {
    if (Test-Path $envTemplate) {
        Copy-Item $envTemplate $envFile
        Write-Host "Created $envFile from .env-template for docker compose tests." -ForegroundColor Cyan
    } else {
        Write-Error "Error: missing $envFile and $envTemplate."
        exit 1
    }
}

# Ensure Docker network exists
$networkExists = docker network inspect $networkName 2>$null
if ($LASTEXITCODE -ne 0) {
    docker network create $networkName | Out-Null
    Write-Host "Created Docker network: $networkName" -ForegroundColor Cyan
}

# Build and run pytest service
Invoke-Expression "$composeCmd -f `"$composeFile`" --project-directory `"$repoRoot`" build pytest"
Invoke-Expression "$composeCmd -f `"$composeFile`" --project-directory `"$repoRoot`" run --rm -e JULES_API_KEY -e JULES_API_URL pytest bash -lc `"pytest tests/provider/jules -m 'provider_verification and jules' -q --tb=short -s`""
$testExitCode = $LASTEXITCODE

# Bring down compose services
Invoke-Expression "$composeCmd -f `"$composeFile`" --project-directory `"$repoRoot`" down --remove-orphans" | Out-Null

if ($testExitCode -ne 0) {
    Write-Error "Provider verification tests failed with exit code $testExitCode."
    exit $testExitCode
}

Write-Host "Provider verification tests passed." -ForegroundColor Green
