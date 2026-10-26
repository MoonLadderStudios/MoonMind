# Fixed service access for container jobs

This optional example is not part of the default deployment. It enables a
repository-owned preparation image to reach one operator-approved service, not
the host network, general LAN, or arbitrary destinations. Project protocols,
lockfiles, preparation commands and evidence formats stay in their repositories.

## Authority and limitations

An explicit declaration grants endpoint access to any container-enabled workflow
using one of its `allowedImageSourceRefs`. It is a single-operator deployment
grant, not a per-user or per-step role system. Jobs sharing the same service
network share that boundary. The relay does **not** enforce application-level
read-only operations. An unauthenticated upstream may accept writes or deletes;
use an upstream read-only account/service when required. Review that risk before
approving the exact target, protocols, image sources and network configuration.
No declaration is enabled by default, and no credential is passed to jobs.

The relay forwards only to its fixed literal IPv4 destination. TCP and UDP
connections share a bounded limit and idle timeout. It has no CONNECT/SOCKS API,
DNS forwarding or IP routing. The internal network must use Docker's `isolated`
IPv4 gateway mode, not just `internal: true`, which alone can expose host bridge
services. IPv6 is disabled. A deployment whose Docker version cannot realize
that mode must upgrade or leave the grant disabled; do not substitute host,
ordinary bridge or a broad private-network allowance.

## Deployment preparation

1. Build/pull a MoonMind image containing `moonmind.security.service_relay`. The
   ordinary MoonMind image already packages the module and Python; there is no
   additional agent-host image dependency. Set the same relay image reference in
   the example Compose file and `gatewayImage`. The trusted worker verifies the declared relay image and canonical read-only
   launch, checks the running configuration, and records its implementation
   digest as evidence without requiring worker/relay source equality.
2. Replace documentation-only addresses and names in `relay.json`, `connections.json`
   and `compose.yaml`. Pick an unused internal subnet. `targetHost` is the exact
   upstream IPv4 address; the relay alone can reach it. `serverName` is the
   service's original DNS/TLS identity. Preserve the original port. The backend
   supplies a single hosts-file mapping to the relay and uses loopback-only DNS
   in jobs, so clients need no MagicDNS or broader DNS access.
3. Approve the deployment/network change separately, then deploy this optional
   Compose project. Keep its root filesystem and config mount read-only, all
   capabilities dropped, IP forwarding disabled, and only the two declared
   networks attached. No port is published to the host. Health checks use an
   owner-only Unix socket to verify the running process's loaded configuration;
   they do not probe the upstream.
4. Declare the preparation image through the existing
   `MOONMIND_CONTAINER_BACKEND_IMAGE_SOURCES` setting, plus any approved cache
   sources. Put the compact JSON array from `connections.json` in
   `MOONMIND_CONTAINER_BACKEND_SERVICE_CONNECTIONS` for the API and trusted
   container worker. Root Compose forwards it, empty by default. It is not
   supplied by workflow arguments. Read-only workspace grants remain enforced.
5. Confirm the configured client image's user can write the admitted workspace
   and cache target. The generic job backend honors the image's user; it does
   not override the user or repair file permissions. Do not use permission
   broadening or mount the Docker socket as a workaround.

A job requests ordinary image/workspace/cache/output fields plus
`"networkMode": "service", "serviceRef": "asset-store"`. It cannot combine this
with public bridge networking or name raw network/host settings. The backend
sets `MOONMIND_SERVICE_HOST` and `MOONMIND_SERVICE_PORT`; clients retain their
original service URL and certificate validation. Unknown grants, wrong image
sources, stale relay configuration, extra attachments, external DNS, unsafe
mounts or unavailable launch evidence fail closed. Cleanup removes only the
owned job, not the shared relay, network, images or caches; revoking a grant does
not prevent cleanup.

## Repository settings in ordinary workflows

The trusted `temporal-worker-agent-runtime` must receive both the repository's
non-secret values and their names in
`MOONMIND_MANAGED_SESSION_NON_SECRET_ENV_KEYS`. Root Compose already loads the
optional deployment `.env` into that worker; an explicit operator-owned Compose
override is another supported delivery path. Merely listing a name does not
create its value. For example, a deployment override can contain:

```yaml
services:
  temporal-worker-agent-runtime:
    environment:
      MOONMIND_MANAGED_SESSION_NON_SECRET_ENV_KEYS: ASSET_IMAGE_SOURCE_REF,ASSET_SERVICE_REF,ASSET_SERVER_URL,ASSET_REPOSITORY_NAME
      ASSET_IMAGE_SOURCE_REF: asset-client
      ASSET_SERVICE_REF: asset-store
      ASSET_SERVER_URL: asset://assets.example.test:41300
      ASSET_REPOSITORY_NAME: project-content
```

These illustrative names belong to the consuming repository, not MoonMind's
core schema. Retain any other existing non-secret declarations your deployment
needs. Ordinary generic workflows resolve the declared values on the trusted
worker and carry them into the runtime's login shell. Workflows cannot override
them. Invalid names, credential-looking keys or URLs, and protected execution,
network, authentication and MoonMind runtime controls are refused. This is not a
secret delivery interface. Never list credentials or use this setting to bypass
service/image admission; it supplies repository configuration, not authority.
The legacy managed-session declaration behavior is unchanged. In the generic
Omnigent path, `MOONMIND_*` is a reserved namespace, not a project-setting
channel; use repository-owned names as above. No example is applied
automatically or changes the default deployment.

## Verification

Unit tests cover admission, launch construction, attestation drift, cleanup and
bounded actual loopback TCP/UDP forwarding. Qualify the real deployment by
running the repository's preparation from an empty admitted workspace, checking
its output receipt and exact content identity, then running the native consumer.
Also test that host/LAN destinations other than the service, external DNS, IPv6
and an unapproved image source are inaccessible. This Docker-network and actual
service qualification is distinct from mock attestation or loopback tests. Never
claim a successful content download from an image build or healthy relay alone.
