# Both targets share one BuildKit graph and Python builder. PRs import the
# recipe caches; automatic main runs own their export.
variable "UPSTREAM_COMMIT" {
  default = ""
}

variable "CACHE_EXPORTS" {
  default = false
}

group "default" {
  targets = ["server", "host-base"]
}

target "common" {
  context = "omnigent"
  dockerfile = "deploy/docker/Dockerfile"
  labels = {
    "org.opencontainers.image.revision" = UPSTREAM_COMMIT
  }
  cache-from = [
    "type=gha,scope=credential-free-recovery-server",
    "type=gha,scope=credential-free-recovery-host",
  ]
  output = ["type=docker"]
}

target "server" {
  inherits = ["common"]
  target = "runtime"
  tags = ["moonmind-test-omnigent-server:candidate"]
  cache-to = CACHE_EXPORTS ? ["type=gha,scope=credential-free-recovery-server,mode=max"] : []
}

target "host-base" {
  inherits = ["common"]
  target = "host"
  tags = ["moonmind-test-omnigent-host-base:candidate"]
  cache-to = CACHE_EXPORTS ? ["type=gha,scope=credential-free-recovery-host,mode=max"] : []
}
