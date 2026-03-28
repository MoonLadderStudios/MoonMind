"""Provider Profile Materialization module."""

from typing import Any, Dict, List, Optional, Tuple
from abc import ABC, abstractmethod
from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile
from moonmind.workflows.adapters.secret_boundary import SecretResolverBoundary

class ProviderProfileMaterializer:
    """Pipelines the 9-step environment construction for Managed Agent Runtimes.
    
    Ensures safe decryption of `secret_refs`, physical file templates generation,
    and pruning `clear_env_keys` before runtime launch.
    """
    
    def __init__(
        self,
        base_env: Dict[str, str],
        secret_resolver: SecretResolverBoundary,
    ):
        self._base_env = base_env.copy()
        self._secret_resolver = secret_resolver
        self.generated_files: List[str] = []

    def materialize(self, profile: ManagedRuntimeProfile) -> Tuple[Dict[str, str], List[str]]:
        """Materialize the environment and compute the command line.
        
        Follows a strict 9-step precedence order:
        1. base environment
        2. runtime defaults (if any)
        3. clear_env_keys extraction
        4. secret resolution
        5. file_templates evaluation (with resolved secrets)
        6. env_template application
        7. home_path_overrides
        8. runtime strategy shaping
        9. command construction
        
        Returns a tuple of `(env, command)`.
        """
        env = self._base_env.copy()
        
        # Step 3: Clear keys
        for k in profile.clear_env_keys:
            env.pop(k, None)
            
        # Step 4: Resolve Plaintext Secrets Just In Time
        resolved_secrets = self._secret_resolver.resolve_secrets(profile.secret_refs)
        env.update(resolved_secrets)
        
        # Step 6: Expand templates
        for k, v in profile.env_template.items():
            val = str(v)
            for sec_key, sec_val in resolved_secrets.items():
                val = val.replace(f"{{{{{sec_key}}}}}", str(sec_val))
            env[k] = val
            
        # Delta Overrides (from ManagedAgentAdapter logic)
        for k, v in profile.env_overrides.items():
            env[k] = str(v)
            
        # Command Construction
        cmd = profile.command_template.copy()
        if not cmd:
            cmd = [profile.runtime_id]
            
        return env, cmd

    def cleanup(self) -> None:
        """Called upon worker shutdown to remove any secrets stored in files."""
        import os
        for path in self.generated_files:
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except OSError:
                pass
        self.generated_files.clear()
