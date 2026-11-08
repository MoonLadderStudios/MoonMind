# Installed Runtime Resources

A deployment can install one reusable resource package containing preparation
images, persistent caches, fixed service connections, and safe runtime settings.
Ordinary workflows inherit it. Repository Skills still own preparation commands,
locked-input verification, and their output receipts; MoonMind owns container
execution and resource provisioning.

This supports tools that need assets or another service before their main
workload. It uses the existing Container Jobs backend and accepts no project
names or special preparation protocol in MoonMind runtime code.

## Installation and activation

A package is a directory containing `resources.json` and any local image build
contexts it references. Install it on the deployment host:

```bash
moonmind runtime-resources install /path/to/package --project-dir /path/to/MoonMind
```

Installation validates the complete configuration, copies build contexts into
content-addressed packages beneath `deploy/runtime-resources/`, and atomically
writes an activation record. It does not start jobs or change the running stack.
Apply the normal [deployment controller update](../Steps/DockerComposeUpdateSystem.md)
to activate the installed package. Compose reads the activation identity into
the API and runtime worker configurations, so its changed-service recreation
selects the same package for both processes. Subsequent host and Settings
Operations updates retain that selection; routine updates need no reinstall,
workflow inputs, or provider profile changes.

The standard Compose deployment mounts this directory read-only into the API
and runtime worker. `MOONMIND_RUNTIME_RESOURCES_DIRECTORY` selects a different
host directory. `MOONMIND_RUNTIME_RESOURCES_FILE` is an optional in-container
file override for an explicitly managed installation. An absent default package
keeps ordinary defaults; a missing explicitly selected package or invalid file
fails with a configuration diagnostic.

Each process retains its selected package. Updating a package preserves earlier
contexts and activation identities, including inputs used by active builds.
Installation never renames the directory used by the Compose bind mount and
never removes previous packages. Retain those packages while jobs or recovery
may still need them.

## Resource document

The envelope has `schemaVersion: 1` and optional `imageSources`, `cacheSources`,
`serviceConnections`, and `environment` fields. For example:

```json
{
  "schemaVersion": 1,
  "imageSources": [
    {
      "sourceRef": "asset-preparer",
      "image": "asset-preparer:local",
      "build": {
        "context": "preparer",
        "dockerfile": "Dockerfile",
        "validationCommand": ["python3", "--version"]
      }
    }
  ],
  "cacheSources": [
    {
      "cacheRef": "asset-objects",
      "volumeName": "asset_objects",
      "target": "/cache/objects"
    }
  ],
  "environment": {
    "ASSET_PREPARATION_IMAGE": "asset-preparer",
    "ASSET_OBJECT_CACHE": "asset-objects"
  }
}
```

Published images use `sourceRef`, `image`, optional `pullPolicy`, and
`registryCredentialRef`. A maintained image channel with `pullPolicy: always`
receives tool releases on acquisition while retaining installed connection and
cache bindings. Private images use the existing managed credential mechanism.
Source-only installations can declare `build` for first-use builds. These
contexts are retained snapshots: changing the recipe requires installing a new
package. Contexts are package-relative and contain only ordinary build inputs;
linked and special files are refused.

A local build supports optional `target`, string-valued `args`, and
`maxAgeSeconds`; `validationCommand` is required. Registry pull settings cannot
be combined with a local build. The existing image acquisition owner coalesces
concurrent builds, loads the result into the deployment daemon, and validates
it before use. Context contents and executable modes determine freshness.
The build key identifies an image artifact, not runtime compatibility.

Caches retain the existing owner-scoped volume behavior. Resource-file entries
use the same image, cache, and service schemas as backend environment settings.
Explicit backend environment declarations override matching installed opaque
references and retain distinct entries. Duplicate references within one source
and unknown fields are configuration errors.

The safe `environment` literals reach ordinary managed runtime shells through
the existing project-environment projection. Explicit allowlisted deployment
values override installed values; explicit Provider Profile values retain their
existing precedence. Credentials and interpreter/runtime control variables do
not use this channel.

## Fixed services

A service declaration uses the existing `serviceRef`, `networkRef`, `gatewayRef`,
`gatewayImage`, `egressNetworkRef`, `serverName`, `allowedImageSourceRefs`, and
`relay` fields described in [Docker Backend Service](DockerBackendService.md).
Set `provision: true` to have the backend prepare the relay on first use.
`subnet` optionally chooses its isolated IPv4 subnet; otherwise the relay's
listen address selects its `/24` subnet. The relay configuration names one
upstream address and port and explicitly selects `tcp`, `udp`, or both.

The backend creates the isolated network and bounded relay, waits for health,
then performs the same launch and network attestation used for an existing
service. Concurrent workers reconcile Docker resource identities. A stopped
owned relay can restart. Unowned names, changed policy, unhealthy startup, or
unexpected network peers produce diagnostics without removing retained
resources. Existing manually provisioned service declarations keep their
attestation path.

Only granted images can select the service. Its network exposes that fixed
endpoint rather than general LAN access. Endpoint access does not establish
application authentication or read-only permission; those remain properties of
the actual service and the repository-owned operation.
