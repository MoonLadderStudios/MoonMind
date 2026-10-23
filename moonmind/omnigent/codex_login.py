"""Portable, read-only observation of a stored Codex login session."""

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
