# ManifestIngest consolidation histories

These histories were captured with the unmodified Python workflow definitions
from commit `0ff48324335c77e8462d45650eefe740cca545b5` on a Temporal test server.
`compilation.json` uses the previously registered compile-and-summarize class.
`compilation-default.json` preserves the old omitted-action `apply` failure.
`nodes.json` uses the alternate class, its underscore Activity command contracts,
two child executions, and all six Update names, including a manifest replacement
applied after the children finish.

Compilation and summary payloads came from real manifest helpers and the local
artifact service. The historical alternate Activity facade exposes compiled
nodes as expected by the old command contract; child responses are hermetic
recording fixtures. Current integration tests separately run the consolidated
registration with actual UserWorkflow children and catalogued Activities.
These are command-compatibility evidence, not deployment drain qualification.
