# Quickstart: Manifest Schema & Data Pipeline

## Prerequisites

- Python 3.11+ with MoonMind installed (`pip install -e .`)
- Qdrant running (`docker compose up qdrant`)
- Embedding provider key (`GOOGLE_API_KEY` or `OPENAI_API_KEY`)

## Validate a Manifest

```bash
moonmind manifest validate -f examples/readers-githubrepositoryreader-example.yaml
```

## Plan (dry-run, no writes)

```bash
moonmind manifest plan -f examples/readers-full-example.yaml
```

## Run (index data)

```bash
export GITHUB_TOKEN="your-token"
export QDRANT_HOST="localhost"
export QDRANT_PORT="6333"
moonmind manifest run -f examples/readers-githubrepositoryreader-example.yaml
```

## Evaluate

```bash
moonmind manifest evaluate -f examples/readers-full-example.yaml --dataset smoke
```

## Run Tests

```bash
./tools/test_unit.sh
```
