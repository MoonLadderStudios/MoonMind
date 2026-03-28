# Data Model: Provider Profiles Phase 5

## Entities

### `OAuthSession` (Updates)
- **`terminal_session_id`**: String ID mapping to the live terminal PTY.
- **`terminal_bridge_id`**: String mapping to the bridge container.
- **`connected_at`**: Datetime timestamp tracking initial hookup.
- **`disconnected_at`**: Datetime timestamp tracking socket drop.
- **`oauth_web_url`**: DELETED
- **`oauth_ssh_url`**: DELETED

### `TerminalPTYBridge`
- Concept representing the transient container handling SSH -> WebSocket forwarding to the client.
