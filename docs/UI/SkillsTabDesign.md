# Mission Control Skills Tab Design Document

Status: Proposed
Owners: MoonMind Engineering
Last Updated: 2026-03-18

## 1. Purpose
Define the design and implementation approach for the new "Skills" tab in the MoonMind Mission Control UI. This feature allows users to view existing skills and create new ones directly from the dashboard.

## 2. Goals
- Provide a dedicated tab in Mission Control for managing skills.
- Allow users to preview existing skills by rendering their `SKILL.md` content.
- Provide a form to create a new skill by pasting Markdown content.
- Expose a new API endpoint to handle skill creation.
- Ensure newly created skills are added to the local skills directory and immediately appear in the skill selection dropdown.

## 3. User Interface Design

### 3.1 Navigation
- A new top-level navigation pill "Skills" will be added to the `.route-nav` list, pointing to `/skills`.

### 3.2 Main Layout
- The `/skills` route will display a split-pane layout or a list-detail view.
- **Left Pane (List):** A list of available skills fetched from the server.
- **Right Pane (Detail/Editor):**
  - **View Mode:** When an existing skill is selected, this pane renders the `SKILL.md` content as HTML using the existing Markdown rendering components.
  - **Create Mode:** A button "Create New Skill" switches this pane to a form. The form contains an input for the skill name and a large text area for the Markdown content. A "Save Skill" button submits the form.

### 3.3 Visual Style
- The design will strictly follow `docs/UI/MissionControlStyleGuide.md`.
- **Buttons:** Use the defined `queue-submit-primary` (green) for "Save Skill" and default action styles for "Create New Skill".
- **Glass Effects:** The skill list and detail panes will use `mm-glass` or `mm-glass-strong` container classes.
- **Typography:** The skill content preview will utilize existing `markdown-body` styles if available, or fall back to standard prose styling with high contrast for dark mode.

## 4. API & Backend Implementation

### 4.1 New API Endpoint
- **Route:** `POST /api/skills`
- **Payload:**
  ```json
  {
    "name": "MyNewSkill",
    "markdown": "# My New Skill\n\nThis is the skill content..."
  }
  ```
- **Response:**
  - `201 Created` on success.
  - `400 Bad Request` if validation fails (e.g., name conflict or invalid name format).

### 4.2 Local Storage Handling
- The backend handler for `POST /api/skills` will write the provided markdown to `.agents/skills/local/{name}/SKILL.md`.
- It will create the parent directory `.agents/skills/local/{name}` if it does not exist.
- This path aligns with the "Shared Skills Runtime" rules specified in `AGENTS.md`.

## 5. Frontend Implementation

### 5.1 Route Shell Updates (`api_service/api/routers/task_dashboard.py`)
- Add a new route handler for `/skills` that renders `api_service/templates/task_dashboard.html` (the SPA shell handles routing client-side, or a specific template context flag can be set).
- *Alternatively*, handle the `/skills` route entirely client-side within `dashboard.js`.

### 5.2 Client-Side Logic (`api_service/static/task_dashboard/dashboard.js`)
- Add a new view controller/render function for the Skills tab.
- **Fetch Skills:** `GET /api/skills` (assuming a read endpoint exists or needs to be added) to populate the list.
- **Render Preview:** Use a lightweight Markdown-to-HTML converter or rely on the backend to pre-render the content.
- **Submit Form:** Attach a submit handler to the creation form that sends the JSON payload to `POST /api/skills`.
- **Post-Submit Action:** On success, reload the skill list and switch back to "View Mode" selecting the newly created skill. This ensures it's visible and implicitly confirms it will appear in the "skill dropdown" used elsewhere in the app.

## 6. Security Considerations
- The API must validate the `name` field to prevent path traversal attacks (e.g., rejecting names with `../` or absolute paths).
- The Markdown rendering must safely handle user-supplied content, though in this context the user is trusted.

## 7. Implementation Checklist
- [ ] Add `POST /api/skills` backend endpoint.
- [ ] (If needed) Add `GET /api/skills` endpoint to list available skills.
- [ ] Add "Skills" navigation link to the UI header.
- [ ] Implement the Skills List View in the frontend.
- [ ] Implement the Skill Detail View (Markdown Preview).
- [ ] Implement the Create Skill Form.
- [ ] Wire up form submission to the new API endpoint.
- [ ] Verify newly created skills populate the standard skill selection dropdown.
