# Development script for MoonMind

Write-Host "Setting up development environment..." -ForegroundColor Green

# Set environment variables for development
$env:FASTAPI_RELOAD = "True"
$env:LOG_LEVEL = "DEBUG"
# TODO: set keycloak to development mode here and make sure keycloak uses it

Write-Host "Stopping existing containers, but not deleting volumes..." -ForegroundColor Cyan
docker-compose --profile keycloak down

Write-Host "Building containers..." -ForegroundColor Cyan
docker-compose --profile keycloak build

Write-Host "Starting containers..." -ForegroundColor Cyan
docker-compose --profile keycloak up -d

Write-Host "Following logs (Ctrl+C to stop watching)..." -ForegroundColor Green
docker-compose --profile keycloak logs -f