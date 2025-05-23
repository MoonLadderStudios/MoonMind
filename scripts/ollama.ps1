param (
    [ValidateSet("chat", "embedding")]
    [string]$ModelType = "chat"
)

$env:OLLAMA_MODEL_TYPE = $ModelType
Write-Host "OLLAMA_MODEL_TYPE set to: $env:OLLAMA_MODEL_TYPE" # Inform the user

# Stop existing services
docker-compose -f docker-compose.ollama.yaml down

# Build services (optional, but can be kept)
docker-compose -f docker-compose.ollama.yaml build

# Start services in detached mode, ensuring recreation for env var changes
docker-compose -f docker-compose.ollama.yaml up -d --force-recreate --remove-orphans

# Follow logs (can be kept or removed based on desired behavior)
docker-compose -f docker-compose.ollama.yaml logs -f