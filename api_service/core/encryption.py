import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet
import structlog

from moonmind.config.settings import settings

logger = structlog.get_logger(__name__)

_ACTIVE_ENCRYPTION_KEY: str | None = None

def get_encryption_key() -> str:
    """
    Retrieves the encryption master key.
    Precedence:
    1. settings.security.ENCRYPTION_MASTER_KEY
    2. Docker secret at /run/secrets/moonmind_master_key
    3. Project-local var/secrets/encryption_master_key
       (If absent, a new key is generated and written here for zero-env startup).
    """
    global _ACTIVE_ENCRYPTION_KEY
    if _ACTIVE_ENCRYPTION_KEY is not None:
        return _ACTIVE_ENCRYPTION_KEY

    # 1. Environment / Settings Override
    if settings.security.ENCRYPTION_MASTER_KEY:
        _ACTIVE_ENCRYPTION_KEY = settings.security.ENCRYPTION_MASTER_KEY
        return _ACTIVE_ENCRYPTION_KEY

    # 2. Docker secret
    docker_secret_path = Path("/run/secrets/moonmind_master_key")
    if docker_secret_path.is_file():
        try:
            key = docker_secret_path.read_text(encoding="utf-8").strip()
            if key:
                _ACTIVE_ENCRYPTION_KEY = key
                return _ACTIVE_ENCRYPTION_KEY
        except Exception as e:
            logger.warning("failed_to_read_docker_secret", error=str(e), path=str(docker_secret_path))

    # 3. Local fallback secret
    repo_root = getattr(settings.workflow, "repo_root", ".")
    local_secret_dir = Path(repo_root) / "var" / "secrets"
    local_secret_path = local_secret_dir / "encryption_master_key"

    if local_secret_path.is_file():
        try:
            key = local_secret_path.read_text(encoding="utf-8").strip()
            if key:
                _ACTIVE_ENCRYPTION_KEY = key
                return _ACTIVE_ENCRYPTION_KEY
        except Exception as e:
            logger.error("failed_to_read_local_secret", error=str(e), path=str(local_secret_path))
            raise ValueError("Could not read local encryption master key.") from e

    # 4. Generate and persist new key for baseline local-first startup
    try:
        local_secret_dir.mkdir(parents=True, exist_ok=True)
        new_key = Fernet.generate_key().decode("utf-8")
        
        # Write securely
        if not local_secret_path.exists():
            local_secret_path.touch(mode=0o600, exist_ok=False)
        local_secret_path.write_text(new_key, encoding="utf-8")
        os.chmod(local_secret_path, stat.S_IRUSR | stat.S_IWUSR)
        
        logger.info("initialized_new_local_encryption_key", path=str(local_secret_path))
        _ACTIVE_ENCRYPTION_KEY = new_key
        return _ACTIVE_ENCRYPTION_KEY
    except Exception as e:
        logger.error("failed_to_initialize_local_secret", error=str(e))
        raise ValueError("Could not resolve or initialize ENCRYPTION_MASTER_KEY") from e
