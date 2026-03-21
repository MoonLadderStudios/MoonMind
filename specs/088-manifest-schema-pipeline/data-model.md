# Data Model: Manifest Schema & Data Pipeline

**Feature**: `088-manifest-schema-pipeline`

## Entities

### ManifestV0 (Pydantic model)

Top-level manifest document. Fields map 1:1 to the v0 JSON Schema.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| version | Literal["v0"] | ✅ | Schema version |
| metadata | ManifestMetadata | ✅ | Name, description, owner, tags |
| llm | LLMConfig | | Optional LLM for answer generation |
| embeddings | EmbeddingsConfig | ✅ | Provider, model, batchSize |
| vectorStore | VectorStoreConfig | ✅ | Type, indexName, connection |
| dataSources | list[DataSourceConfig] | ✅ | Reader definitions |
| transforms | TransformsConfig | | Splitter, enrichment, PII |
| indices | list[IndexConfig] | ✅ | Index definitions |
| retrievers | list[RetrieverConfig] | ✅ | Named retrievers |
| postprocessors | list[dict] | | Similarity cutoff, dedupe |
| evaluation | EvaluationConfig | | Datasets + metrics |
| run | RunConfig | | Concurrency, batch, error policy |
| observability | dict | | Tracing/log sinks |
| security | SecurityConfig | | PII redaction, metadata allowlist |
| scheduling | str | | Cron expression or "manual" |

### ReaderAdapter (Protocol)

Interface for data source adapters.

| Method | Signature | Description |
|--------|-----------|-------------|
| plan() | () → PlanResult | Enumerate files/docs, estimate sizes |
| fetch() | () → Iterator[(text, metadata)] | Yield document content |
| state() | () → dict | Return cursor for incremental runs |

### EvaluationResult

Metrics output from manifest evaluation.

| Field | Type | Description |
|-------|------|-------------|
| manifest_name | str | Manifest metadata.name |
| dataset_name | str | Evaluated dataset name |
| metrics | list[MetricScore] | Computed metric scores |
| passed | bool | All thresholds met |

### MetricScore

| Field | Type | Description |
|-------|------|-------------|
| name | str | Metric name (e.g., "hitRate@10") |
| score | float | Computed score |
| threshold | float | Required threshold (if any) |
| passed | bool | Score >= threshold |

## Relationships

- ManifestV0 contains DataSourceConfig[] → each maps to a ReaderAdapter type
- ManifestV0.indices[].sources[] reference DataSourceConfig.id
- ManifestV0.retrievers[].indices[] reference IndexConfig.id
- EvaluationConfig.datasets[] are JSONL files loaded at evaluation time
