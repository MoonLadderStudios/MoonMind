# Llama Index Manifest System Tech Brief

## Introduction
The Llama Index Manifest System enables declarative configuration of Llama Index components. By authoring manifests, teams can predefine the configuration and data sources for readers and other components so that they can be recreated, synced, or updated consistently.

A manifest for readers, for example, allows users to load data from multiple sources with a single definition. When updates or full reloads are required, the existing manifest streamlines the process.

## System Overview
The system is composed of three core ideas:

- **Manifest format** similar to Kubernetes manifests, providing a consistent structure for configuration files.
- **Manifest kinds** that map to Llama Index components (currently focused on readers).
- **Loader classes** that parse manifests and dynamically import the correct Llama Index component, instantiating classes (e.g., `GitHubRepositoryReader`) with parameters supplied in the manifest.

Although readers are the initial focus, future manifest kinds may target vector stores or other Llama Index components.

## Key Behaviors
- `init` fields are passed directly to a component's constructor.
- `load_data` fields are passed to the component's `load_data` method (when present).
- If the `init` section references nested objects, they are recursively initialized before the component is constructed.

## Root Manifest Structure
Root manifests draw inspiration from Kubernetes and include the following top-level fields:

- `apiVersion`
- `kind`
- `metadata`
- `spec`

## Readers Manifest
When `kind` is `Readers`, the `spec` field must contain a `readers` list. Each reader entry includes:

- `type`
- `enabled`
- `auth` (reserved for future use)
- `init`
- `load_data` (optional)

The `load_data` section can be omitted when no additional parameters are required beyond those supplied to `init`. The `enabled` flag supports selective loading, allowing entries to remain in the manifest while temporarily disabled.

## Secrets Management
While secrets may be hard-coded, the preferred pattern uses `secretRef` to defer resolution to a provider. The current provider, `profile`, pulls values from a user's profile. For example:

```yaml
github_token:
  secretRef:
    provider: profile
    key: GITHUB_TOKEN
```

## Requirements
- Define reader configurations declaratively.
- Enforce one manifest entry per reader type, with shared credentials defined externally. Multiple credentials of the same reader type require separate manifests.
- Support the GitHub Repository Reader, Confluence Reader, and Google Drive Reader.
- Target a single vector collection per manifest, leaving collection definition to higher-level configuration.

## Architecture Considerations
- Follow declarative principles for describing system state.
- Support import and export in YAML or JSON formats.
- Persist manifests (YAML or JSON) in a database.
- Provide intelligent syncing or re-indexing that tracks last indexed times and detects changes.
- Plan for future integration of permissions and ownership metadata.

## Open Questions
- How should permissions be enforced so that users can access only the readers they are allowed to use?
- What is the best process for transforming manifests into instantiated classes with invoked methods? Which intermediate data structures support reusable pipelines for actions like re-syncing all data sources?
