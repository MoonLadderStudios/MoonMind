# Frontend Development

This document describes the standard developer workflow for working with the MoonMind frontend.

## Prerequisite

Install frontend dependencies once:

```bash
npm install
```

## Demo and review workflow

Use the production-like workflow when you need another person to review a
frontend change, when you are demoing over the network, or when visual
correctness matters more than Hot Module Replacement (HMR).

Build a fresh frontend bundle and let FastAPI serve the built assets:

```bash
npm run ui:build
docker compose up -d api
```

Then open Mission Control through the normal FastAPI route:

```text
http://localhost:8000/tasks/list
```

For network demos, replace `localhost` with the host name or IP address that the
reviewer can reach, for example:

```text
http://asus-laptop:8000/tasks/list
http://192.168.0.20:8000/tasks/list
```

In this mode, FastAPI serves the built `dist/` bundle through the Vite manifest.
This is the preferred review path because the browser receives one coherent
asset set from the same server that owns the Mission Control HTML shell and API
routes.

Do not open the Vite dev server root (`http://localhost:5173/`) for demos or
reviews. MoonMind's Vite server serves frontend modules for development; it does
not own the Mission Control HTML routes and may return `404` at `/`.

## Live development with Hot Module Replacement (HMR)

Assume the normal MoonMind development stack is already running, for example:

```bash
docker compose up -d
```

HMR uses the **normal MoonMind API**. It does **not** use a secondary API.

### Standard local workflow

Start the Vite dev server:

```bash
npm run ui:dev
```

Start or restart FastAPI with the Vite dev-server URL set:

```bash
MOONMIND_UI_DEV_SERVER_URL=http://127.0.0.1:5173 <your-fastapi-start-command>
```

When `MOONMIND_UI_DEV_SERVER_URL` is set, FastAPI bypasses the built manifest and loads the frontend modules directly from the Vite dev server.

### Important notes

* `npm run ui:dev` by itself is **not enough**
* FastAPI must be started with `MOONMIND_UI_DEV_SERVER_URL` set
* if FastAPI was already running without that env var, restart it in this mode
* frontend changes should then update through Vite HMR without restarting FastAPI again

### If FastAPI is running in Docker

If FastAPI is running inside Docker instead of on the host, `127.0.0.1` usually will not work for `MOONMIND_UI_DEV_SERVER_URL` because that points to the container itself.

Use a host-reachable address instead, for example:

```bash
MOONMIND_UI_DEV_SERVER_URL=http://host.docker.internal:5173 <your-fastapi-start-command>
```

## Frontend verification

Run the standard frontend checks:

```bash
npm run ui:test
npm run ui:typecheck
npm run ui:lint
npm run ui:build:check
```

These commands cover:

* `ui:test` — Vitest unit tests
* `ui:typecheck` — TypeScript type checking
* `ui:lint` — ESLint
* `ui:build:check` — clean rebuild plus manifest validation

## Generated API types

Refresh the generated frontend API types with:

```bash
npm run generate
```

This updates:

```text
frontend/src/generated/openapi.ts
```

## Source of truth and generated output

Frontend source files live under:

```text
frontend/
```

Built frontend output is emitted under:

```text
api_service/static/task_dashboard/dist/
```

Do not edit files in `dist/` directly. Treat `dist/` as generated output, not hand-edited source.
