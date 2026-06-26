# Development script for MoonMind

Write-Host "Setting up development environment..." -ForegroundColor Green

# Set environment variables for development and disabled authentication
$env:AUTH_PROVIDER = "disabled"
$env:FASTAPI_RELOAD = "True"
$env:LOG_LEVEL = "DEBUG"
$env:WEBUI_AUTH = "false"  # Disable authentication for development
# TODO: set keycloak to development mode here and make sure keycloak uses it

Write-Host "Stopping existing containers, but not deleting volumes..." -ForegroundColor Cyan
docker-compose --profile vllm down

Write-Host "Building containers..." -ForegroundColor Cyan
docker-compose --profile vllm build

Write-Host "Starting containers..." -ForegroundColor Cyan
docker-compose --profile vllm up -d

Write-Host "Following logs (Ctrl+C to stop watching)..." -ForegroundColor Green
docker-compose logs -f