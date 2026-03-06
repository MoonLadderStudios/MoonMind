# Contract: MoonMind Dashboard Theme Tokens

| Token | Description | Light RGB | Usage |
|-------|-------------|-----------|-------|
| `--mm-bg` | Global background wash | `248 247 255` | `<body>` gradient base |
| `--mm-panel` | Card/panel surface | `255 255 255` | `.masthead`, `.panel`, `.card` backgrounds |
| `--mm-ink` | Primary text color | `18 20 32` | Body copy, nav text |
| `--mm-muted` | Secondary text | `95 102 122` | Subheaders, metadata |
| `--mm-border` | Panel border color | `214 220 235` | Panel/card borders |
| `--mm-accent` | Primary purple accent | `139 92 246` | Buttons, nav active states, awaiting action status |
| `--mm-accent-2` | Cyan accent | `34 211 238` | Running status, highlights |
| `--mm-accent-warm` | Pink accent | `244 114 182` | Gradient sheen, emphasis |
| `--mm-ok` | Success status | `34 197 94` | `.status-succeeded` |
| `--mm-warn` | Warning/queued | `245 158 11` | `.status-queued` |
| `--mm-danger` | Failure/cancelled | `244 63 94` | `.status-failed`, `.status-cancelled` |
| `--mm-shadow` | Shared shadow stack | `0 18px 32px -26px rgb(10 8 30 / 0.55)` | `.masthead`, `.panel`, `.btn` |

Status chips consume the same tokens but at 14% fill and 35% border alpha. Future dark mode will override these tokens via `.dark` scope.
