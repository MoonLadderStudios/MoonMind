# Development script for MoonMind
# This script runs the containers needed for development without the qdrant container
# and enables hot reload for the API service

# Args are added as specific containers to build and run
# Default containers for dev (excluding qdrant since we don't need it locally)
$defaultContainers = @("ui", "api", "init-vector-db")
$specificContainers = if ($args.Count -gt 0) { $args } else { $defaultContainers }

Write-Host "Setting up development environment..." -ForegroundColor Green
Write-Host "Containers to run: $($specificContainers -join ', ')" -ForegroundColor Yellow

# Set environment variables for development
$env:FASTAPI_RELOAD = "True"
$env:LOG_LEVEL = "DEBUG"

# Check if we need to set a remote qdrant URL when not running qdrant locally
if ($specificContainers -notcontains "qdrant") {
    Write-Host "Note: Running without local qdrant container." -ForegroundColor Yellow
    Write-Host "Make sure QDRANT_URL is set in your .env file to point to a remote qdrant instance." -ForegroundColor Yellow
}

Write-Host "Stopping any existing containers..." -ForegroundColor Cyan
docker-compose -f docker-compose.yaml down

Write-Host "Building containers..." -ForegroundColor Cyan
docker-compose -f docker-compose.yaml build @specificContainers

Write-Host "Starting containers..." -ForegroundColor Cyan
docker-compose -f docker-compose.yaml up -d @specificContainers

Write-Host "Following logs (Ctrl+C to stop watching)..." -ForegroundColor Green
docker-compose -f docker-compose.yaml logs -f