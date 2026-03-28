import asyncio
from unittest.mock import patch, AsyncMock

import pytest

from moonmind.auth.secret_refs import (
    parse_secret_ref,
    SecretMissingError,
    SecretAccessDeniedError,
    SecretUnsupportedBackendError,
    SecretDecryptionError,
)
from moonmind.auth.resolvers import (
    EnvSecretResolver,
    DbEncryptedSecretResolver,
    ExecSecretResolver,
    RootSecretResolver,
)


@pytest.mark.asyncio
async def test_env_resolver_success(monkeypatch):
    monkeypatch.setenv("DUMMY_ENV_VAR", "super-secret")
    ref = parse_secret_ref("env://DUMMY_ENV_VAR")
    resolver = EnvSecretResolver()
    assert await resolver.resolve(ref) == "super-secret"


@pytest.mark.asyncio
async def test_env_resolver_missing():
    ref = parse_secret_ref("env://NOT_REAL_ENV_VAR_123")
    resolver = EnvSecretResolver()
    with pytest.raises(SecretMissingError) as exc:
        await resolver.resolve(ref)
    assert "NOT_REAL_ENV_VAR" in str(exc.value)


@pytest.mark.asyncio
@patch("moonmind.auth.resolvers.db_resolver.SecretsService")
@patch("moonmind.auth.resolvers.db_resolver.async_session_maker")
async def test_db_resolver_success(mock_session_maker, mock_secrets_service):
    # Setup mock session
    mock_session = AsyncMock()
    mock_session_context = AsyncMock()
    mock_session_context.__aenter__.return_value = mock_session
    mock_session_maker.return_value = mock_session_context

    mock_secrets_service.get_secret = AsyncMock(return_value="db-secret")

    ref = parse_secret_ref("db://my-slug")
    resolver = DbEncryptedSecretResolver()
    assert await resolver.resolve(ref) == "db-secret"


@pytest.mark.asyncio
@patch("moonmind.auth.resolvers.db_resolver.SecretsService")
@patch("moonmind.auth.resolvers.db_resolver.async_session_maker")
async def test_db_resolver_missing(mock_session_maker, mock_secrets_service):
    mock_session = AsyncMock()
    mock_session_context = AsyncMock()
    mock_session_context.__aenter__.return_value = mock_session
    mock_session_maker.return_value = mock_session_context

    mock_secrets_service.get_secret = AsyncMock(return_value=None)

    ref = parse_secret_ref("db://missing-slug")
    resolver = DbEncryptedSecretResolver()
    with pytest.raises(SecretMissingError):
        await resolver.resolve(ref)


@pytest.mark.asyncio
@patch("moonmind.auth.resolvers.db_resolver.SecretsService")
@patch("moonmind.auth.resolvers.db_resolver.async_session_maker")
async def test_db_resolver_decryption_error(mock_session_maker, mock_secrets_service):
    mock_session = AsyncMock()
    mock_session_context = AsyncMock()
    mock_session_context.__aenter__.return_value = mock_session
    mock_session_maker.return_value = mock_session_context

    mock_secrets_service.get_secret = AsyncMock(side_effect=Exception("Invalid Fernet token"))

    ref = parse_secret_ref("db://corrupt-slug")
    resolver = DbEncryptedSecretResolver()
    with pytest.raises(SecretDecryptionError):
        await resolver.resolve(ref)


@pytest.mark.asyncio
@patch("moonmind.auth.resolvers.exec_resolver.asyncio.create_subprocess_exec")
async def test_exec_resolver_success(mock_create_subprocess_exec):
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"exec-secret\n", b"")
    mock_proc.returncode = 0
    mock_create_subprocess_exec.return_value = mock_proc

    ref = parse_secret_ref("exec://op?read?op://vault/item/field")
    resolver = ExecSecretResolver()
    assert await resolver.resolve(ref) == "exec-secret"
    mock_create_subprocess_exec.assert_called_once_with(
        "op", "read", "op://vault/item/field", 
        stdout=asyncio.subprocess.PIPE, 
        stderr=asyncio.subprocess.PIPE
    )


@pytest.mark.asyncio
async def test_exec_resolver_not_allowed():
    ref = parse_secret_ref("exec://rm?-rf?/")
    resolver = ExecSecretResolver()
    with pytest.raises(SecretAccessDeniedError) as exc:
        await resolver.resolve(ref)
    assert "not in the exec allowlist" in str(exc.value)


@pytest.mark.asyncio
@patch("moonmind.auth.resolvers.exec_resolver.asyncio.create_subprocess_exec")
async def test_exec_resolver_not_found(mock_create_subprocess_exec):
    mock_create_subprocess_exec.side_effect = FileNotFoundError()
    
    ref = parse_secret_ref("exec://op?test")
    resolver = ExecSecretResolver()
    with pytest.raises(SecretMissingError) as exc:
        await resolver.resolve(ref)
    assert "Executable not found" in str(exc.value)


@pytest.mark.asyncio
@patch("moonmind.auth.resolvers.exec_resolver.asyncio.create_subprocess_exec")
async def test_exec_resolver_failure(mock_create_subprocess_exec):
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"Some error\n")
    mock_proc.returncode = 1
    mock_create_subprocess_exec.return_value = mock_proc

    ref = parse_secret_ref("exec://aws?--version")
    resolver = ExecSecretResolver()
    with pytest.raises(SecretMissingError) as exc:
        await resolver.resolve(ref)
    assert "failed with exit code 1" in str(exc.value)


@pytest.mark.asyncio
async def test_root_resolver_routing():
    mock_env_resolver = AsyncMock()
    mock_env_resolver.resolve.return_value = "env-val"
    
    mock_db_resolver = AsyncMock()
    mock_db_resolver.resolve.return_value = "db-val"
    
    resolvers = {
        parse_secret_ref("env://X").backend: mock_env_resolver,
        parse_secret_ref("db://y-slug").backend: mock_db_resolver,
    }
    
    root_resolver = RootSecretResolver(resolvers)
    
    env_ref = parse_secret_ref("env://MY_VAR")
    assert await root_resolver.resolve(env_ref) == "env-val"
    mock_env_resolver.resolve.assert_called_once_with(env_ref)
    
    db_ref = parse_secret_ref("db://my-slug")
    assert await root_resolver.resolve(db_ref) == "db-val"
    mock_db_resolver.resolve.assert_called_once_with(db_ref)
    
    # Unconfigured backend
    exec_ref = parse_secret_ref("exec://op")
    with pytest.raises(SecretUnsupportedBackendError):
        await root_resolver.resolve(exec_ref)
