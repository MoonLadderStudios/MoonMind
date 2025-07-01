# Operations Runbook

MoonMind background jobs run using the default user account.

- **User ID:** `00000000-0000-0000-0000-000000000000`
- **Email:** `default@example.com`

Keys for model providers (e.g. Google and OpenAI) are read from this user's profile whenever jobs execute. In `disabled` auth mode, the values from `.env` seed this profile on startup. Remove them from the environment to verify jobs fail until a key is stored on this user.
