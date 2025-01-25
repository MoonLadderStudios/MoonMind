docker-compose -f docker-compose.ollama.yaml down
docker-compose -f docker-compose.ollama.yaml build
docker-compose -f docker-compose.ollama.yaml up -d
docker-compose -f docker-compose.ollama.yaml logs -f