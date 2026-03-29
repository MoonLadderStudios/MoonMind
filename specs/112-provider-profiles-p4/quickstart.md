# Quickstart: Provider Profiles Phase 4

After this phase is deployed, a new provider profile utilizing `secret_refs` can be launched via standard Manager workflow:
1. Define a `ManagedRuntimeProfile` containing:
   ```json
   {
      "credential_source": "API_KEY",
      "runtime_materialization_mode": "anthropic",
      "secret_refs": {"ANTHROPIC_API_KEY": "secret-db-id-xyz"},
      "clear_env_keys": ["OLD_API_KEY"],
      "file_templates": {"/tmp/custom.json": "{\"key\": \"{{ANTHROPIC_API_KEY}}\"}"}
   }
   ```
2. Agent adapter triggers `ProviderProfileMaterializer` upon startup.
3. Secret `secret-db-id-xyz` is decrypted, old keys removed, and the base environment is constructed cleanly without leaking the credentials back to the Workflow log traces.
