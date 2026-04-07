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

# Detect Docker Compose command (docker compose vs docker-compose)
$composeDriver = $null
if (Get-Command "docker" -ErrorAction SilentlyContinue) {
    & docker compose version 2>$null
    if ($LASTEXITCODE -eq 0) {
        $composeDriver = "docker"
    }
}
if (-not $composeDriver -and (Get-Command "docker-compose" -ErrorAction SilentlyContinue)) {
    $composeDriver = "docker-compose"
}
if (-not $composeDriver) {
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
& docker network inspect $networkName 2>$null
if ($LASTEXITCODE -ne 0) {
    & docker network create $networkName | Out-Null
    Write-Host "Created Docker network: $networkName" -ForegroundColor Cyan
}

# Helper: run docker compose command with proper argument handling
function Run-Compose {
    param([string[]]$ComposeArgs)
    if ($composeDriver -eq "docker") {
        & docker @("compose", "-f", $composeFile, "--project-directory", $repoRoot) + $ComposeArgs
    } else {
        & "docker-compose" @("-f", $composeFile, "--project-directory", $repoRoot) + $ComposeArgs
    }
}

# Build pytest service using the call operator for safer command execution
Run-Compose @("build", "pytest") 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-Error "Error: docker compose build failed."
    exit $LASTEXITCODE
}

# Run pytest provider verification
Run-Compose @("run", "--rm", "-e", "JULES_API_KEY", "-e", "JULES_API_URL", "pytest", "bash", "-lc", "pytest tests/provider/jules -m 'provider_verification and jules' -q --tb=short -s") 2>&1 | Out-Host
$testExitCode = $LASTEXITCODE

# Bring down compose services (always runs, even on test failure)
Run-Compose @("down", "--remove-orphans") 2>&1 | Out-Null

if ($testExitCode -ne 0) {
    Write-Host "Provider verification tests failed with exit code $testExitCode." -ForegroundColor Red
    exit $testExitCode
}

Write-Host "Provider verification tests passed." -ForegroundColor Green
