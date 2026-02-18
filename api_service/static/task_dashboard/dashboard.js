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
  const sourceConfig = config.sources || {};
  const queueSourceConfig =
    sourceConfig.queue && typeof sourceConfig.queue === "object" ? sourceConfig.queue : {};
  const systemConfig = config.system || {};
  const defaultQueueName = String(systemConfig.defaultQueue || "moonmind.jobs");
  const supportedWorkerRuntimes =
    Array.isArray(systemConfig.supportedWorkerRuntimes) &&
    systemConfig.supportedWorkerRuntimes.length > 0
      ? systemConfig.supportedWorkerRuntimes
      : ["codex", "gemini", "claude", "universal"];
  const configuredTaskRuntimes =
    Array.isArray(systemConfig.supportedTaskRuntimes) &&
    systemConfig.supportedTaskRuntimes.length > 0
      ? systemConfig.supportedTaskRuntimes
      : [];
  const inferredTaskRuntimes = supportedWorkerRuntimes.filter(
    (runtime) => runtime !== "universal",
  );
  const supportedTaskRuntimes =
    configuredTaskRuntimes.length > 0
      ? configuredTaskRuntimes
      : inferredTaskRuntimes.length > 0
        ? inferredTaskRuntimes
        : ["codex", "gemini", "claude"];
  const defaultTaskRuntime =
    normalizeTaskRuntimeInput(systemConfig.defaultTaskRuntime) ||
    (supportedTaskRuntimes.includes("codex")
      ? "codex"
      : supportedTaskRuntimes[0] || "codex");
  const configuredModelDefaults =
    systemConfig.defaultTaskModelByRuntime &&
    typeof systemConfig.defaultTaskModelByRuntime === "object" &&
    !Array.isArray(systemConfig.defaultTaskModelByRuntime)
      ? systemConfig.defaultTaskModelByRuntime
      : {};
  const configuredEffortDefaults =
    systemConfig.defaultTaskEffortByRuntime &&
    typeof systemConfig.defaultTaskEffortByRuntime === "object" &&
    !Array.isArray(systemConfig.defaultTaskEffortByRuntime)
      ? systemConfig.defaultTaskEffortByRuntime
      : {};
  function resolveRuntimeDefault(defaultsByRuntime, runtime) {
    const runtimeKey = String(runtime || "").trim().toLowerCase();
    if (!runtimeKey) {
      return "";
    }
    const value = defaultsByRuntime[runtimeKey];
    return value ? String(value).trim() : "";
  }
  const codexDefaultTaskModel =
    resolveRuntimeDefault(configuredModelDefaults, "codex") ||
    String(systemConfig.defaultTaskModel || "").trim();
  const codexDefaultTaskEffort =
    resolveRuntimeDefault(configuredEffortDefaults, "codex") ||
    String(systemConfig.defaultTaskEffort || "").trim();
  const defaultTaskModel = resolveRuntimeDefault(
    { ...configuredModelDefaults, codex: codexDefaultTaskModel },
    defaultTaskRuntime,
  );
  const defaultTaskEffort = resolveRuntimeDefault(
    { ...configuredEffortDefaults, codex: codexDefaultTaskEffort },
    defaultTaskRuntime,
  );
  const defaultRepository = String(systemConfig.defaultRepository || "").trim();
  const ownerRepoPattern = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

  const TASK_LIST_TITLE_MAX_CHARS = 400;
  const pollers = [];
  const disposers = [];
  let cachedAvailableSkillIds = null;

  function stopPolling() {
    while (pollers.length > 0) {
      clearInterval(pollers.pop());
    }
    while (disposers.length > 0) {
      const dispose = disposers.pop();
      if (typeof dispose === "function") {
        try {
          dispose();
        } catch (error) {
          console.error("polling disposer failed", error);
        }
      }
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

  function registerDisposer(disposer) {
    if (typeof disposer === "function") {
      disposers.push(disposer);
    }
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

  function extractRuntimeFromPayload(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return null;
    }

    const directRuntime = pick(payload, "targetRuntime", "target_runtime", "runtime");
    if (directRuntime) {
      return String(directRuntime);
    }

    const task = pick(payload, "task");
    if (task && typeof task === "object" && !Array.isArray(task)) {
      const runtimeNode = pick(task, "runtime");
      if (runtimeNode && typeof runtimeNode === "object" && !Array.isArray(runtimeNode)) {
        const runtimeMode = pick(runtimeNode, "mode");
        if (runtimeMode) {
          return String(runtimeMode);
        }
      }
      const taskRuntime = pick(task, "targetRuntime", "target_runtime", "runtime");
      if (taskRuntime) {
        return String(taskRuntime);
      }
    }

    return null;
  }

  function extractTaskNode(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return null;
    }
    const task = pick(payload, "task");
    if (!task || typeof task !== "object" || Array.isArray(task)) {
      return null;
    }
    return task;
  }

  function extractRuntimeModelFromPayload(payload) {
    const task = extractTaskNode(payload);
    if (!task) {
      return null;
    }
    const runtimeNode = pick(task, "runtime");
    if (!runtimeNode || typeof runtimeNode !== "object" || Array.isArray(runtimeNode)) {
      return null;
    }
    const model = pick(runtimeNode, "model");
    return model ? String(model) : null;
  }

  function extractRuntimeEffortFromPayload(payload) {
    const task = extractTaskNode(payload);
    if (!task) {
      return null;
    }
    const runtimeNode = pick(task, "runtime");
    if (!runtimeNode || typeof runtimeNode !== "object" || Array.isArray(runtimeNode)) {
      return null;
    }
    const effort = pick(runtimeNode, "effort");
    return effort ? String(effort) : null;
  }

  function extractSkillFromPayload(payload) {
    const task = extractTaskNode(payload);
    if (!task) {
      return null;
    }
    const skillNode = pick(task, "skill");
    if (!skillNode || typeof skillNode !== "object" || Array.isArray(skillNode)) {
      return null;
    }
    const skillId = pick(skillNode, "id");
    return skillId ? String(skillId) : null;
  }

  function extractPublishModeFromPayload(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return null;
    }
    const task = extractTaskNode(payload);
    if (task) {
      const publishNode = pick(task, "publish");
      if (publishNode && typeof publishNode === "object" && !Array.isArray(publishNode)) {
        const mode = pick(publishNode, "mode");
        if (mode) {
          return String(mode).toLowerCase();
        }
      }
    }
    const publishNode = pick(payload, "publish");
    if (publishNode && typeof publishNode === "object" && !Array.isArray(publishNode)) {
      const mode = pick(publishNode, "mode");
      if (mode) {
        return String(mode).toLowerCase();
      }
    }
    const legacyMode = pick(payload, "publishMode");
    return legacyMode ? String(legacyMode).toLowerCase() : null;
  }

  function renderRuntime(runtime) {
    return runtime ? escapeHtml(runtime) : "-";
  }

  function deriveStageFromEvent(event) {
    const payload = pick(event, "payload");
    const payloadStage =
      payload && typeof payload === "object" && !Array.isArray(payload)
        ? String(pick(payload, "stage") || "").trim()
        : "";
    const message = String(pick(event, "message") || "").trim();
    const candidate = payloadStage || message;
    if (candidate.startsWith("moonmind.task.prepare")) {
      return "prepare";
    }
    if (candidate.startsWith("moonmind.task.execute")) {
      return "execute";
    }
    if (candidate.startsWith("moonmind.task.publish")) {
      return "publish";
    }
    if (candidate.startsWith("task.git.")) {
      return "git";
    }
    return "-";
  }

  function deriveStageFromArtifactName(name) {
    const candidate = String(name || "");
    if (candidate.includes("prepare.log")) {
      return "prepare";
    }
    if (candidate.includes("execute.log") || candidate.includes("codex_exec.log")) {
      return "execute";
    }
    if (candidate.includes("publish.log") || candidate.includes("publish_result")) {
      return "publish";
    }
    if (candidate.includes("task_context")) {
      return "prepare";
    }
    if (candidate.includes("changes.patch")) {
      return "execute";
    }
    return "-";
  }

  function getEventPayload(event) {
    const payload = pick(event, "payload");
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return null;
    }
    return payload;
  }

  function isLogEvent(event) {
    const payload = getEventPayload(event);
    return Boolean(payload && pick(payload, "kind") === "log");
  }

  function eventLevel(event) {
    return String(pick(event, "level") || "info").trim().toLowerCase();
  }

  function eventMatchesOutputFilter(event, filter) {
    const normalizedFilter = String(filter || "all").trim().toLowerCase();
    if (normalizedFilter === "all") {
      return true;
    }
    if (normalizedFilter === "stages") {
      return !isLogEvent(event);
    }
    if (normalizedFilter === "logs") {
      return isLogEvent(event);
    }
    if (normalizedFilter === "warnings") {
      const level = eventLevel(event);
      if (level === "warn" || level === "warning" || level === "error") {
        return true;
      }
      const payload = getEventPayload(event);
      return Boolean(payload && String(pick(payload, "stream") || "") === "stderr");
    }
    return true;
  }

  function formatLiveOutputLine(event) {
    const timestamp = formatTimestamp(pick(event, "createdAt"));
    const level = eventLevel(event);
    const message = String(pick(event, "message") || "").replaceAll("\r", "");
    const payload = getEventPayload(event);
    if (payload && pick(payload, "kind") === "log") {
      const stream = String(pick(payload, "stream") || "stdout").trim();
      const stage = String(pick(payload, "stage") || deriveStageFromEvent(event)).trim();
      return `[${timestamp}] [${stream}] [${stage}] ${message}`;
    }
    const stage = deriveStageFromEvent(event);
    return `[${timestamp}] [${level}] [${stage}] ${message}`;
  }

  function buildLiveOutput(events, filter) {
    return events
      .filter((event) => eventMatchesOutputFilter(event, filter))
      .map((event) => formatLiveOutputLine(event))
      .join("\n");
  }

  function normalizeTaskRuntimeInput(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (!normalized) {
      return "";
    }
    return supportedTaskRuntimes.includes(normalized) ? normalized : "";
  }

  function isValidRepositoryInput(value) {
    const candidate = String(value || "").trim();
    if (!candidate) {
      return false;
    }
    if (ownerRepoPattern.test(candidate)) {
      return true;
    }
    if (candidate.startsWith("http://") || candidate.startsWith("https://")) {
      try {
        const parsed = new URL(candidate);
        if (!parsed.hostname || parsed.pathname === "/" || !parsed.pathname) {
          return false;
        }
        return !parsed.username && !parsed.password;
      } catch (_error) {
        return false;
      }
    }
    return candidate.startsWith("git@");
  }

  function parseCapabilitiesCsv(value) {
    const parts = String(value || "")
      .split(",")
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean);
    return Array.from(new Set(parts));
  }

  async function loadAvailableSkillIds() {
    if (cachedAvailableSkillIds) {
      return cachedAvailableSkillIds;
    }

    const skillsEndpoint = queueSourceConfig.skills || "/api/tasks/skills";
    try {
      const payload = await fetchJson(skillsEndpoint);
      const items = Array.isArray(payload?.items) ? payload.items : [];
      const discovered = items
        .map((item) => {
          if (item && typeof item.id === "string") {
            return item.id.trim();
          }
          return "";
        })
        .filter(Boolean);
      cachedAvailableSkillIds = Array.from(new Set(["auto", ...discovered]));
    } catch (error) {
      console.error("skills list load failed", error);
      return ["auto"];
    }

    return cachedAvailableSkillIds;
  }

  function populateSkillDatalist(datalistId, skillIds) {
    const node = document.getElementById(datalistId);
    if (!node) {
      return;
    }
    const options = (Array.isArray(skillIds) && skillIds.length > 0 ? skillIds : ["auto"])
      .map((skillId) => `<option value="${escapeHtml(skillId)}"></option>`)
      .join("");
    node.innerHTML = options;
  }

  function deriveRequiredCapabilities({
    runtimeMode,
    publishMode,
    taskSkillRequiredCapabilities = [],
    stepSkillRequiredCapabilities = [],
  }) {
    const capabilities = [runtimeMode, "git"];
    if (publishMode === "pr") {
      capabilities.push("gh");
    }
    capabilities.push(...taskSkillRequiredCapabilities);
    capabilities.push(...stepSkillRequiredCapabilities);
    return Array.from(new Set(capabilities));
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

  function sanitizeExternalHttpUrl(candidate) {
    const raw = String(candidate || "").trim();
    if (!raw) {
      return "";
    }
    try {
      const parsed = new URL(raw, window.location.origin);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        return "";
      }
      return parsed.href;
    } catch (_error) {
      return "";
    }
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
      const error = new Error(message);
      error.status = response.status;
      error.statusText = response.statusText;
      error.payload = payload;
      if (detail && typeof detail === "object" && !Array.isArray(detail)) {
        error.code = detail.code ? String(detail.code) : "";
      } else {
        error.code = "";
      }
      throw error;
    }

    return payload;
  }

  function classifyLiveSessionError(error) {
    const code = String(error?.code || "")
      .trim()
      .toLowerCase();
    const status = Number(error?.status || 0);

    if (code === "live_session_not_found") {
      return "disabled";
    }

    if (status === 404 && !code) {
      return "route_missing";
    }

    return "other";
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
          <td>${escapeHtml(row.queueName || "-")}</td>
          <td>${renderRuntime(row.runtimeMode)}</td>
          <td>${escapeHtml(row.skillId || "-")}</td>
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
            <th>Queue</th>
            <th>Runtime</th>
            <th>Skill</th>
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

  function summarizeInstructionPreview(value) {
    const raw = String(value ?? "")
      .replace(/\r\n/g, "\n")
      .trim();
    if (!raw) {
      return "";
    }
    const [firstParagraph] = raw.split(/\n\s*\n/, 1);
    const collapsed = firstParagraph
      .replace(/\s*\n\s*/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (!collapsed) {
      return "";
    }
    if (collapsed.length <= TASK_LIST_TITLE_MAX_CHARS) {
      return collapsed;
    }
    const truncated = collapsed.slice(0, TASK_LIST_TITLE_MAX_CHARS);
    const lastSpace = truncated.lastIndexOf(" ");
    const safeCut =
      lastSpace > TASK_LIST_TITLE_MAX_CHARS * 0.6 ? truncated.slice(0, lastSpace) : truncated;
    return `${safeCut.trimEnd()}...`;
  }

  function toQueueRows(items) {
    return items.map((item) => {
      const payload = pick(item, "payload") || {};
      const task = extractTaskNode(payload);
      const rawInstructions =
        (task && pick(task, "instructions")) || pick(payload, "instruction") || "";
      const summarizedTitle = summarizeInstructionPreview(rawInstructions);
      return {
        source: "queue",
        sourceLabel: "Queue",
        id: pick(item, "id") || "",
        payload,
        queueName: defaultQueueName,
        runtimeMode: extractRuntimeFromPayload(payload),
        skillId: extractSkillFromPayload(payload),
        rawStatus: pick(item, "status") || "queued",
        title: summarizedTitle || pick(item, "type") || "Queue Job",
        createdAt: pick(item, "createdAt"),
        startedAt: pick(item, "startedAt"),
        finishedAt: pick(item, "finishedAt"),
        link: `/tasks/queue/${encodeURIComponent(String(pick(item, "id") || ""))}`,
      };
    });
  }

  function toOrchestratorRows(runs) {
    return runs.map((run) => ({
      source: "orchestrator",
      sourceLabel: "Orchestrator",
      id: pick(run, "runId") || "",
      queueName: pick(run, "queueName") || "-",
      runtimeMode: null,
      skillId: null,
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
      `Running and queued work across queue and orchestrator systems. Unified queue: ${defaultQueueName}.`,
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
          console.error("active page data source failed", request.source, result.reason);
          errors.push(request.source);
        }
      });

      const notices = errors
        .map(
          (source) =>
            `<div class="notice error">${escapeHtml(
              `Unable to load ${source} data source.`,
            )}</div>`,
        )
        .join("");

      root.querySelector(".panel")?.remove();
      setView(
        "Active Tasks",
        `Running and queued work across queue and orchestrator systems. Unified queue: ${defaultQueueName}.`,
        `${notices}${renderRowsTable(sortRows(rows))}`,
      );
    };

    startPolling(loader, pollIntervals.list);
  }

  async function renderQueueListPage() {
    setView(
      "Queue Jobs",
      `All queue jobs ordered by creation time. Unified queue: ${defaultQueueName}.`,
      "<p class='loading'>Loading queue jobs...</p>",
    );

    const filterState = {
      runtime: "",
      skill: "",
      stageStatus: "",
      publishMode: "",
    };

    function applyQueueFilters(rows) {
      return rows.filter((row) => {
        if (filterState.runtime) {
          const rowRuntime = String(row.runtimeMode || "").trim().toLowerCase();
          if (rowRuntime !== filterState.runtime) {
            return false;
          }
        }

        if (filterState.skill) {
          const rowSkill = String(row.skillId || "").trim().toLowerCase();
          if (!rowSkill.includes(filterState.skill)) {
            return false;
          }
        }

        if (filterState.stageStatus) {
          const normalizedStatus = normalizeStatus("queue", row.rawStatus);
          if (normalizedStatus !== filterState.stageStatus) {
            return false;
          }
        }

        if (filterState.publishMode) {
          const publishMode =
            extractPublishModeFromPayload(row.payload || {}) || "branch";
          if (publishMode !== filterState.publishMode) {
            return false;
          }
        }

        return true;
      });
    }

    function renderQueueFilters() {
      const runtimeOptions = supportedTaskRuntimes
        .map(
          (runtime) =>
            `<option value="${escapeHtml(runtime)}" ${
              filterState.runtime === runtime ? "selected" : ""
            }>${escapeHtml(runtime)}</option>`,
        )
        .join("");
      const stageStatusOptions = [
        ["queued", "queued"],
        ["running", "running"],
        ["succeeded", "succeeded"],
        ["failed", "failed"],
        ["cancelled", "cancelled"],
      ]
        .map(
          ([value, label]) =>
            `<option value="${escapeHtml(value)}" ${
              filterState.stageStatus === value ? "selected" : ""
            }>${escapeHtml(label)}</option>`,
        )
        .join("");
      const publishOptions = ["none", "branch", "pr"]
        .map(
          (mode) =>
            `<option value="${escapeHtml(mode)}" ${
              filterState.publishMode === mode ? "selected" : ""
            }>${escapeHtml(mode)}</option>`,
        )
        .join("");

      return `
        <form id="queue-filter-form">
          <div class="grid-2">
            <label>Runtime
              <select name="runtime">
                <option value="">(all)</option>
                ${runtimeOptions}
              </select>
            </label>
            <label>Skill
              <input name="skill" placeholder="auto, speckit-orchestrate, ..." value="${escapeHtml(
                filterState.skill,
              )}" />
            </label>
          </div>
          <div class="grid-2">
            <label>Stage Status
              <select name="stageStatus">
                <option value="">(all)</option>
                ${stageStatusOptions}
              </select>
            </label>
          </div>
          <label>Publish Mode
            <select name="publishMode">
              <option value="">(all)</option>
              ${publishOptions}
            </select>
          </label>
        </form>
      `;
    }

    function renderTelemetrySummary(snapshot) {
      if (!snapshot || typeof snapshot !== "object") {
        return "";
      }
      const volumes = pick(snapshot, "jobVolumeByType") || {};
      const publish = pick(snapshot, "publishOutcomes") || {};
      const legacyCount = Number(pick(snapshot, "legacyJobSubmissions") || 0);
      const totalJobs = Number(pick(snapshot, "totalJobs") || 0);
      const publishedRate = Number(pick(publish, "publishedRate") || 0);
      const failedRate = Number(pick(publish, "failedRate") || 0);
      return `
        <div class="grid-2">
          <div class="card"><strong>Total Jobs (Window):</strong> ${escapeHtml(totalJobs)}</div>
          <div class="card"><strong>Legacy Submissions:</strong> ${escapeHtml(legacyCount)}</div>
          <div class="card"><strong>Task Jobs:</strong> ${escapeHtml(Number(volumes.task || 0))}</div>
          <div class="card"><strong>Publish Success Rate:</strong> ${escapeHtml(
            (publishedRate * 100).toFixed(1),
          )}%</div>
          <div class="card"><strong>Publish Failure Rate:</strong> ${escapeHtml(
            (failedRate * 100).toFixed(1),
          )}%</div>
        </div>
      `;
    }

    function attachFilterHandlers(rows, telemetryHtml) {
      const filterForm = document.getElementById("queue-filter-form");
      if (!filterForm) {
        return;
      }
      const runtimeField = filterForm.elements.namedItem("runtime");
      const skillField = filterForm.elements.namedItem("skill");
      const stageField = filterForm.elements.namedItem("stageStatus");
      const publishField = filterForm.elements.namedItem("publishMode");

      const rerender = () => {
        const filteredRows = applyQueueFilters(rows);
        setView(
          "Queue Jobs",
          `All queue jobs ordered by creation time. Unified queue: ${defaultQueueName}.`,
          `<div class="actions"><a href="/tasks/queue/new"><button type="button">New Queue Task</button></a></div>${telemetryHtml}${renderQueueFilters()}${renderRowsTable(filteredRows)}`,
        );
        attachFilterHandlers(rows, telemetryHtml);
      };

      if (runtimeField) {
        runtimeField.addEventListener("change", () => {
          filterState.runtime = normalizeTaskRuntimeInput(runtimeField.value);
          rerender();
        });
      }
      if (skillField) {
        skillField.addEventListener("input", () => {
          filterState.skill = String(skillField.value || "").trim().toLowerCase();
          rerender();
        });
      }
      if (stageField) {
        stageField.addEventListener("change", () => {
          filterState.stageStatus = String(stageField.value || "").trim().toLowerCase();
          rerender();
        });
      }
      if (publishField) {
        publishField.addEventListener("change", () => {
          filterState.publishMode = String(publishField.value || "").trim().toLowerCase();
          rerender();
        });
      }
    }

    const load = async () => {
      let telemetryPayload = null;
      const payload = await fetchJson("/api/queue/jobs?limit=200");
      try {
        telemetryPayload = await fetchJson(
          (queueSourceConfig.migrationTelemetry || "/api/queue/telemetry/migration") +
            "?windowHours=168",
        );
      } catch (error) {
        console.error("queue migration telemetry load failed", error);
      }
      const rows = sortRows(toQueueRows(payload?.items || []));
      const filteredRows = applyQueueFilters(rows);
      const telemetryHtml = renderTelemetrySummary(telemetryPayload);
      setView(
        "Queue Jobs",
        `All queue jobs ordered by creation time. Unified queue: ${defaultQueueName}.`,
        `<div class="actions"><a href="/tasks/queue/new"><button type="button">New Queue Task</button></a></div>${telemetryHtml}${renderQueueFilters()}${renderRowsTable(filteredRows)}`,
      );
      attachFilterHandlers(rows, telemetryHtml);
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
    const runtimeOptions = supportedTaskRuntimes
      .map(
        (runtime) =>
          `<option value="${escapeHtml(runtime)}" ${
            runtime === defaultTaskRuntime ? "selected" : ""
          }>${escapeHtml(runtime)}</option>`,
      )
      .join("");
    const repositoryFallback = defaultRepository;
    const repositoryHint = repositoryFallback
      ? `Leave blank to use default repository: ${repositoryFallback}.`
      : "Set a repository in this form (no system default repository is configured).";

    setView(
      "Submit Queue Task",
      `Create a typed Task job. Jobs are consumed from the shared queue ${defaultQueueName}.`,
      `
      <form id="queue-submit-form">
        <label>Runtime
          <select name="runtime">
            ${runtimeOptions}
          </select>
        </label>
        <div class="card">
          <div class="actions">
            <strong>Steps</strong>
          </div>
          <span class="small">Step 1 is required and defines default task instructions + skill. Add more steps for multi-step runs.</span>
          <div id="queue-steps-list"></div>
        </div>
        <datalist id="queue-skill-options">
          <option value="auto"></option>
        </datalist>
        <div class="grid-2">
          <label>Model
            <input name="model" value="${escapeHtml(defaultTaskModel)}" placeholder="runtime default" />
          </label>
          <label>Effort
            <input name="effort" value="${escapeHtml(defaultTaskEffort)}" placeholder="runtime default" />
          </label>
        </div>
        <label>GitHub Repo
          <input name="repository" value="${escapeHtml(repositoryFallback)}" placeholder="owner/repo" />
          <span class="small">${escapeHtml(repositoryHint)} Accepted formats: owner/repo, https://&lt;host&gt;/&lt;path&gt;, or git@&lt;host&gt;:&lt;path&gt; (token-free).</span>
        </label>
        <div class="grid-2">
          <label>Starting Branch (optional)
            <input name="startingBranch" placeholder="repo default branch" />
          </label>
          <label>New Branch (optional)
            <input name="newBranch" placeholder="auto-generated unless starting branch is non-default" />
          </label>
        </div>
        <label>Publish Mode
          <select name="publishMode">
            <option value="pr" selected>pr</option>
            <option value="branch">branch</option>
            <option value="none">none</option>
          </select>
          <span class="small">Defaults: no branch fields resolve at execution time; publish default is <span class="inline-code">pr</span>.</span>
        </label>
        <div class="grid-2">
          <label>Priority
            <input type="number" name="priority" value="0" />
          </label>
          <label>Max Attempts
            <input type="number" min="1" name="maxAttempts" value="3" />
          </label>
        </div>
        <label>Affinity Key (optional)
          <input name="affinityKey" placeholder="optional affinity key" />
        </label>
        <p class="small">Submission emits canonical <span class="inline-code">type="task"</span> payloads; server validation rejects malformed contracts.</p>
        <div class="actions">
          <button type="submit">Create Queue Task</button>
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
    const runtimeSelect = form.querySelector('select[name="runtime"]');
    const modelInputElement = form.querySelector('input[name="model"]');
    const effortInputElement = form.querySelector('input[name="effort"]');
    const stepsList = document.getElementById("queue-steps-list");
    const runtimeModelDefaults = {
      ...configuredModelDefaults,
      codex: codexDefaultTaskModel,
    };
    const runtimeEffortDefaults = {
      ...configuredEffortDefaults,
      codex: codexDefaultTaskEffort,
    };
    let activeDefaultModel = resolveRuntimeDefault(runtimeModelDefaults, defaultTaskRuntime);
    let activeDefaultEffort = resolveRuntimeDefault(
      runtimeEffortDefaults,
      defaultTaskRuntime,
    );
    const applyRuntimeDefaults = (runtime) => {
      if (!modelInputElement || !effortInputElement) {
        return;
      }
      const nextDefaultModel = resolveRuntimeDefault(runtimeModelDefaults, runtime);
      const nextDefaultEffort = resolveRuntimeDefault(runtimeEffortDefaults, runtime);
      if (modelInputElement.value.trim() === activeDefaultModel) {
        modelInputElement.value = nextDefaultModel;
      }
      if (effortInputElement.value.trim() === activeDefaultEffort) {
        effortInputElement.value = nextDefaultEffort;
      }
      activeDefaultModel = nextDefaultModel;
      activeDefaultEffort = nextDefaultEffort;
    };
    if (runtimeSelect) {
      runtimeSelect.addEventListener("change", (event) => {
        const nextRuntime = normalizeTaskRuntimeInput(event.target.value);
        applyRuntimeDefaults(nextRuntime || defaultTaskRuntime);
      });
    }
    const createStepStateEntry = (overrides = {}) => ({
      id: "",
      title: "",
      instructions: "",
      skillId: "",
      skillArgs: "",
      skillRequiredCapabilities: "",
      ...overrides,
    });
    const stepState = [createStepStateEntry()];
    const ensurePrimaryStep = () => {
      if (stepState.length === 0) {
        stepState.push(createStepStateEntry());
      }
    };
    const renderStepEditor = () => {
      if (!stepsList) {
        console.error("[dashboard] #queue-steps-list not found; step editor unavailable");
        return;
      }
      ensurePrimaryStep();
      const rows = stepState
        .map((step, index) => {
          const isPrimaryStep = index === 0;
          const stepLabel = isPrimaryStep ? " (Required)" : "";
          const skillLabel = isPrimaryStep ? "Skill" : "Skill (optional)";
          const skillPlaceholder = isPrimaryStep
            ? "auto (default), speckit-orchestrate, ..."
            : "inherit Step 1 skill";
          const instructionsLabel = isPrimaryStep ? "Instructions" : "Instructions (optional)";
          const instructionsPlaceholder = isPrimaryStep
            ? "Describe the task to execute against the repository."
            : "Step-specific instructions (leave blank to continue from the task objective).";
          const upDisabled = isPrimaryStep || index <= 1 ? "disabled" : "";
          const downDisabled = isPrimaryStep || index === stepState.length - 1 ? "disabled" : "";
          const removeDisabled = isPrimaryStep ? "disabled" : "";
          const defaultHint = isPrimaryStep
            ? "Step 1 skill values are forwarded to <span class=\"inline-code\">task.skill</span>."
            : "Leave skill blank to inherit Step 1 defaults.";
          return `
            <div class="card" data-step-index="${index}">
              <div class="actions">
                <strong>Step ${index + 1}${stepLabel}</strong>
                <div>
                  <button type="button" data-step-action="up" data-step-index="${index}" ${upDisabled}>Up</button>
                  <button type="button" data-step-action="down" data-step-index="${index}" ${downDisabled}>Down</button>
                  <button type="button" data-step-action="remove" data-step-index="${index}" ${removeDisabled}>Remove</button>
                </div>
              </div>
              <div class="grid-2">
                <label>Step ID (optional)
                  <input data-step-field="id" data-step-index="${index}" value="${escapeHtml(step.id)}" placeholder="step-${index + 1}" />
                </label>
                <label>Title (optional)
                  <input data-step-field="title" data-step-index="${index}" value="${escapeHtml(step.title)}" placeholder="Short label" />
                </label>
              </div>
              <label>${instructionsLabel}
                <textarea class="queue-step-instructions" data-step-field="instructions" data-step-index="${index}" placeholder="${escapeHtml(
                  instructionsPlaceholder,
                )}">${escapeHtml(
                  step.instructions,
                )}</textarea>
              </label>
              <div class="grid-2">
                <label>${skillLabel}
                  <input data-step-field="skillId" data-step-index="${index}" value="${escapeHtml(
                    step.skillId,
                  )}" placeholder="${escapeHtml(skillPlaceholder)}" list="queue-skill-options" />
                  <span class="small">${defaultHint}</span>
                </label>
                <label>Skill Required Capabilities (optional CSV)
                  <input data-step-field="skillRequiredCapabilities" data-step-index="${index}" value="${escapeHtml(
                    step.skillRequiredCapabilities,
                  )}" placeholder="docker,qdrant,unity" />
                  <span class="small">Merged into job <span class="inline-code">requiredCapabilities</span> when provided.</span>
                </label>
              </div>
              <label>Skill Args (optional JSON object)
                <textarea class="queue-step-skill-args" data-step-field="skillArgs" data-step-index="${index}" placeholder='{"notes":"optional context"}'>${escapeHtml(
                  step.skillArgs,
                )}</textarea>
              </label>
            </div>
          `;
        })
        .join("");
      const addStepButtonRow = `
        <div class="actions queue-step-add">
          <button type="button" data-step-action="add">Add Step</button>
        </div>
      `;
      stepsList.innerHTML = rows + addStepButtonRow;
    };
    const readStepIndex = (target) => {
      if (!(target instanceof HTMLElement)) {
        return null;
      }
      const raw = target.getAttribute("data-step-index");
      if (raw === null) {
        return null;
      }
      const index = Number(raw);
      if (!Number.isInteger(index) || index < 0 || index >= stepState.length) {
        return null;
      }
      return index;
    };
    if (stepsList) {
      stepsList.addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) {
          return;
        }
        const actionButton = target.closest("[data-step-action]");
        if (!(actionButton instanceof HTMLElement)) {
          return;
        }
        const action = actionButton.getAttribute("data-step-action");
        if (!action) {
          return;
        }
        if (action === "add") {
          stepState.push(createStepStateEntry());
          renderStepEditor();
          return;
        }
        const index = readStepIndex(actionButton);
        if (index === null) {
          return;
        }
        if (action === "remove") {
          if (index === 0) {
            return;
          }
          stepState.splice(index, 1);
          renderStepEditor();
          return;
        }
        if (action === "up" && index > 1) {
          const current = stepState[index];
          stepState[index] = stepState[index - 1];
          stepState[index - 1] = current;
          renderStepEditor();
          return;
        }
        if (action === "down" && index > 0 && index < stepState.length - 1) {
          const current = stepState[index];
          stepState[index] = stepState[index + 1];
          stepState[index + 1] = current;
          renderStepEditor();
        }
      });
      stepsList.addEventListener("input", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) {
          return;
        }
        const fieldInput = target.closest("[data-step-field]");
        if (!(fieldInput instanceof HTMLInputElement || fieldInput instanceof HTMLTextAreaElement)) {
          return;
        }
        const field = fieldInput.getAttribute("data-step-field");
        if (!field) {
          return;
        }
        const index = readStepIndex(fieldInput);
        if (index === null) {
          return;
        }
        stepState[index][field] = fieldInput.value || "";
      });
    }
    renderStepEditor();
    loadAvailableSkillIds().then((skillIds) => {
      populateSkillDatalist("queue-skill-options", skillIds);
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      message.className = "small";
      message.textContent = "Submitting...";

      const formData = new FormData(form);
      ensurePrimaryStep();
      const primaryStep = stepState[0] || createStepStateEntry();
      const instructions = String(primaryStep.instructions || "").trim();
      if (!instructions) {
        message.className = "notice error";
        message.textContent = "Step 1 instructions are required.";
        return;
      }

      const repositoryInput = String(formData.get("repository") || "").trim();
      const repository = repositoryInput || defaultRepository;
      if (!repository) {
        message.className = "notice error";
        message.textContent =
          "Repository is required because no system default repository is configured.";
        return;
      }
      if (!isValidRepositoryInput(repository)) {
        message.className = "notice error";
        message.textContent =
          "Repository must be owner/repo, https://<host>/<path>, or git@<host>:<path> (token-free).";
        return;
      }

      const affinityKey = String(formData.get("affinityKey") || "").trim();
      const rawRuntime = String(formData.get("runtime") || "").trim();
      const runtimeCandidate = rawRuntime || defaultTaskRuntime;
      const runtimeMode = normalizeTaskRuntimeInput(runtimeCandidate);
      if (!runtimeMode) {
        message.className = "notice error";
        message.textContent =
          "Runtime must be one of: " + supportedTaskRuntimes.join(", ") + ".";
        return;
      }

      const publishMode = String(formData.get("publishMode") || "pr")
        .trim()
        .toLowerCase();
      if (!["none", "branch", "pr"].includes(publishMode)) {
        message.className = "notice error";
        message.textContent =
          "Publish mode must be one of: none, branch, pr.";
        return;
      }

      const priority = Number(formData.get("priority") || 0);
      if (!Number.isInteger(priority)) {
        message.className = "notice error";
        message.textContent = "Priority must be an integer.";
        return;
      }

      const maxAttempts = Number(formData.get("maxAttempts") || 3);
      if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
        message.className = "notice error";
        message.textContent = "Max Attempts must be an integer >= 1.";
        return;
      }

      const skillId = String(primaryStep.skillId || "").trim() || "auto";
      const skillArgsRaw = String(primaryStep.skillArgs || "").trim();
      const taskSkillRequiredCapabilities = parseCapabilitiesCsv(
        primaryStep.skillRequiredCapabilities || "",
      );
      let skillArgs = {};
      if (skillArgsRaw) {
        try {
          const parsed = JSON.parse(skillArgsRaw);
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("Skill args must be a JSON object.");
          }
          skillArgs = parsed;
        } catch (error) {
          message.className = "notice error";
          message.textContent =
            "Step 1 Skill Args must be valid JSON object text (for example: {\"featureKey\":\"...\"}).";
          return;
        }
      }
      const model = String(formData.get("model") || "").trim() || null;
      const effort = String(formData.get("effort") || "").trim() || null;
      const startingBranch = String(formData.get("startingBranch") || "").trim() || null;
      const newBranch = String(formData.get("newBranch") || "").trim() || null;
      const primaryStepId = String(primaryStep.id || "").trim();
      const primaryStepTitle = String(primaryStep.title || "").trim();
      const additionalSteps = [];
      const stepSkillRequiredCapabilities = [];
      for (let index = 1; index < stepState.length; index += 1) {
        const rawStep = stepState[index] || {};
        const stepId = String(rawStep.id || "").trim();
        const stepTitle = String(rawStep.title || "").trim();
        const stepInstructions = String(rawStep.instructions || "").trim();
        const stepSkillId = String(rawStep.skillId || "").trim();
        const stepSkillArgsRaw = String(rawStep.skillArgs || "").trim();
        const stepSkillCaps = parseCapabilitiesCsv(rawStep.skillRequiredCapabilities || "");
        const hasStepContent =
          Boolean(stepId) ||
          Boolean(stepTitle) ||
          Boolean(stepInstructions) ||
          Boolean(stepSkillId) ||
          Boolean(stepSkillArgsRaw) ||
          stepSkillCaps.length > 0;
        if (!hasStepContent) {
          continue;
        }
        let stepSkillArgs = {};
        if (stepSkillArgsRaw) {
          try {
            const parsed = JSON.parse(stepSkillArgsRaw);
            if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
              throw new Error("Step skill args must be a JSON object.");
            }
            stepSkillArgs = parsed;
          } catch (_error) {
            message.className = "notice error";
            message.textContent = `Step ${index + 1} Skill Args must be valid JSON object text.`;
            return;
          }
        }
        const stepPayload = {};
        if (stepId) {
          stepPayload.id = stepId;
        }
        if (stepTitle) {
          stepPayload.title = stepTitle;
        }
        if (stepInstructions) {
          stepPayload.instructions = stepInstructions;
        }
        if (stepSkillId || stepSkillArgsRaw || stepSkillCaps.length > 0) {
          const skillPayload = {
            id: stepSkillId || skillId,
            args: stepSkillArgs,
          };
          if (stepSkillCaps.length > 0) {
            skillPayload.requiredCapabilities = stepSkillCaps;
            stepSkillRequiredCapabilities.push(...stepSkillCaps);
          }
          stepPayload.skill = skillPayload;
        }
        additionalSteps.push(stepPayload);
      }
      const includeExplicitSteps =
        additionalSteps.length > 0 || Boolean(primaryStepId) || Boolean(primaryStepTitle);
      const normalizedSteps = includeExplicitSteps
        ? [
            {
              ...(primaryStepId ? { id: primaryStepId } : {}),
              ...(primaryStepTitle ? { title: primaryStepTitle } : {}),
              instructions,
            },
            ...additionalSteps,
          ]
        : [];

      const payload = {
        repository,
        requiredCapabilities: deriveRequiredCapabilities({
          runtimeMode,
          publishMode,
          taskSkillRequiredCapabilities,
          stepSkillRequiredCapabilities,
        }),
        targetRuntime: runtimeMode,
        task: {
          instructions,
          skill: {
            id: skillId,
            args: skillArgs,
            ...(taskSkillRequiredCapabilities.length > 0
              ? { requiredCapabilities: taskSkillRequiredCapabilities }
              : {}),
          },
          runtime: { mode: runtimeMode, model, effort },
          git: { startingBranch, newBranch },
          publish: {
            mode: publishMode,
            prBaseBranch: null,
            commitMessage: null,
            prTitle: null,
            prBody: null,
          },
          ...(normalizedSteps.length > 0 ? { steps: normalizedSteps } : {}),
        },
      };

      const requestBody = {
        type: "task",
        payload,
        priority,
        maxAttempts,
      };
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
        console.error("queue submit failed", error);
        message.className = "notice error";
        message.textContent =
          "Failed to create queue task: " +
          String(error?.message || "request failed");
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
        console.error("orchestrator submit failed", error);
        message.className = "notice error";
        message.textContent = "Failed to create orchestrator run.";
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
      job: null,
      artifacts: [],
      events: [],
      liveSession: null,
      liveSessionError: null,
      liveSessionRouteMissing: false,
      liveSessionRwAttach: "",
      liveSessionRwWeb: "",
      liveSessionRwGrantedUntil: "",
      eventIds: new Set(),
      after: null,
      afterEventId: null,
      outputFilter: "all",
      followOutput: true,
      eventsTransport: "polling",
      eventsTransportStatus: "idle",
      eventsPollingStarted: false,
    };

    const appendIncomingEvents = (incomingEvents) => {
      let changed = false;
      const ordered = (incomingEvents || []).slice().sort((left, right) => {
        return (
          Date.parse(pick(left, "createdAt") || 0) - Date.parse(pick(right, "createdAt") || 0)
        );
      });
      ordered.forEach((event) => {
        const eventId = String(pick(event, "id") || "");
        if (!eventId || state.eventIds.has(eventId)) {
          return;
        }
        state.eventIds.add(eventId);
        state.events.push(event);
        state.after = pick(event, "createdAt") || state.after;
        state.afterEventId = eventId;
        changed = true;
      });

      const maxEvents = 500;
      if (state.events.length > maxEvents) {
        const overflow = state.events.length - maxEvents;
        const removed = state.events.splice(0, overflow);
        removed.forEach((event) => {
          const eventId = String(pick(event, "id") || "");
          if (eventId) {
            state.eventIds.delete(eventId);
          }
        });
      }
      return changed;
    };

    const resolveFullLogArtifact = (artifacts) => {
      const allArtifacts = Array.isArray(artifacts) ? artifacts : [];
      const byPriority = [
        "logs/execute.log",
        "logs/codex_exec.log",
        "logs/steps/step-0000.log",
      ];
      for (const name of byPriority) {
        const exact = allArtifacts.find((artifact) => pick(artifact, "name") === name);
        if (exact) {
          return exact;
        }
      }
      return (
        allArtifacts.find((artifact) => String(pick(artifact, "name") || "").startsWith("logs/")) ||
        null
      );
    };

    const render = (loadError) => {
      const job = state.job;
      const artifacts = state.artifacts;
      const events = state.events;
      const notices = loadError
        ? `<div class="notice error">${escapeHtml(loadError)}</div>`
        : "";
      const normalizedStatus = job ? normalizeStatus("queue", pick(job, "status")) : "";
      const cancelRequestedAt = job ? pick(job, "cancelRequestedAt") : null;
      const cancelPending = Boolean(cancelRequestedAt) && normalizedStatus === "running";
      const canCancel = Boolean(job) && (normalizedStatus === "queued" || normalizedStatus === "running");
      const cancelButtonDisabled = !canCancel || cancelPending;
      const cancelButtonLabel = cancelPending ? "Cancellation Requested" : "Cancel Job";
      const eventRows = events
        .map(
          (event) => `
            <tr>
              <td>${formatTimestamp(pick(event, "createdAt"))}</td>
              <td>${escapeHtml(deriveStageFromEvent(event))}</td>
              <td>${escapeHtml(pick(event, "level") || "info")}</td>
              <td>${escapeHtml(pick(event, "message") || "")}</td>
            </tr>
          `,
        )
        .join("");

      const liveOutputText = buildLiveOutput(events, state.outputFilter);
      const fullLogArtifact = resolveFullLogArtifact(artifacts);
      const fullLogDownloadUrl = fullLogArtifact
        ? endpoint("/api/queue/jobs/{id}/artifacts/{artifactId}/download", {
            id: jobId,
            artifactId: pick(fullLogArtifact, "id"),
          })
        : "";
      const transportLabel =
        state.eventsTransport === "sse" ? "SSE" : "Polling";
      const transportStatus = state.eventsTransportStatus;

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
              <td>${escapeHtml(deriveStageFromArtifactName(pick(artifact, "name") || ""))}</td>
              <td>${escapeHtml(String(pick(artifact, "sizeBytes") || "-"))}</td>
              <td>${escapeHtml(pick(artifact, "contentType") || "-")}</td>
              <td><a href="${escapeHtml(downloadUrl)}">Download</a></td>
            </tr>
          `;
        })
        .join("");

      const detail = job
        ? `
            ${(() => {
              const payload = pick(job, "payload") || {};
              const runtimeTarget = extractRuntimeFromPayload(payload) || "any";
              const runtimeModel = extractRuntimeModelFromPayload(payload) || "default";
              const runtimeEffort = extractRuntimeEffortFromPayload(payload) || "default";
              const selectedSkill = extractSkillFromPayload(payload) || "auto";
              return `
            <p class="small">Effective queue: <span class="inline-code">${escapeHtml(
              pick(job, "queueName") || defaultQueueName,
            )}</span></p>
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
              <div class="card"><strong>Runtime Target:</strong> ${escapeHtml(runtimeTarget)}</div>
              <div class="card"><strong>Runtime Model:</strong> ${escapeHtml(runtimeModel)}</div>
              <div class="card"><strong>Runtime Effort:</strong> ${escapeHtml(runtimeEffort)}</div>
              <div class="card"><strong>Skill:</strong> ${escapeHtml(selectedSkill)}</div>
              <div class="card"><strong>Cancel Requested:</strong> ${formatTimestamp(
                pick(job, "cancelRequestedAt"),
              )}</div>
              <div class="card"><strong>Cancel Reason:</strong> ${escapeHtml(
                pick(job, "cancelReason") || "-",
              )}</div>
              <div class="card"><strong>Lease Expires:</strong> ${formatTimestamp(
                pick(job, "leaseExpiresAt"),
              )}</div>
            </div>
          `;
            })()}
          `
        : "<div class='notice error'>Queue job not found.</div>";

      const jobPayload =
        job && typeof pick(job, "payload") === "object" && !Array.isArray(pick(job, "payload"))
          ? pick(job, "payload")
          : {};
      const liveControl =
        jobPayload &&
        typeof pick(jobPayload, "liveControl") === "object" &&
        !Array.isArray(pick(jobPayload, "liveControl"))
          ? pick(jobPayload, "liveControl")
          : {};
      const pauseActive = Boolean(pick(liveControl, "paused"));
      const liveSession = state.liveSession;
      const liveSessionRouteMissing = Boolean(state.liveSessionRouteMissing);
      const liveSessionStatus = liveSession
        ? String(pick(liveSession, "status") || "disabled")
        : liveSessionRouteMissing
          ? "unavailable"
          : "disabled";
      const liveSessionCreated = Boolean(liveSession);
      const liveSessionReady = liveSessionStatus === "ready";
      const liveSessionActionsDisabled = liveSessionRouteMissing;
      const showGrantDetails = Boolean(state.liveSessionRwAttach);
      const liveSessionRwWebUrl = sanitizeExternalHttpUrl(state.liveSessionRwWeb);
      const liveSessionSection = job
        ? `
            <section>
              <h3>Live Session</h3>
              ${
                state.liveSessionError
                  ? `<div class="notice error">${escapeHtml(state.liveSessionError)}</div>`
                  : ""
              }
              <div id="queue-live-session-notice"></div>
              <div class="grid-2">
                <div class="card"><strong>Status:</strong> ${escapeHtml(liveSessionStatus)}</div>
                <div class="card"><strong>Provider:</strong> ${escapeHtml(
                  String(pick(liveSession || {}, "provider") || "tmate"),
                )}</div>
                <div class="card"><strong>Ready:</strong> ${formatTimestamp(
                  pick(liveSession || {}, "readyAt"),
                )}</div>
                <div class="card"><strong>Expires:</strong> ${formatTimestamp(
                  pick(liveSession || {}, "expiresAt"),
                )}</div>
                <div class="card"><strong>RO Attach:</strong> ${escapeHtml(
                  String(pick(liveSession || {}, "attachRo") || "-"),
                )}</div>
                <div class="card"><strong>RW Granted Until:</strong> ${formatTimestamp(
                  state.liveSessionRwGrantedUntil || pick(liveSession || {}, "rwGrantedUntil"),
                )}</div>
              </div>
              ${
                showGrantDetails
                  ? `<p class="small">RW attach: <span class="inline-code">${escapeHtml(
                      state.liveSessionRwAttach,
                    )}</span>${
                      liveSessionRwWebUrl
                        ? ` | Web: <a href="${escapeHtml(liveSessionRwWebUrl)}" target="_blank" rel="noreferrer">open</a>`
                        : ""
                    }</p>`
                  : ""
              }
              <div class="actions">
                <button type="button" id="queue-live-enable" ${
                  liveSessionActionsDisabled
                    ? "disabled"
                    : liveSessionCreated && ["starting", "ready"].includes(liveSessionStatus)
                      ? "disabled"
                      : ""
                }>Enable Live Session</button>
                <button type="button" id="queue-live-grant" ${
                  liveSessionReady && !liveSessionActionsDisabled ? "" : "disabled"
                }>Grant Write (15m)</button>
                <button type="button" id="queue-live-revoke" ${
                  liveSessionCreated && !liveSessionActionsDisabled ? "" : "disabled"
                }>Revoke Session</button>
                <button type="button" id="queue-live-pause">${
                  pauseActive ? "Resume" : "Pause"
                }</button>
                <button type="button" id="queue-live-takeover">Takeover</button>
              </div>
              <div class="actions">
                <input id="queue-operator-message" placeholder="Send operator message..." />
                <button type="button" id="queue-operator-send">Send</button>
              </div>
            </section>
          `
        : "";

      setView(
        "Queue Job Detail",
        `Job ${jobId}`,
        `
          ${notices}
          ${job ? `<div id="queue-cancel-notice"></div>` : ""}
          ${job ? `<div class="actions"><button type="button" id="queue-cancel-button" ${
            cancelButtonDisabled ? "disabled" : ""
          }>${escapeHtml(cancelButtonLabel)}</button></div>` : ""}
          ${detail}
          <div class="stack">
            ${liveSessionSection}
            <section>
              <h3>Events</h3>
              <table>
                <thead><tr><th>Time</th><th>Stage</th><th>Level</th><th>Message</th></tr></thead>
                <tbody>${eventRows || "<tr><td colspan='4' class='small'>No events.</td></tr>"}</tbody>
              </table>
            </section>
            <section>
              <div class="actions queue-live-output-toolbar">
                <label class="queue-inline-toggle">
                  <input type="checkbox" id="queue-follow-output" ${
                    state.followOutput ? "checked" : ""
                  } />
                  Follow output
                </label>
                <label class="queue-inline-filter">
                  Filter
                  <select id="queue-output-filter">
                    <option value="all" ${state.outputFilter === "all" ? "selected" : ""}>All</option>
                    <option value="stages" ${
                      state.outputFilter === "stages" ? "selected" : ""
                    }>Stages</option>
                    <option value="logs" ${state.outputFilter === "logs" ? "selected" : ""}>Logs</option>
                    <option value="warnings" ${
                      state.outputFilter === "warnings" ? "selected" : ""
                    }>Warnings/Errors</option>
                  </select>
                </label>
                <button type="button" class="secondary" id="queue-copy-output">Copy</button>
                ${
                  fullLogArtifact
                    ? `<a href="${escapeHtml(fullLogDownloadUrl)}"><button type="button" class="secondary">Download Full Logs</button></a>`
                    : "<span class='small'>Download full logs unavailable.</span>"
                }
                <span class="small">Live transport: ${escapeHtml(
                  `${transportLabel} (${transportStatus})`,
                )}</span>
              </div>
              <pre id="queue-live-output" class="queue-live-output">${escapeHtml(
                liveOutputText || "",
              )}</pre>
            </section>
            <section>
              <h3>Artifacts</h3>
              <table>
                <thead><tr><th>Name</th><th>Stage</th><th>Size</th><th>Content Type</th><th>Action</th></tr></thead>
                <tbody>${artifactRows || "<tr><td colspan='5' class='small'>No artifacts.</td></tr>"}</tbody>
              </table>
            </section>
          </div>
        `,
      );

      const cancelButton = document.getElementById("queue-cancel-button");
      const cancelNotice = document.getElementById("queue-cancel-notice");
      if (cancelButton && job) {
        cancelButton.addEventListener("click", async () => {
          cancelButton.disabled = true;
          if (cancelNotice) {
            cancelNotice.className = "notice";
            cancelNotice.textContent = "Submitting cancellation request...";
          }
          try {
            await fetchJson(
              endpoint(
                queueSourceConfig.cancel || "/api/queue/jobs/{id}/cancel",
                { id: jobId },
              ),
              {
                method: "POST",
                body: JSON.stringify({ reason: "Cancellation requested from dashboard" }),
              },
            );
            if (cancelNotice) {
              cancelNotice.className = "notice";
              cancelNotice.textContent = "Cancellation request submitted.";
            }
            await loadEvents();
            await loadDetail();
          } catch (error) {
            console.error("queue cancellation request failed", error);
            if (cancelNotice) {
              cancelNotice.className = "notice error";
              cancelNotice.textContent = "Failed to cancel queue job.";
            }
            cancelButton.disabled = false;
          }
        });
      }

      const liveNotice = document.getElementById("queue-live-session-notice");
      const setLiveNotice = (text, isError = false) => {
        if (!liveNotice) {
          return;
        }
        if (!text) {
          liveNotice.className = "";
          liveNotice.textContent = "";
          return;
        }
        liveNotice.className = isError ? "notice error" : "notice";
        liveNotice.textContent = text;
      };

      const liveEnableButton = document.getElementById("queue-live-enable");
      if (liveEnableButton) {
        liveEnableButton.addEventListener("click", async () => {
          liveEnableButton.disabled = true;
          setLiveNotice("Enabling live session...");
          try {
            await fetchJson(
              endpoint(
                queueSourceConfig.liveSession || "/api/queue/jobs/{id}/live-session",
                { id: jobId },
              ),
              {
                method: "POST",
                body: JSON.stringify({}),
              },
            );
            state.liveSessionRwAttach = "";
            state.liveSessionRwWeb = "";
            state.liveSessionRwGrantedUntil = "";
            await loadDetail();
            await loadEvents();
            setLiveNotice("Live session enabled.");
          } catch (error) {
            console.error("live session enable failed", error);
            setLiveNotice("Failed to enable live session.", true);
            liveEnableButton.disabled = false;
          }
        });
      }

      const liveGrantButton = document.getElementById("queue-live-grant");
      if (liveGrantButton) {
        liveGrantButton.addEventListener("click", async () => {
          liveGrantButton.disabled = true;
          setLiveNotice("Requesting temporary write access...");
          try {
            const grant = await fetchJson(
              endpoint(
                queueSourceConfig.liveSessionGrantWrite ||
                  "/api/queue/jobs/{id}/live-session/grant-write",
                { id: jobId },
              ),
              {
                method: "POST",
                body: JSON.stringify({ ttlMinutes: 15 }),
              },
            );
            state.liveSessionRwAttach = String(pick(grant, "attachRw") || "");
            state.liveSessionRwWeb = String(pick(grant, "webRw") || "");
            state.liveSessionRwGrantedUntil = String(pick(grant, "grantedUntil") || "");
            await loadDetail();
            await loadEvents();
            setLiveNotice("RW access granted.");
          } catch (error) {
            console.error("live session grant failed", error);
            setLiveNotice("Failed to grant write access.", true);
            liveGrantButton.disabled = false;
          }
        });
      }

      const liveRevokeButton = document.getElementById("queue-live-revoke");
      if (liveRevokeButton) {
        liveRevokeButton.addEventListener("click", async () => {
          liveRevokeButton.disabled = true;
          setLiveNotice("Revoking live session...");
          try {
            await fetchJson(
              endpoint(
                queueSourceConfig.liveSessionRevoke ||
                  "/api/queue/jobs/{id}/live-session/revoke",
                { id: jobId },
              ),
              {
                method: "POST",
                body: JSON.stringify({ reason: "Revoked from dashboard" }),
              },
            );
            state.liveSessionRwAttach = "";
            state.liveSessionRwWeb = "";
            state.liveSessionRwGrantedUntil = "";
            await loadDetail();
            await loadEvents();
            setLiveNotice("Live session revoked.");
          } catch (error) {
            console.error("live session revoke failed", error);
            setLiveNotice("Failed to revoke live session.", true);
            liveRevokeButton.disabled = false;
          }
        });
      }

      const pauseButton = document.getElementById("queue-live-pause");
      if (pauseButton) {
        pauseButton.addEventListener("click", async () => {
          pauseButton.disabled = true;
          const action = pauseButton.textContent === "Resume" ? "resume" : "pause";
          setLiveNotice(action === "pause" ? "Pausing worker..." : "Resuming worker...");
          try {
            await fetchJson(
              endpoint(queueSourceConfig.taskControl || "/api/queue/jobs/{id}/control", {
                id: jobId,
              }),
              {
                method: "POST",
                body: JSON.stringify({ action }),
              },
            );
            await loadDetail();
            await loadEvents();
            setLiveNotice(action === "pause" ? "Pause requested." : "Resume requested.");
          } catch (error) {
            console.error("task control action failed", error);
            setLiveNotice("Failed to apply control action.", true);
            pauseButton.disabled = false;
          }
        });
      }

      const takeoverButton = document.getElementById("queue-live-takeover");
      if (takeoverButton) {
        takeoverButton.addEventListener("click", async () => {
          takeoverButton.disabled = true;
          setLiveNotice("Requesting takeover...");
          try {
            await fetchJson(
              endpoint(queueSourceConfig.taskControl || "/api/queue/jobs/{id}/control", {
                id: jobId,
              }),
              {
                method: "POST",
                body: JSON.stringify({ action: "takeover" }),
              },
            );
            await loadDetail();
            await loadEvents();
            setLiveNotice("Takeover requested.");
          } catch (error) {
            console.error("task takeover action failed", error);
            setLiveNotice("Failed to request takeover.", true);
            takeoverButton.disabled = false;
          }
        });
      }

      const operatorMessageInput = document.getElementById("queue-operator-message");
      const operatorSendButton = document.getElementById("queue-operator-send");
      if (operatorMessageInput && operatorSendButton) {
        operatorSendButton.addEventListener("click", async () => {
          const messageText = String(operatorMessageInput.value || "").trim();
          if (!messageText) {
            return;
          }
          operatorSendButton.disabled = true;
          setLiveNotice("Sending operator message...");
          try {
            await fetchJson(
              endpoint(
                queueSourceConfig.operatorMessages ||
                  "/api/queue/jobs/{id}/operator-messages",
                { id: jobId },
              ),
              {
                method: "POST",
                body: JSON.stringify({ message: messageText }),
              },
            );
            operatorMessageInput.value = "";
            await loadEvents();
            setLiveNotice("Operator message sent.");
          } catch (error) {
            console.error("operator message failed", error);
            setLiveNotice("Failed to send operator message.", true);
          } finally {
            operatorSendButton.disabled = false;
          }
        });
      }

      const followOutput = document.getElementById("queue-follow-output");
      if (followOutput) {
        followOutput.addEventListener("change", () => {
          state.followOutput = Boolean(followOutput.checked);
          if (state.followOutput) {
            const outputNode = document.getElementById("queue-live-output");
            if (outputNode) {
              outputNode.scrollTop = outputNode.scrollHeight;
            }
          }
        });
      }

      const outputFilter = document.getElementById("queue-output-filter");
      if (outputFilter) {
        outputFilter.addEventListener("change", () => {
          state.outputFilter = String(outputFilter.value || "all");
          render(null);
        });
      }

      const copyOutput = document.getElementById("queue-copy-output");
      if (copyOutput) {
        copyOutput.addEventListener("click", async () => {
          const content = buildLiveOutput(state.events, state.outputFilter);
          if (!content) {
            return;
          }
          try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
              await navigator.clipboard.writeText(content);
            }
          } catch (error) {
            console.error("copy live output failed", error);
          }
        });
      }

      const outputNode = document.getElementById("queue-live-output");
      if (outputNode && state.followOutput) {
        outputNode.scrollTop = outputNode.scrollHeight;
      }
    };

    const loadDetail = async () => {
      try {
        const [job, artifactsPayload] = await Promise.all([
          fetchJson(endpoint("/api/queue/jobs/{id}", { id: jobId })),
          fetchJson(endpoint("/api/queue/jobs/{id}/artifacts", { id: jobId })),
        ]);
        let liveSession = null;
        let liveSessionError = null;
        let liveSessionRouteMissing = false;
        try {
          const livePayload = await fetchJson(
            endpoint(
              queueSourceConfig.liveSession || "/api/queue/jobs/{id}/live-session",
              { id: jobId },
            ),
          );
          liveSession = pick(livePayload || {}, "session") || null;
        } catch (error) {
          const classification = classifyLiveSessionError(error);
          if (classification === "route_missing") {
            liveSessionRouteMissing = true;
            liveSessionError =
              "Live session API is unavailable on this deployment. Verify queue live-session routes are exposed.";
          } else if (classification === "other") {
            const message = String(error?.message || "");
            liveSessionError = message || "Live session unavailable.";
          }
        }
        state.job = job;
        state.artifacts = artifactsPayload?.items || [];
        state.liveSession = liveSession;
        state.liveSessionError = liveSessionError;
        state.liveSessionRouteMissing = liveSessionRouteMissing;
        render(null);
      } catch (error) {
        console.error("queue detail load failed", error);
        state.job = null;
        state.artifacts = [];
        state.liveSession = null;
        state.liveSessionError = null;
        state.liveSessionRouteMissing = false;
        render("Failed to load queue detail.");
      }
    };

    const loadEvents = async () => {
      const queryParams = ["limit=200"];
      if (state.after) {
        queryParams.push(`after=${encodeURIComponent(state.after)}`);
      }
      if (state.afterEventId) {
        queryParams.push(`afterEventId=${encodeURIComponent(state.afterEventId)}`);
      }
      const query = `?${queryParams.join("&")}`;
      try {
        const payload = await fetchJson(
          endpoint(queueSourceConfig.events || "/api/queue/jobs/{id}/events", { id: jobId }) +
            query,
        );
        if (appendIncomingEvents(payload?.items || [])) {
          render(null);
        }
      } catch (error) {
        console.error("queue event poll failed", error);
      }
    };

    const beginPollingEvents = () => {
      if (state.eventsPollingStarted) {
        return;
      }
      state.eventsPollingStarted = true;
      state.eventsTransport = "polling";
      state.eventsTransportStatus = "active";
      render(null);
      startPolling(loadEvents, pollIntervals.events);
    };

    const startEventStream = () => {
      const streamTemplate =
        queueSourceConfig.eventsStream || "/api/queue/jobs/{id}/events/stream";
      if (typeof window.EventSource !== "function") {
        state.eventsTransport = "polling";
        state.eventsTransportStatus = "unsupported";
        beginPollingEvents();
        return;
      }

      const queryParams = ["limit=200"];
      if (state.after) {
        queryParams.push(`after=${encodeURIComponent(state.after)}`);
      }
      if (state.afterEventId) {
        queryParams.push(`afterEventId=${encodeURIComponent(state.afterEventId)}`);
      }
      const query = `?${queryParams.join("&")}`;
      const streamUrl = endpoint(streamTemplate, { id: jobId }) + query;
      state.eventsTransport = "sse";
      state.eventsTransportStatus = "connecting";
      render(null);

      const source = new window.EventSource(streamUrl);
      registerDisposer(() => source.close());

      const handleMessage = (rawData) => {
        if (!rawData) {
          return;
        }
        try {
          const parsed = JSON.parse(rawData);
          if (appendIncomingEvents([parsed])) {
            render(null);
          }
        } catch (error) {
          console.error("queue event stream parse failed", error);
        }
      };

      source.addEventListener("open", () => {
        state.eventsTransport = "sse";
        state.eventsTransportStatus = "active";
        render(null);
      });

      source.addEventListener("queue_event", (event) => {
        state.eventsTransport = "sse";
        state.eventsTransportStatus = "active";
        handleMessage(event.data);
      });

      source.onmessage = (event) => {
        state.eventsTransport = "sse";
        state.eventsTransportStatus = "active";
        handleMessage(event.data);
      };

      source.onerror = (error) => {
        console.error("queue event stream failed; switching to polling", error);
        state.eventsTransport = "polling";
        state.eventsTransportStatus = "error";
        render(null);
        source.close();
        beginPollingEvents();
      };
    };

    await loadDetail();
    await loadEvents();
    startPolling(loadDetail, pollIntervals.detail);
    startEventStream();
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
        console.error("orchestrator detail load failed", error);
        setView(
          "Orchestrator Run Detail",
          `Run ${runId}`,
          "<div class='notice error'>Failed to load run detail.</div>",
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
    if (pathname === "/tasks/orchestrator") {
      await renderOrchestratorListPage();
      return;
    }

    if (pathname === "/tasks/queue/new") {
      renderQueueSubmitPage();
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
      "<div class='notice error'>Unexpected dashboard rendering failure.</div>",
    );
  });
})();
