param (
    [switch]$LoadChatModel,
    [switch]$LoadEmbeddingModel
)

$modes = [System.Collections.Generic.List[string]]::new()
if ($LoadChatModel) {
    $modes.Add("chat")
}
if ($LoadEmbeddingModel) {
    $modes.Add("embed")
}

# Default to chat if no specific mode is selected
if ($modes.Count -eq 0) {
    $modes.Add("chat")
    Write-Host "No specific model type selected, defaulting to OLLAMA_MODES=chat"
}
$OLLAMA_MODES_VALUE = $modes -join ","

$env:OLLAMA_MODES = $OLLAMA_MODES_VALUE
Write-Host "OLLAMA_MODES set to: $env:OLLAMA_MODES"

# Stop existing services
docker-compose -f docker-compose.ollama.yaml down

# Build services (optional, but can be kept)
docker-compose -f docker-compose.ollama.yaml build

# Start services in detached mode, ensuring recreation for env var changes
docker-compose -f docker-compose.ollama.yaml up -d --force-recreate --remove-orphans

# Follow logs (can be kept or removed based on desired behavior)
docker-compose -f docker-compose.ollama.yaml logs -f