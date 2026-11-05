"""Read Codex readiness/defaults and record proven enrollment expiry."""

from moonmind.omnigent.oauth_login import record_ended_oauth_login
from moonmind.workflows.provider_failures import provider_auth_failure_markers

# Run as ``python3 -c <probe> <codex home>``. Print only a verdict, never
# token material. An already-expired token minted without a lifetime proves
# the login ended; an ordinarily aged token may still refresh when Codex runs.
# API-key auth and unreadable or unexpected layouts remain unknown.
CODEX_LOGIN_SESSION_PROBE = """\
import base64, json, sys, time
try:
    with open(sys.argv[1] + "/auth.json", encoding="utf-8") as handle:
        token = json.load(handle)["tokens"]["access_token"]
    claims = token.split(".")[1]
    claims += "=" * (-len(claims) % 4)
    claims = json.loads(base64.urlsafe_b64decode(claims))
    expires_at = float(claims["exp"])
    ended = expires_at <= float(claims["iat"]) and expires_at <= time.time()
    print("ended" if ended else "active")
except Exception:
    print("unknown")
"""


# Run on the admitted host, under its credential lease and restricted egress.
# account/read is the TUI bootstrap check: login status and model/list can both
# succeed from local state while workspace routing rejects the same login.
# Use the public CLI protocol, send no turn, and print no account/error bodies.
# Serialize the shared auth markers: refresh rejection may carry no HTTP status.
_CODEX_APP_SERVER_READ_PROBE = (
    f"AUTH_FAILURE_MARKERS = {provider_auth_failure_markers()!r}\n"
    + """\
import asyncio, importlib, importlib.util, json, os, re, signal, sys

async def main():
    process = None
    verdict = "unavailable"
    try:
        command = ["codex"]
        if READ_CONFIGURATION:
            os.environ["CODEX_HOME"] = sys.argv[1]
            # Resolve the same public native launch shape as the runner. Its
            # explicit provider default can be hidden from the visible picker.
            try:
                native = importlib.import_module(
                    "omnigent.harnesses.codex_native.app_server"
                    if importlib.util.find_spec("omnigent.harnesses") is not None
                    else "omnigent.codex_native_app_server"
                )
                launch = native.resolve_native_codex_launch(model=None)
            except (ImportError, AttributeError, TypeError):
                print(json.dumps({"status": "unsupported"}))
                return
            if launch.profile is not None:
                print(json.dumps({"status": "unsupported"}))
                return
            if isinstance(launch.model, str) and launch.model.strip():
                print(json.dumps({"status": "ready", "model": launch.model}))
                return
            for override in launch.config_overrides:
                command.extend(["-c", override])
        command.append("app-server")
        process = await asyncio.create_subprocess_exec(
            *command,
            env={**os.environ, "CODEX_HOME": sys.argv[1]},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            limit=1024 * 1024 if READ_CONFIGURATION else 64 * 1024,
        )

        async def request(request_id, method, params):
            process.stdin.write((json.dumps({
                "id": request_id, "method": method, "params": params,
            }) + "\\n").encode())
            await process.stdin.drain()
            while True:
                line = await process.stdout.readline()
                if not line:
                    raise RuntimeError("account probe stream ended")
                message = json.loads(line)
                if isinstance(message, dict) and message.get("id") == request_id:
                    return message

        async def observe():
            initialized = await request(1, "initialize", {
                "clientInfo": {"name": "moonmind-model-preflight" if READ_CONFIGURATION else "moonmind-login-preflight", "version": "1"},
            })
            if "error" in initialized or not isinstance(initialized.get("result"), dict):
                return "unavailable"
            process.stdin.write(b'{"method":"initialized"}\\n')
            await process.stdin.drain()
            if READ_CONFIGURATION:
                response = await request(2, "config/read", {"includeLayers": False})
                error = response.get("error")
                if isinstance(error, dict) and error.get("code") in {-32600, -32602}:
                    response = await request(3, "config/read", {})
                result = response.get("result")
                config = result.get("config") if isinstance(result, dict) else None
                model = config.get("model") if isinstance(config, dict) else None
                if isinstance(model, str) and model.strip():
                    return {"status": "ready", "model": model}
                return {"status": "unavailable"}
            response = await request(2, "account/read", {"refreshToken": False})

            def unauthorized(error):
                return isinstance(error, dict) and (
                    error.get("code") == 401 or re.search(
                        r"(?<![0-9])401(?![0-9])", str(error.get("message", ""))
                    )
                    or any(marker in str(error.get("message", "")).lower()
                           for marker in AUTH_FAILURE_MARKERS)
                )

            # The access token can be rejected while its refresh token is
            # still usable. Let Codex repair it once in the authoritative,
            # exclusively leased OAuth home before requiring a new sign-in.
            if unauthorized(response.get("error")):
                response = await request(3, "account/read", {"refreshToken": True})
            error = response.get("error")
            if isinstance(error, dict):
                if unauthorized(error):
                    return "unauthorized"
                return "unavailable"
            result = response.get("result")
            if not isinstance(result, dict):
                return "unavailable"
            account = result.get("account")
            if isinstance(account, dict) and account.get("type") == "chatgpt":
                return "ready"
            if account is None and result.get("requiresOpenaiAuth") is True:
                return "missing_login"
            return "unavailable"

        verdict = await asyncio.wait_for(observe(), timeout=20)
    except Exception:
        pass
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
    print(json.dumps(verdict if isinstance(verdict, dict) else {"status": verdict}) if READ_CONFIGURATION else verdict)

asyncio.run(main())
"""
)

CODEX_ACCOUNT_READ_PROBE = "READ_CONFIGURATION = False\n" + _CODEX_APP_SERVER_READ_PROBE
CODEX_CONFIGURATION_READ_PROBE = "READ_CONFIGURATION = True\n" + _CODEX_APP_SERVER_READ_PROBE


async def record_ended_codex_login(
    profile_id: str,
    credential_generation: int,
    *,
    reason: str = "codex_login_session_ended",
) -> None:
    """Disable only the Codex enrollment whose mounted login was proven unusable."""

    await record_ended_oauth_login(
        profile_id, credential_generation, runtime_id="codex_cli", reason=reason
    )
