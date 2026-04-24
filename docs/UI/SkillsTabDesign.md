# Mission Control Skills Tab Design Document

Status: Proposed 
Owners: MoonMind Engineering 
Last Updated: 2026-03-24

**Implementation tracking:** Rollout and backlog notes live in MoonSpec artifacts (`specs/<feature>/`), gitignored handoffs (for example `artifacts/`), or other local-only files—not as migration checklists in canonical `docs/`.

## 1. Purpose

Define the desired design for the **Skills** area in Mission Control: navigation, list/detail UX, API usage, and security expectations so operators can view and create `.agents/skills` entries from the dashboard.

## 2. Goals

- Dedicated Mission Control entry point for skills (route `/tasks/skills`).
- List skills and preview `SKILL.md` content (Markdown → HTML).
- Create flow: name + Markdown body persisted under `.agents/skills/local/{name}/SKILL.md`.
- `POST /api/tasks/skills` creates on-disk skill; `GET /api/tasks/skills` lists skills and, when complete, returns enough data for list and detail views.
- New skills participate in the same skill selection surfaces as other dashboard flows once written.

## 3. User Interface Design

### 3.1 Navigation

- Top-level nav pill **Skills** in `.route-nav` → `/tasks/skills`, consistent with `/tasks/list`, `/tasks/new`, etc.

### 3.2 Main Layout

- `/tasks/skills` uses list–detail: left list from server, right pane for preview or create form.
- **View mode:** selected skill renders `SKILL.md` as HTML (shared Markdown component).
- **Create mode:** “Create New Skill” reveals form (name, Markdown body, save action).

### 3.3 Visual Style

- Follow [`MissionControlStyleGuide.md`](MissionControlStyleGuide.md): `queue-submit-primary` for primary save, `mm-glass` / `mm-glass-strong` containers, `markdown-body` or equivalent prose for preview (dark-mode safe).

## 4. API & backend

### 4.1 Endpoints

- **`POST /api/tasks/skills`** — body `{ "name", "markdown" }`; `201` on success; `400` on validation (name conflict, invalid name).
- **`GET /api/tasks/skills`** — list skills; extended to return file content for detail view when implementation is complete (see tracker).

### 4.2 Storage

- Handler writes `.agents/skills/local/{name}/SKILL.md`, creating directories as needed (see `AGENTS.md` shared skills runtime).

## 5. Frontend

- Route `/tasks/skills` in dashboard shell (`task_dashboard` template + client routing).
- Client: fetch list, render preview, submit create form to `POST /api/tasks/skills`, refresh list and select new skill on success.

## 6. Security

- Reject path traversal in `name` (`../`, absolute paths).
- Render Markdown with the same safety posture as other user-editable dashboard content.
