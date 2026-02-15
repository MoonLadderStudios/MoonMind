(() => {
  const configNode = document.getElementById("task-dashboard-config");
  const root = document.getElementById("dashboard-content");
  if (!configNode || !root) {
    return;
  }

  const config = JSON.parse(configNode.textContent || "{}");
  const pollIntervals = config.pollIntervalsMs || {
    list: 5000,
    detail: 2000,
    events: 1000,
  };

  const pollers = [];

  function stopPolling() {
    while (pollers.length > 0) {
      clearInterval(pollers.pop());
    }
  }

  function startPolling(task, intervalMs) {
    const run = () => {
      if (document.visibilityState === "hidden") {
        return;
      }
      task().catch((error) => {
        console.error("polling task failed", error);
      });
    };

    run();
    const timer = window.setInterval(run, intervalMs);
    pollers.push(timer);
  }

  function activateNav(pathname) {
    const links = document.querySelectorAll("a[data-nav]");
    links.forEach((link) => {
      const href = link.getAttribute("href") || "";
      if (href === pathname) {
        link.classList.add("active");
      } else {
        link.classList.remove("active");
      }
    });
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function pick(obj, ...keys) {
    for (const key of keys) {
      if (obj && Object.prototype.hasOwnProperty.call(obj, key)) {
        return obj[key];
      }
    }
    return undefined;
  }

  function formatTimestamp(value) {
    if (!value) {
      return "-";
    }

    try {
      return new Date(value).toLocaleString();
    } catch (_error) {
      return String(value);
    }
  }

  function normalizeStatus(source, rawStatus) {
    const sourceMap = (config.statusMaps || {})[source] || {};
    const statusKey = String(rawStatus || "").toLowerCase();
    if (statusKey in sourceMap) {
      return sourceMap[statusKey];
    }

    if (statusKey.includes("running")) {
      return "running";
    }
    if (["failed", "error", "failure"].includes(statusKey)) {
      return "failed";
    }
    if (["success", "succeeded", "completed"].includes(statusKey)) {
      return "succeeded";
    }
    return "queued";
  }

  function statusBadge(source, rawStatus) {
    const normalized = normalizeStatus(source, rawStatus);
    return `<span class="status status-${normalized}">${escapeHtml(normalized)}</span>`;
  }

  function endpoint(template, replacements) {
    let resolved = template;
    Object.entries(replacements).forEach(([key, value]) => {
      resolved = resolved.replace(`{${key}}`, encodeURIComponent(String(value)));
    });
    return resolved;
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, {
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    });

    let payload = null;
    const text = await response.text();
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (_error) {
        payload = { message: text };
      }
    }

    if (!response.ok) {
      const detail = payload?.detail;
      const message =
        (typeof detail === "string" && detail) ||
        detail?.message ||
        payload?.message ||
        `${response.status} ${response.statusText}`;
      throw new Error(message);
    }

    return payload;
  }

  function setView(title, subtitle, body) {
    root.innerHTML = `
      <div class="toolbar">
        <div>
          <h2 class="page-title">${escapeHtml(title)}</h2>
          <p class="page-meta">${escapeHtml(subtitle)}</p>
        </div>
      </div>
      ${body}
    `;
  }

  function renderRowsTable(rows) {
    if (rows.length === 0) {
      return "<p class='small'>No rows available.</p>";
    }

    const body = rows
      .map((row) => {
        return `
        <tr>
          <td>${escapeHtml(row.sourceLabel)}</td>
          <td><a href="${escapeHtml(row.link)}">${escapeHtml(row.id)}</a></td>
          <td>${statusBadge(row.source, row.rawStatus)} <span class="small">${escapeHtml(
            row.rawStatus,
          )}</span></td>
          <td>${escapeHtml(row.title)}</td>
          <td>${formatTimestamp(row.createdAt)}</td>
          <td>${formatTimestamp(row.startedAt)}</td>
          <td>${formatTimestamp(row.finishedAt)}</td>
        </tr>
      `;
      })
      .join("");

    return `
      <table>
        <thead>
          <tr>
            <th>Source</th>
            <th>ID</th>
            <th>Status</th>
            <th>Title</th>
            <th>Created</th>
            <th>Started</th>
            <th>Finished</th>
          </tr>
        </thead>
        <tbody>${body}</tbody>
      </table>
    `;
  }

  function toQueueRows(items) {
    return items.map((item) => ({
      source: "queue",
      sourceLabel: "Queue",
      id: pick(item, "id") || "",
      rawStatus: pick(item, "status") || "queued",
      title:
        pick(item, "type") || pick(item, "payload")?.instruction || "Queue Job",
      createdAt: pick(item, "createdAt"),
      startedAt: pick(item, "startedAt"),
      finishedAt: pick(item, "finishedAt"),
      link: `/tasks/queue/${encodeURIComponent(String(pick(item, "id") || ""))}`,
    }));
  }

  function toSpeckitRows(items) {
    return items.map((item) => ({
      source: "speckit",
      sourceLabel: "SpecKit",
      id: pick(item, "id") || "",
      rawStatus: pick(item, "status") || "pending",
      title:
        pick(item, "featureKey") || pick(item, "repository") || "SpecKit Run",
      createdAt: pick(item, "createdAt"),
      startedAt: pick(item, "startedAt"),
      finishedAt: pick(item, "finishedAt"),
      link: `/tasks/speckit/${encodeURIComponent(String(pick(item, "id") || ""))}`,
    }));
  }

  function toOrchestratorRows(runs) {
    return runs.map((run) => ({
      source: "orchestrator",
      sourceLabel: "Orchestrator",
      id: pick(run, "runId") || "",
      rawStatus: pick(run, "status") || "pending",
      title:
        pick(run, "targetService") ||
        pick(run, "instruction") ||
        "Orchestrator Run",
      createdAt: pick(run, "queuedAt"),
      startedAt: pick(run, "startedAt"),
      finishedAt: pick(run, "completedAt"),
      link: `/tasks/orchestrator/${encodeURIComponent(
        String(pick(run, "runId") || ""),
      )}`,
    }));
  }

  function sortRows(rows) {
    return rows.sort((left, right) => {
      const leftTime = Date.parse(left.startedAt || left.createdAt || 0) || 0;
      const rightTime = Date.parse(right.startedAt || right.createdAt || 0) || 0;
      return rightTime - leftTime;
    });
  }

  async function renderActivePage() {
    setView(
      "Active Tasks",
      "Running and queued work across queue, SpecKit, and orchestrator systems.",
      "<p class='loading'>Loading active runs...</p>",
    );

    const loader = async () => {
      const errors = [];
      const rows = [];

      const requests = [
        {
          source: "queue-running",
          call: () => fetchJson("/api/queue/jobs?status=running&limit=200"),
          transform: (payload) => toQueueRows(payload?.items || []),
        },
        {
          source: "queue-queued",
          call: () => fetchJson("/api/queue/jobs?status=queued&limit=200"),
          transform: (payload) => toQueueRows(payload?.items || []),
        },
        {
          source: "speckit-running",
          call: () =>
            fetchJson("/api/workflows/speckit/runs?status=running&limit=100"),
          transform: (payload) => toSpeckitRows(payload?.items || []),
        },
        {
          source: "speckit-pending",
          call: () =>
            fetchJson("/api/workflows/speckit/runs?status=pending&limit=100"),
          transform: (payload) => toSpeckitRows(payload?.items || []),
        },
        {
          source: "speckit-retrying",
          call: () =>
            fetchJson("/api/workflows/speckit/runs?status=retrying&limit=100"),
          transform: (payload) => toSpeckitRows(payload?.items || []),
        },
        {
          source: "orchestrator-running",
          call: () => fetchJson("/orchestrator/runs?status=running&limit=100"),
          transform: (payload) => toOrchestratorRows(payload?.runs || []),
        },
        {
          source: "orchestrator-pending",
          call: () => fetchJson("/orchestrator/runs?status=pending&limit=100"),
          transform: (payload) => toOrchestratorRows(payload?.runs || []),
        },
        {
          source: "orchestrator-awaiting",
          call: () =>
            fetchJson("/orchestrator/runs?status=awaiting_approval&limit=100"),
          transform: (payload) => toOrchestratorRows(payload?.runs || []),
        },
      ];

      const settled = await Promise.allSettled(requests.map((req) => req.call()));
      settled.forEach((result, index) => {
        const request = requests[index];
        if (result.status === "fulfilled") {
          rows.push(...request.transform(result.value));
        } else {
          errors.push(`${request.source}: ${result.reason?.message || "request failed"}`);
        }
      });

      const notices = errors
        .map((error) => `<div class="notice error">${escapeHtml(error)}</div>`)
        .join("");

      root.querySelector(".panel")?.remove();
      setView(
        "Active Tasks",
        "Running and queued work across queue, SpecKit, and orchestrator systems.",
        `${notices}${renderRowsTable(sortRows(rows))}`,
      );
    };

    startPolling(loader, pollIntervals.list);
  }

  async function renderQueueListPage() {
    setView("Queue Jobs", "All queue jobs ordered by creation time.", "<p class='loading'>Loading queue jobs...</p>");

    const load = async () => {
      const payload = await fetchJson("/api/queue/jobs?limit=100");
      const rows = sortRows(toQueueRows(payload?.items || []));
      setView(
        "Queue Jobs",
        "All queue jobs ordered by creation time.",
        `<div class="actions"><a href="/tasks/queue/new"><button type="button">New Queue Job</button></a></div>${renderRowsTable(rows)}`,
      );
    };

    startPolling(load, pollIntervals.list);
  }

  async function renderSpeckitListPage() {
    setView("SpecKit Runs", "Recent SpecKit runs.", "<p class='loading'>Loading SpecKit runs...</p>");

    const load = async () => {
      const payload = await fetchJson("/api/workflows/speckit/runs?limit=100");
      const rows = sortRows(toSpeckitRows(payload?.items || []));
      setView(
        "SpecKit Runs",
        "Recent SpecKit runs.",
        `<div class="actions"><a href="/tasks/speckit/new"><button type="button">New SpecKit Run</button></a></div>${renderRowsTable(rows)}`,
      );
    };

    startPolling(load, pollIntervals.list);
  }

  async function renderOrchestratorListPage() {
    setView(
      "Orchestrator Runs",
      "Recent orchestrator runs.",
      "<p class='loading'>Loading orchestrator runs...</p>",
    );

    const load = async () => {
      const payload = await fetchJson("/orchestrator/runs?limit=100");
      const rows = sortRows(toOrchestratorRows(payload?.runs || []));
      setView(
        "Orchestrator Runs",
        "Recent orchestrator runs.",
        `<div class="actions"><a href="/tasks/orchestrator/new"><button type="button">New Orchestrator Run</button></a></div>${renderRowsTable(rows)}`,
      );
    };

    startPolling(load, pollIntervals.list);
  }

  function renderQueueSubmitPage() {
    setView(
      "Submit Queue Job",
      "Create an Agent Queue job with JSON payload.",
      `
      <form id="queue-submit-form">
        <label>Type
          <input name="type" value="codex_exec" required />
        </label>
        <div class="grid-2">
          <label>Priority
            <input type="number" name="priority" value="0" />
          </label>
          <label>Max Attempts
            <input type="number" min="1" name="maxAttempts" value="3" />
          </label>
        </div>
        <label>Affinity Key
          <input name="affinityKey" placeholder="optional affinity key" />
        </label>
        <label>Payload (JSON)
          <textarea name="payload">{
  "instruction": "Describe the job work here"
}</textarea>
        </label>
        <div class="actions">
          <button type="submit">Create Queue Job</button>
          <a href="/tasks/queue"><button class="secondary" type="button">Cancel</button></a>
        </div>
        <p class="small" id="queue-submit-message"></p>
      </form>
      `,
    );

    const form = document.getElementById("queue-submit-form");
    const message = document.getElementById("queue-submit-message");
    if (!form || !message) {
      return;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      message.className = "small";
      message.textContent = "Submitting...";

      const formData = new FormData(form);
      let payload;
      try {
        payload = JSON.parse(String(formData.get("payload") || "{}"));
      } catch (_error) {
        message.className = "notice error";
        message.textContent = "Payload must be valid JSON.";
        return;
      }

      const requestBody = {
        type: String(formData.get("type") || "").trim(),
        payload,
        priority: Number(formData.get("priority") || 0),
        maxAttempts: Number(formData.get("maxAttempts") || 3),
      };

      const affinityKey = String(formData.get("affinityKey") || "").trim();
      if (affinityKey) {
        requestBody.affinityKey = affinityKey;
      }

      try {
        const created = await fetchJson("/api/queue/jobs", {
          method: "POST",
          body: JSON.stringify(requestBody),
        });
        window.location.href = `/tasks/queue/${encodeURIComponent(created.id)}`;
      } catch (error) {
        message.className = "notice error";
        message.textContent = error.message || "Failed to create queue job.";
      }
    });
  }

  function renderSpeckitSubmitPage() {
    setView(
      "Submit SpecKit Run",
      "Trigger a SpecKit workflow run.",
      `
      <form id="speckit-submit-form">
        <label>Repository (owner/repo)
          <input name="repository" placeholder="owner/repo" required />
        </label>
        <div class="grid-2">
          <label>Feature Key
            <input name="featureKey" placeholder="optional" />
          </label>
          <label>Force Phase
            <select name="forcePhase">
              <option value="">(auto)</option>
              <option value="discover">discover</option>
              <option value="submit">submit</option>
              <option value="apply">apply</option>
              <option value="publish">publish</option>
            </select>
          </label>
        </div>
        <label>Notes
          <textarea name="notes" placeholder="optional notes"></textarea>
        </label>
        <div class="actions">
          <button type="submit">Create SpecKit Run</button>
          <a href="/tasks/speckit"><button class="secondary" type="button">Cancel</button></a>
        </div>
        <p class="small" id="speckit-submit-message"></p>
      </form>
      `,
    );

    const form = document.getElementById("speckit-submit-form");
    const message = document.getElementById("speckit-submit-message");
    if (!form || !message) {
      return;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      message.className = "small";
      message.textContent = "Submitting...";

      const formData = new FormData(form);
      const body = {
        repository: String(formData.get("repository") || "").trim(),
      };
      const featureKey = String(formData.get("featureKey") || "").trim();
      const forcePhase = String(formData.get("forcePhase") || "").trim();
      const notes = String(formData.get("notes") || "").trim();
      if (featureKey) {
        body.featureKey = featureKey;
      }
      if (forcePhase) {
        body.forcePhase = forcePhase;
      }
      if (notes) {
        body.notes = notes;
      }

      try {
        const created = await fetchJson("/api/workflows/speckit/runs", {
          method: "POST",
          body: JSON.stringify(body),
        });
        window.location.href = `/tasks/speckit/${encodeURIComponent(created.id)}`;
      } catch (error) {
        message.className = "notice error";
        message.textContent = error.message || "Failed to create SpecKit run.";
      }
    });
  }

  function renderOrchestratorSubmitPage() {
    setView(
      "Submit Orchestrator Run",
      "Queue an orchestrator action plan.",
      `
      <form id="orchestrator-submit-form">
        <label>Instruction
          <textarea name="instruction" required placeholder="Describe what should be changed and verified."></textarea>
        </label>
        <label>Target Service
          <input name="targetService" required placeholder="api" />
        </label>
        <div class="grid-2">
          <label>Priority
            <select name="priority">
              <option value="normal">normal</option>
              <option value="high">high</option>
            </select>
          </label>
          <label>Approval Token
            <input name="approvalToken" placeholder="optional" />
          </label>
        </div>
        <div class="actions">
          <button type="submit">Create Orchestrator Run</button>
          <a href="/tasks/orchestrator"><button class="secondary" type="button">Cancel</button></a>
        </div>
        <p class="small" id="orchestrator-submit-message"></p>
      </form>
      `,
    );

    const form = document.getElementById("orchestrator-submit-form");
    const message = document.getElementById("orchestrator-submit-message");
    if (!form || !message) {
      return;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      message.className = "small";
      message.textContent = "Submitting...";

      const formData = new FormData(form);
      const body = {
        instruction: String(formData.get("instruction") || "").trim(),
        targetService: String(formData.get("targetService") || "").trim(),
        priority: String(formData.get("priority") || "normal").trim() || "normal",
      };
      const token = String(formData.get("approvalToken") || "").trim();
      if (token) {
        body.approvalToken = token;
      }

      try {
        const created = await fetchJson("/orchestrator/runs", {
          method: "POST",
          body: JSON.stringify(body),
        });
        window.location.href = `/tasks/orchestrator/${encodeURIComponent(
          created.runId,
        )}`;
      } catch (error) {
        message.className = "notice error";
        message.textContent = error.message || "Failed to create orchestrator run.";
      }
    });
  }

  async function renderQueueDetailPage(jobId) {
    setView(
      "Queue Job Detail",
      `Job ${jobId}`,
      "<p class='loading'>Loading queue detail...</p>",
    );

    const state = {
      events: [],
      eventIds: new Set(),
      after: null,
    };

    const render = (job, artifacts, events, loadError) => {
      const notices = loadError
        ? `<div class="notice error">${escapeHtml(loadError)}</div>`
        : "";
      const eventRows = events
        .map(
          (event) => `
            <tr>
              <td>${formatTimestamp(pick(event, "createdAt"))}</td>
              <td>${escapeHtml(pick(event, "level") || "info")}</td>
              <td>${escapeHtml(pick(event, "message") || "")}</td>
            </tr>
          `,
        )
        .join("");

      const artifactRows = artifacts
        .map((artifact) => {
          const downloadUrl = endpoint(
            "/api/queue/jobs/{id}/artifacts/{artifactId}/download",
            {
              id: jobId,
              artifactId: pick(artifact, "id"),
            },
          );
          return `
            <tr>
              <td>${escapeHtml(pick(artifact, "name") || "")}</td>
              <td>${escapeHtml(String(pick(artifact, "sizeBytes") || "-"))}</td>
              <td>${escapeHtml(pick(artifact, "contentType") || "-")}</td>
              <td><a href="${escapeHtml(downloadUrl)}">Download</a></td>
            </tr>
          `;
        })
        .join("");

      const detail = job
        ? `
            <div class="grid-2">
              <div class="card"><strong>Status:</strong> ${statusBadge("queue", pick(
                job,
                "status",
              ))}</div>
              <div class="card"><strong>Type:</strong> ${escapeHtml(
                pick(job, "type") || "",
              )}</div>
              <div class="card"><strong>Created:</strong> ${formatTimestamp(
                pick(job, "createdAt"),
              )}</div>
              <div class="card"><strong>Started:</strong> ${formatTimestamp(
                pick(job, "startedAt"),
              )}</div>
            </div>
          `
        : "<div class='notice error'>Queue job not found.</div>";

      setView(
        "Queue Job Detail",
        `Job ${jobId}`,
        `
          ${notices}
          ${detail}
          <div class="stack">
            <section>
              <h3>Events</h3>
              <table>
                <thead><tr><th>Time</th><th>Level</th><th>Message</th></tr></thead>
                <tbody>${eventRows || "<tr><td colspan='3' class='small'>No events.</td></tr>"}</tbody>
              </table>
            </section>
            <section>
              <h3>Artifacts</h3>
              <table>
                <thead><tr><th>Name</th><th>Size</th><th>Content Type</th><th>Action</th></tr></thead>
                <tbody>${artifactRows || "<tr><td colspan='4' class='small'>No artifacts.</td></tr>"}</tbody>
              </table>
            </section>
          </div>
        `,
      );
    };

    const loadDetail = async () => {
      try {
        const [job, artifactsPayload] = await Promise.all([
          fetchJson(endpoint("/api/queue/jobs/{id}", { id: jobId })),
          fetchJson(endpoint("/api/queue/jobs/{id}/artifacts", { id: jobId })),
        ]);
        render(job, artifactsPayload?.items || [], state.events, null);
      } catch (error) {
        render(null, [], state.events, error.message || "Failed to load queue detail.");
      }
    };

    const loadEvents = async () => {
      const query = state.after
        ? `?after=${encodeURIComponent(state.after)}&limit=200`
        : "?limit=200";
      try {
        const payload = await fetchJson(
          endpoint("/api/queue/jobs/{id}/events", { id: jobId }) + query,
        );
        const incoming = (payload?.items || []).slice().sort((a, b) => {
          return Date.parse(pick(a, "createdAt") || 0) - Date.parse(pick(b, "createdAt") || 0);
        });
        incoming.forEach((event) => {
          const eventId = String(pick(event, "id") || "");
          if (!eventId || state.eventIds.has(eventId)) {
            return;
          }
          state.eventIds.add(eventId);
          state.events.push(event);
          state.after = pick(event, "createdAt") || state.after;
        });

        state.events = state.events.slice(-500);
      } catch (error) {
        console.error("queue event poll failed", error);
      }
    };

    await loadDetail();
    await loadEvents();
    startPolling(loadDetail, pollIntervals.detail);
    startPolling(loadEvents, pollIntervals.events);
  }

  function renderArtifactsRows(artifacts, showDownload = false, runId = "") {
    return artifacts
      .map((artifact) => {
        const name = pick(artifact, "name") || pick(artifact, "path") || "artifact";
        const size = pick(artifact, "sizeBytes") || "-";
        const type = pick(artifact, "contentType") || pick(artifact, "type") || "-";
        let action = "-";
        if (showDownload && pick(artifact, "id")) {
          action = `<a href="${escapeHtml(
            endpoint("/api/queue/jobs/{id}/artifacts/{artifactId}/download", {
              id: runId,
              artifactId: pick(artifact, "id"),
            }),
          )}">Download</a>`;
        } else if (pick(artifact, "path")) {
          action = `<span class="inline-code">${escapeHtml(pick(artifact, "path"))}</span>`;
        }

        return `
          <tr>
            <td>${escapeHtml(name)}</td>
            <td>${escapeHtml(String(size))}</td>
            <td>${escapeHtml(String(type))}</td>
            <td>${action}</td>
          </tr>
        `;
      })
      .join("");
  }

  async function renderSpeckitDetailPage(runId) {
    setView(
      "SpecKit Run Detail",
      `Run ${runId}`,
      "<p class='loading'>Loading SpecKit run...</p>",
    );

    const load = async () => {
      try {
        const [run, tasksPayload, artifactsPayload] = await Promise.all([
          fetchJson(endpoint("/api/workflows/speckit/runs/{id}", { id: runId })),
          fetchJson(endpoint("/api/workflows/speckit/runs/{id}/tasks", { id: runId })),
          fetchJson(endpoint("/api/workflows/speckit/runs/{id}/artifacts", { id: runId })),
        ]);

        const tasks = tasksPayload?.tasks || [];
        const artifacts = artifactsPayload?.artifacts || [];
        const taskRows = tasks
          .map(
            (task) => `
              <tr>
                <td>${escapeHtml(pick(task, "taskName") || "")}</td>
                <td>${statusBadge("speckit", pick(task, "status"))}</td>
                <td>${escapeHtml(String(pick(task, "attempt") || "-"))}</td>
                <td>${formatTimestamp(pick(task, "startedAt"))}</td>
                <td>${formatTimestamp(pick(task, "finishedAt"))}</td>
              </tr>
            `,
          )
          .join("");

        setView(
          "SpecKit Run Detail",
          `Run ${runId}`,
          `
            <div class="grid-2">
              <div class="card"><strong>Status:</strong> ${statusBadge("speckit", pick(
                run,
                "status",
              ))}</div>
              <div class="card"><strong>Repository:</strong> ${escapeHtml(
                pick(run, "repository") || "-",
              )}</div>
              <div class="card"><strong>Feature Key:</strong> ${escapeHtml(
                pick(run, "featureKey") || "-",
              )}</div>
              <div class="card"><strong>Started:</strong> ${formatTimestamp(
                pick(run, "startedAt"),
              )}</div>
            </div>
            <div class="stack">
              <section>
                <h3>Tasks</h3>
                <table>
                  <thead><tr><th>Task</th><th>Status</th><th>Attempt</th><th>Started</th><th>Finished</th></tr></thead>
                  <tbody>${taskRows || "<tr><td colspan='5' class='small'>No task rows.</td></tr>"}</tbody>
                </table>
              </section>
              <section>
                <h3>Artifacts</h3>
                <table>
                  <thead><tr><th>Name/Path</th><th>Size</th><th>Type</th><th>Reference</th></tr></thead>
                  <tbody>${renderArtifactsRows(artifacts) || "<tr><td colspan='4' class='small'>No artifacts.</td></tr>"}</tbody>
                </table>
              </section>
            </div>
          `,
        );
      } catch (error) {
        setView(
          "SpecKit Run Detail",
          `Run ${runId}`,
          `<div class="notice error">${escapeHtml(
            error.message || "Failed to load run detail.",
          )}</div>`,
        );
      }
    };

    startPolling(load, pollIntervals.detail);
  }

  async function renderOrchestratorDetailPage(runId) {
    setView(
      "Orchestrator Run Detail",
      `Run ${runId}`,
      "<p class='loading'>Loading orchestrator run...</p>",
    );

    const load = async () => {
      try {
        const [run, artifactsPayload] = await Promise.all([
          fetchJson(endpoint("/orchestrator/runs/{id}", { id: runId })),
          fetchJson(endpoint("/orchestrator/runs/{id}/artifacts", { id: runId })),
        ]);

        const steps = pick(run, "steps") || [];
        const stepRows = steps
          .map(
            (step) => `
              <tr>
                <td>${escapeHtml(pick(step, "name") || "")}</td>
                <td>${escapeHtml(pick(step, "status") || pick(step, "celeryState") || "-")}</td>
                <td>${formatTimestamp(pick(step, "startedAt"))}</td>
                <td>${formatTimestamp(pick(step, "completedAt"))}</td>
              </tr>
            `,
          )
          .join("");

        setView(
          "Orchestrator Run Detail",
          `Run ${runId}`,
          `
            <div class="grid-2">
              <div class="card"><strong>Status:</strong> ${statusBadge(
                "orchestrator",
                pick(run, "status"),
              )}</div>
              <div class="card"><strong>Service:</strong> ${escapeHtml(
                pick(run, "targetService") || "-",
              )}</div>
              <div class="card"><strong>Priority:</strong> ${escapeHtml(
                pick(run, "priority") || "-",
              )}</div>
              <div class="card"><strong>Started:</strong> ${formatTimestamp(
                pick(run, "startedAt"),
              )}</div>
            </div>
            <div class="stack">
              <section>
                <h3>Plan Steps</h3>
                <table>
                  <thead><tr><th>Step</th><th>Status</th><th>Started</th><th>Completed</th></tr></thead>
                  <tbody>${stepRows || "<tr><td colspan='4' class='small'>No steps yet.</td></tr>"}</tbody>
                </table>
              </section>
              <section>
                <h3>Artifacts</h3>
                <table>
                  <thead><tr><th>Name/Path</th><th>Size</th><th>Type</th><th>Reference</th></tr></thead>
                  <tbody>${
                    renderArtifactsRows(artifactsPayload?.artifacts || []) ||
                    "<tr><td colspan='4' class='small'>No artifacts.</td></tr>"
                  }</tbody>
                </table>
              </section>
            </div>
          `,
        );
      } catch (error) {
        setView(
          "Orchestrator Run Detail",
          `Run ${runId}`,
          `<div class="notice error">${escapeHtml(
            error.message || "Failed to load run detail.",
          )}</div>`,
        );
      }
    };

    startPolling(load, pollIntervals.detail);
  }

  function renderNotFound() {
    setView(
      "Route Not Found",
      "The requested dashboard route does not exist.",
      "<div class='notice error'>Unknown dashboard route.</div>",
    );
  }

  async function renderForPath(pathname) {
    stopPolling();
    activateNav(pathname);

    const queueDetailMatch = pathname.match(/^\/tasks\/queue\/([^/]+)$/);
    const speckitDetailMatch = pathname.match(/^\/tasks\/speckit\/([^/]+)$/);
    const orchestratorDetailMatch = pathname.match(
      /^\/tasks\/orchestrator\/([^/]+)$/,
    );

    if (pathname === "/tasks") {
      await renderActivePage();
      return;
    }
    if (pathname === "/tasks/queue") {
      await renderQueueListPage();
      return;
    }
    if (pathname === "/tasks/speckit") {
      await renderSpeckitListPage();
      return;
    }
    if (pathname === "/tasks/orchestrator") {
      await renderOrchestratorListPage();
      return;
    }

    if (pathname === "/tasks/queue/new") {
      renderQueueSubmitPage();
      return;
    }
    if (pathname === "/tasks/speckit/new") {
      renderSpeckitSubmitPage();
      return;
    }
    if (pathname === "/tasks/orchestrator/new") {
      renderOrchestratorSubmitPage();
      return;
    }

    if (queueDetailMatch) {
      await renderQueueDetailPage(queueDetailMatch[1]);
      return;
    }
    if (speckitDetailMatch) {
      await renderSpeckitDetailPage(speckitDetailMatch[1]);
      return;
    }
    if (orchestratorDetailMatch) {
      await renderOrchestratorDetailPage(orchestratorDetailMatch[1]);
      return;
    }

    renderNotFound();
  }

  window.addEventListener("beforeunload", stopPolling);
  renderForPath(window.location.pathname).catch((error) => {
    console.error("dashboard render failed", error);
    setView(
      "Dashboard Error",
      "Unexpected rendering failure.",
      `<div class="notice error">${escapeHtml(error.message || "Unknown error")}</div>`,
    );
  });
})();
