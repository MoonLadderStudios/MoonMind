(() => {
  function describeWorkerPauseState(system = {}, metrics = {}) {
    const workersPaused = Boolean(system.workersPaused);
    const mode = (system.mode || "").toString().toLowerCase();
    const state = workersPaused
      ? mode === "quiesce"
        ? "quiesce"
        : "paused"
      : "running";
    const label = workersPaused
      ? `Workers: Paused (${mode === "quiesce" ? "Quiesce" : "Drain"})`
      : "Workers: Running";
    let reasonText = "Workers are accepting new jobs.";
    if (workersPaused) {
      reasonText = system.reason || "Paused without operator reason.";
    } else if (system.reason) {
      reasonText = `Last action: ${system.reason}`;
    }
    return {
      label,
      reason: reasonText,
      state,
      drained: metrics.isDrained ? "Yes" : "No",
    };
  }

  function requiresResumeConfirmation(snapshot) {
    return Boolean(
      snapshot &&
        snapshot.metrics &&
        Object.prototype.hasOwnProperty.call(snapshot.metrics, "isDrained") &&
        !snapshot.metrics.isDrained,
    );
  }

  function createWorkerPauseTransport(workerPauseSettings) {
    if (
      !workerPauseSettings ||
      typeof workerPauseSettings.get !== "string" ||
      typeof workerPauseSettings.post !== "string"
    ) {
      return null;
    }

    const pollInterval = Math.max(
      1000,
      Number(workerPauseSettings.pollIntervalMs) || 5000,
    );
    const getEndpoint = workerPauseSettings.get;
    const postEndpoint = workerPauseSettings.post;

    async function parseJsonBody(response, contextLabel) {
      try {
        return await response.json();
      } catch (error) {
        console.warn(`${contextLabel} response was not valid JSON`, {
          status: response.status,
          name: errorNameForLog(error),
        });
        return null;
      }
    }

    function messageFromDetail(detail) {
      if (!detail) {
        return "";
      }
      if (typeof detail === "string") {
        return detail;
      }
      if (typeof detail === "object" && typeof detail.message === "string") {
        return detail.message;
      }
      return "";
    }

    async function fetchState() {
      const response = await fetch(getEndpoint, {
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      const responseBody = await parseJsonBody(response, "worker pause status");
      if (!response.ok) {
        const detailMessage = messageFromDetail(responseBody && responseBody.detail).trim();
        if (detailMessage && (response.status === 401 || response.status === 403)) {
          throw new Error(detailMessage);
        }
        throw new Error("Unable to load worker pause status.");
      }
      return responseBody || {};
    }

    async function submitAction(payload) {
      const response = await fetch(postEndpoint, {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });
      const responseBody = await parseJsonBody(response, "worker pause update");
      if (!response.ok) {
        throw new Error(response.statusText || "Failed to update worker pause state.");
      }
      return responseBody || {};
    }

    return {
      pollInterval,
      fetchState,
      submitAction,
    };
  }

  if (typeof window !== "undefined") {
    window.__workerPauseTest = {
      describeWorkerPauseState,
      requiresResumeConfirmation,
    };
  }

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
  const proposalsSourceConfig =
    sourceConfig.proposals && typeof sourceConfig.proposals === "object"
      ? sourceConfig.proposals
      : {};
  const manifestsSourceConfig =
    sourceConfig.manifests && typeof sourceConfig.manifests === "object"
      ? sourceConfig.manifests
      : {};
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
  const defaultPublishMode =
    normalizePublishModeInput(systemConfig.defaultPublishMode) || "pr";
  const ownerRepoPattern = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
  const taskTemplateCatalogConfig =
    systemConfig.taskTemplateCatalog &&
    typeof systemConfig.taskTemplateCatalog === "object" &&
    !Array.isArray(systemConfig.taskTemplateCatalog)
      ? systemConfig.taskTemplateCatalog
      : {};
  const taskTemplateCatalogEnabled = Boolean(taskTemplateCatalogConfig.enabled);
  const taskTemplateSaveEnabled = Boolean(taskTemplateCatalogConfig.templateSaveEnabled);
  const workerPauseConfig =
    systemConfig.workerPause &&
    typeof systemConfig.workerPause === "object" &&
    !Array.isArray(systemConfig.workerPause)
      ? systemConfig.workerPause
      : null;
  const workerPauseTransport = createWorkerPauseTransport(workerPauseConfig);
  const taskTemplateEndpoints = {
    list: String(queueSourceConfig.taskStepTemplates || "/api/task-step-templates"),
    detail: String(
      queueSourceConfig.taskStepTemplateDetail || "/api/task-step-templates/{slug}",
    ),
    expand: String(
      queueSourceConfig.taskStepTemplateExpand || "/api/task-step-templates/{slug}:expand",
    ),
    save: String(
      queueSourceConfig.taskStepTemplateSave || "/api/task-step-templates/save-from-task",
    ),
    favorite: String(
      queueSourceConfig.taskStepTemplateFavorite || "/api/task-step-templates/{slug}:favorite",
    ),
  };
  const THEME_STORAGE_KEY = "moonmind.theme";
  const THEME_DARK_CLASS = "dark";
  const THEME_MEDIA_QUERY = "(prefers-color-scheme: dark)";

  const TASK_LIST_TITLE_MAX_CHARS = 400;
  const pollers = [];
  const disposers = [];
  const persistentPollers = [];
  const persistentDisposers = [];
  let cachedAvailableSkillIds = null;
  const AUTO_REFRESH_STORAGE_KEY = "moonmind.tasks.autoRefresh";
  const autoRefreshChangeListeners = new Set();

  function errorNameForLog(error) {
    if (!error || typeof error !== "object") {
      return "unknown";
    }
    const name = error.name;
    return name ? String(name) : "unknown";
  }

  function readStoredAutoRefreshPreference() {
    try {
      const raw = window.localStorage
        ? window.localStorage.getItem(AUTO_REFRESH_STORAGE_KEY)
        : null;
      if (raw === "paused") {
        return true;
      }
      if (raw && raw !== "active" && window.localStorage) {
        window.localStorage.removeItem(AUTO_REFRESH_STORAGE_KEY);
      }
    } catch (error) {
      console.warn("auto refresh preference read failed", {
        name: errorNameForLog(error),
      });
    }
    return false;
  }

  function persistAutoRefreshPreference(paused) {
    try {
      if (!window.localStorage) {
        return;
      }
      if (paused) {
        window.localStorage.setItem(AUTO_REFRESH_STORAGE_KEY, "paused");
      } else {
        window.localStorage.setItem(AUTO_REFRESH_STORAGE_KEY, "active");
      }
    } catch (error) {
      console.warn("auto refresh preference persist failed", {
        name: errorNameForLog(error),
      });
    }
  }

  let autoRefreshPaused = readStoredAutoRefreshPreference();

  function isAutoRefreshActive() {
    return !autoRefreshPaused;
  }

  function notifyAutoRefreshListeners() {
    const enabled = isAutoRefreshActive();
    autoRefreshChangeListeners.forEach((listener) => {
      try {
        listener(enabled);
      } catch (error) {
        console.error("auto refresh listener failed", {
          name: errorNameForLog(error),
        });
      }
    });
  }

  function onAutoRefreshChange(listener) {
    if (typeof listener !== "function") {
      console.warn("onAutoRefreshChange requires a function listener");
      return () => {};
    }
    autoRefreshChangeListeners.add(listener);
    return () => {
      autoRefreshChangeListeners.delete(listener);
    };
  }

  function setAutoRefreshPaused(nextPaused) {
    const normalized = Boolean(nextPaused);
    if (normalized === autoRefreshPaused) {
      return;
    }
    autoRefreshPaused = normalized;
    persistAutoRefreshPreference(normalized);
    syncAutoRefreshControls();
    notifyAutoRefreshListeners();
  }

  function renderAutoRefreshControls() {
    return `
      <div class="toolbar-controls">
        <label class="queue-inline-toggle toolbar-live-toggle">
          <input type="checkbox" data-auto-refresh-toggle ${
            isAutoRefreshActive() ? "checked" : ""
          } aria-pressed="${isAutoRefreshActive() ? "true" : "false"}" />
          Live updates
        </label>
        <span class="small" data-auto-refresh-status>${
          isAutoRefreshActive() ? "" : "Updates paused to keep selections stable."
        }</span>
      </div>
    `;
  }

  function syncAutoRefreshControls() {
    const toggleNodes = root.querySelectorAll("[data-auto-refresh-toggle]");
    toggleNodes.forEach((node) => {
      if (node instanceof HTMLInputElement) {
        node.checked = isAutoRefreshActive();
        node.setAttribute(
          "aria-label",
          isAutoRefreshActive() ? "Disable live updates" : "Enable live updates",
        );
        node.setAttribute("aria-pressed", isAutoRefreshActive() ? "true" : "false");
      }
    });
    const statusNodes = root.querySelectorAll("[data-auto-refresh-status]");
    statusNodes.forEach((node) => {
      node.textContent = isAutoRefreshActive()
        ? ""
        : "Updates paused to keep selections stable.";
    });
  }

  function bindAutoRefreshControls() {
    const toggleNodes = root.querySelectorAll("[data-auto-refresh-toggle]");
    toggleNodes.forEach((node) => {
      if (node instanceof HTMLInputElement) {
        node.addEventListener("change", () => {
          setAutoRefreshPaused(!node.checked);
        });
      }
    });
  }

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

  function stopPersistentPolling() {
    while (persistentPollers.length > 0) {
      clearInterval(persistentPollers.pop());
    }
    while (persistentDisposers.length > 0) {
      const dispose = persistentDisposers.pop();
      if (typeof dispose === "function") {
        try {
          dispose();
        } catch (error) {
          console.error("persistent polling disposer failed", error);
        }
      }
    }
  }

  function startPolling(task, intervalMs, options = {}) {
    const runImmediately = options.runImmediately !== false;
    const skipAutoRefresh = options.skipAutoRefresh === true;
    const persistent = options.persistent === true;
    const run = (forced = false) => {
      if (!forced) {
        if (!skipAutoRefresh && !isAutoRefreshActive()) {
          return;
        }
        if (document.visibilityState === "hidden") {
          return;
        }
      }
      task().catch((error) => {
        console.error("polling task failed", error);
      });
    };

    if (runImmediately) {
      run(true);
    }
    const timer = window.setInterval(() => run(false), intervalMs);
    (persistent ? persistentPollers : pollers).push(timer);
    if (!skipAutoRefresh) {
      const disposeAutoRefreshListener = onAutoRefreshChange((enabled) => {
        if (enabled) {
          run(true);
        }
      });
      registerDisposer(() => disposeAutoRefreshListener(), { persistent });
    }
  }

  function registerDisposer(disposer, options = {}) {
    if (typeof disposer !== "function") {
      return;
    }
    if (options.persistent === true) {
      persistentDisposers.push(disposer);
      return;
    }
    disposers.push(disposer);
  }

  function initWorkerPauseBanner(workerPauseTransport) {
    if (!workerPauseTransport) {
      return null;
    }
    const section = document.querySelector("[data-worker-pause]");
    if (!section) {
      return null;
    }

    const statusNode = section.querySelector("[data-worker-pause-status]");
    const reasonNode = section.querySelector("[data-worker-pause-reason]");
    let latestSnapshot = null;

    function render(snapshot) {
      const system = snapshot.system || {};
      const metrics = snapshot.metrics || {};
      const description = describeWorkerPauseState(system, metrics);
      const isPaused = Boolean(system.workersPaused);
      section.dataset.state = description.state;
      if (!isPaused) {
        section.hidden = true;
        if (statusNode) {
          statusNode.textContent = "Workers: Running";
        }
        if (reasonNode) {
          reasonNode.textContent = "";
        }
        return;
      }
      section.hidden = false;
      if (statusNode) {
        if (description.label.startsWith("Workers:")) {
          const normalized = description.label.replace("Workers:", "Workers").trim();
          statusNode.textContent = `⚠️ ${normalized}`;
        } else {
          statusNode.textContent = `⚠️ ${description.label}`;
        }
      }
      if (reasonNode) {
        const rawReason = system.reason || description.reason || "";
        reasonNode.textContent = rawReason ? `- "${rawReason}"` : "";
      }
      latestSnapshot = snapshot;
    }

    async function refresh() {
      try {
        const snapshot = await workerPauseTransport.fetchState();
        render(snapshot);
      } catch (error) {
        console.error("worker pause banner refresh failed", error);
      }
    }

    return {
      pollInterval: workerPauseTransport.pollInterval,
      refresh,
      getLatestSnapshot() {
        return latestSnapshot;
      },
    };
  }

  function readStoredThemePreference() {
    try {
      const raw = window.localStorage ? window.localStorage.getItem(THEME_STORAGE_KEY) : null;
      if (raw === "dark" || raw === "light") {
        return raw;
      }
      if (raw && window.localStorage) {
        window.localStorage.removeItem(THEME_STORAGE_KEY);
      }
    } catch (_error) {
      // Ignore storage access failures and fall back to system preference.
    }
    return null;
  }

  function persistThemePreference(mode) {
    try {
      if (window.localStorage) {
        window.localStorage.setItem(THEME_STORAGE_KEY, mode);
      }
    } catch (_error) {
      // Ignore storage failures and keep in-memory mode.
    }
  }

  function getSystemThemeMode(mediaQueryList = null) {
    if (mediaQueryList && typeof mediaQueryList.matches === "boolean") {
      return mediaQueryList.matches ? "dark" : "light";
    }
    if (window.matchMedia) {
      return window.matchMedia(THEME_MEDIA_QUERY).matches ? "dark" : "light";
    }
    return "light";
  }

  function syncThemeToggle(mode, source) {
    const toggle = document.querySelector(".theme-toggle");
    if (!toggle) {
      return;
    }
    const titleCaseMode = mode === "dark" ? "Dark" : "Light";
    toggle.textContent = `Theme: ${titleCaseMode}`;
    toggle.setAttribute("aria-label", mode === "dark" ? "Switch to light mode" : "Switch to dark mode");
    toggle.setAttribute("aria-pressed", mode === "dark" ? "true" : "false");
    toggle.dataset.themeSource = source;
  }

  function applyResolvedTheme(mode, source) {
    const resolvedMode = mode === "dark" ? "dark" : "light";
    const documentRoot = document.documentElement;
    documentRoot.classList.toggle(THEME_DARK_CLASS, resolvedMode === "dark");
    documentRoot.dataset.theme = resolvedMode;
    documentRoot.dataset.themeSource = source;
    syncThemeToggle(resolvedMode, source);
    return resolvedMode;
  }

  function initTheme() {
    const mediaQueryList = window.matchMedia ? window.matchMedia(THEME_MEDIA_QUERY) : null;
    let storedPreference = readStoredThemePreference();
    let currentMode = applyResolvedTheme(
      storedPreference || getSystemThemeMode(mediaQueryList),
      storedPreference ? "user" : "system",
    );

    const toggle = document.querySelector(".theme-toggle");
    const handleToggleClick = () => {
      const nextMode = currentMode === "dark" ? "light" : "dark";
      storedPreference = nextMode;
      persistThemePreference(nextMode);
      currentMode = applyResolvedTheme(nextMode, "user");
    };

    if (toggle) {
      toggle.addEventListener("click", handleToggleClick);
    }

    const handleSystemPreferenceChange = (event) => {
      if (storedPreference) {
        return;
      }
      currentMode = applyResolvedTheme(event.matches ? "dark" : "light", "system");
    };

    if (mediaQueryList && typeof mediaQueryList.addEventListener === "function") {
      mediaQueryList.addEventListener("change", handleSystemPreferenceChange);
    }

    return () => {
      if (toggle) {
        toggle.removeEventListener("click", handleToggleClick);
      }
      if (mediaQueryList && typeof mediaQueryList.removeEventListener === "function") {
        mediaQueryList.removeEventListener("change", handleSystemPreferenceChange);
      }
    };
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

  function sanitizeCssToken(value, fallback = "") {
    const token = String(value ?? "")
      .toLowerCase()
      .replaceAll(/[^a-z0-9_-]/g, "");
    return token || fallback;
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

  function normalizePublishModeInput(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (!normalized) {
      return "";
    }
    return ["none", "branch", "pr"].includes(normalized) ? normalized : "";
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
    const statusClassToken = sanitizeCssToken(normalized, "queued");
    return `<span class="status status-${statusClassToken}">${escapeHtml(normalized)}</span>`;
  }

  function endpoint(template, replacements) {
    let resolved = template;
    Object.entries(replacements).forEach(([key, value]) => {
      resolved = resolved.replace(`{${key}}`, encodeURIComponent(String(value)));
    });
    return resolved;
  }

  function withQueueSummaryFlag(url) {
    if (!url || typeof url !== "string") {
      return url;
    }
    if (/[?&]summary=/.test(url)) {
      return url;
    }
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}summary=true`;
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
        ${renderAutoRefreshControls()}
      </div>
      ${body}
    `;
    bindAutoRefreshControls();
    syncAutoRefreshControls();
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
    if (typeof value !== "string") {
      return "";
    }
    const raw = String(value ?? "")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
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
    const safeCut = lastSpace > 0 ? truncated.slice(0, lastSpace) : truncated;
    return `${safeCut.trimEnd()}...`;
  }

  function toQueueRows(items) {
    return items.map((item) => {
      const payload = pick(item, "payload") || {};
      const task = extractTaskNode(payload);
      const taskInstructions = task ? pick(task, "instructions") : undefined;
      const payloadInstruction = pick(payload, "instruction");
      const rawInstructions =
        (typeof taskInstructions === "string" && taskInstructions) ||
        (typeof payloadInstruction === "string" && payloadInstruction) ||
        "";
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

  async function apiPromoteProposal(proposalId, overrides = {}) {
    const endpointTemplate =
      proposalsSourceConfig.promote || "/api/proposals/{id}/promote";
    return fetchJson(endpoint(endpointTemplate, { id: proposalId }), {
      method: "POST",
      body: JSON.stringify(overrides),
    });
  }

  async function apiDismissProposal(proposalId, note = null) {
    const endpointTemplate =
      proposalsSourceConfig.dismiss || "/api/proposals/{id}/dismiss";
    const body = {};
    if (note) {
      body.note = note;
    }
    return fetchJson(endpoint(endpointTemplate, { id: proposalId }), {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  async function apiUpdateProposalPriority(proposalId, priority) {
    const endpointTemplate =
      proposalsSourceConfig.priority || "/api/proposals/{id}/priority";
    return fetchJson(endpoint(endpointTemplate, { id: proposalId }), {
      method: "POST",
      body: JSON.stringify({ priority }),
    });
  }

  async function apiSnoozeProposal(proposalId, until, note = null) {
    const endpointTemplate =
      proposalsSourceConfig.snooze || "/api/proposals/{id}/snooze";
    const payload = { until };
    if (note) {
      payload.note = note;
    }
    return fetchJson(endpoint(endpointTemplate, { id: proposalId }), {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async function apiUnsnoozeProposal(proposalId) {
    const endpointTemplate =
      proposalsSourceConfig.unsnooze || "/api/proposals/{id}/unsnooze";
    return fetchJson(endpoint(endpointTemplate, { id: proposalId }), {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  function cloneTaskRequest(node) {
    try {
      return JSON.parse(JSON.stringify(node || {}));
    } catch {
      return {};
    }
  }

  function buildEditOverrides(row) {
    const baseRequest = cloneTaskRequest(pick(row, "taskCreateRequest") || {});
    const payload =
      baseRequest.payload && typeof baseRequest.payload === "object"
        ? baseRequest.payload
        : {};
    baseRequest.payload = payload;
    const taskNode =
      payload.task && typeof payload.task === "object" ? payload.task : {};
    payload.task = taskNode;
    const publishNode =
      taskNode.publish && typeof taskNode.publish === "object"
        ? taskNode.publish
        : {};
    taskNode.publish = publishNode;
    const gitNode =
      taskNode.git && typeof taskNode.git === "object" ? taskNode.git : {};
    taskNode.git = gitNode;

    const currentInstructions =
      taskNode.instructions || pick(row, "summary") || "";
    const updatedInstructions = window.prompt(
      "Task instructions",
      currentInstructions,
    );
    if (updatedInstructions === null) {
      return null;
    }
    taskNode.instructions = updatedInstructions;
    const publishMode = window.prompt(
      "Publish mode (branch/pr/none)",
      publishNode.mode || defaultPublishMode,
    );
    if (publishMode === null) {
      return null;
    }
    publishNode.mode = publishMode || publishNode.mode || defaultPublishMode;
    const startingBranch = window.prompt(
      "Starting branch (leave blank to keep current)",
      gitNode.startingBranch || "",
    );
    if (startingBranch === null) {
      return null;
    }
    gitNode.startingBranch = startingBranch || null;

    const queuePriority = window.prompt(
      "Queue priority",
      String(baseRequest.priority ?? 0),
    );
    if (queuePriority === null) {
      return null;
    }
    const maxAttempts = window.prompt(
      "Max attempts",
      String(baseRequest.maxAttempts ?? 3),
    );
    if (maxAttempts === null) {
      return null;
    }
    const parsedPriority = Number(queuePriority);
    const parsedAttempts = Number(maxAttempts);
    baseRequest.priority = Number.isFinite(parsedPriority)
      ? parsedPriority
      : baseRequest.priority;
    baseRequest.maxAttempts = Number.isFinite(parsedAttempts)
      ? parsedAttempts
      : baseRequest.maxAttempts;

    return {
      priority: baseRequest.priority,
      maxAttempts: baseRequest.maxAttempts,
      taskCreateRequestOverride: baseRequest,
    };
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
          call: () =>
            fetchJson(withQueueSummaryFlag("/api/queue/jobs?status=running&limit=200")),
          transform: (payload) => toQueueRows(payload?.items || []),
        },
        {
          source: "queue-queued",
          call: () =>
            fetchJson(withQueueSummaryFlag("/api/queue/jobs?status=queued&limit=200")),
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
    const telemetryEndpoint =
      (queueSourceConfig.migrationTelemetry || "/api/queue/telemetry/migration") +
      "?windowHours=168";
    const telemetryRefreshMs = Math.max(
      60000,
      Math.max(1000, Number(pollIntervals.list) || 5000) * 12,
    );
    let telemetryPayload = null;
    let telemetryInFlight = null;
    let telemetryLastRequestedAt = 0;
    let currentRows = [];
    let pageActive = true;
    registerDisposer(() => {
      pageActive = false;
    });

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
            extractPublishModeFromPayload(row.payload || {}) || defaultPublishMode;
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
      const totalJobs = Number(pick(snapshot, "totalJobs") || 0);
      const publishedRate = Number(pick(publish, "publishedRate") || 0);
      const failedRate = Number(pick(publish, "failedRate") || 0);
      return `
        <div class="grid-2">
          <div class="card"><strong>Total Jobs (Window):</strong> ${escapeHtml(totalJobs)}</div>
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

    function renderQueueList(rows) {
      if (!pageActive) {
        return;
      }
      const filteredRows = applyQueueFilters(rows);
      const telemetryHtml = renderTelemetrySummary(telemetryPayload);
      setView(
        "Queue Jobs",
        `All queue jobs ordered by creation time. Unified queue: ${defaultQueueName}.`,
        `${telemetryHtml}${renderQueueFilters()}${renderRowsTable(filteredRows)}`,
      );
      attachFilterHandlers(rows);
    }

    function attachFilterHandlers(rows) {
      const filterForm = document.getElementById("queue-filter-form");
      if (!filterForm) {
        return;
      }
      const runtimeField = filterForm.elements.namedItem("runtime");
      const skillField = filterForm.elements.namedItem("skill");
      const stageField = filterForm.elements.namedItem("stageStatus");
      const publishField = filterForm.elements.namedItem("publishMode");

      const rerender = () => {
        renderQueueList(rows);
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

    async function refreshTelemetryIfStale() {
      if (!pageActive) {
        return;
      }
      const now = Date.now();
      if (telemetryInFlight) {
        return telemetryInFlight;
      }
      if (now - telemetryLastRequestedAt < telemetryRefreshMs) {
        return;
      }

      telemetryLastRequestedAt = now;
      telemetryInFlight = (async () => {
        try {
          const nextPayload = await fetchJson(telemetryEndpoint);
          if (!pageActive) {
            return;
          }
          telemetryPayload = nextPayload;
          renderQueueList(currentRows);
        } catch (error) {
          console.warn("queue migration telemetry load failed");
        } finally {
          telemetryInFlight = null;
        }
      })();
      return telemetryInFlight;
    }

    const load = async () => {
      const payload = await fetchJson(withQueueSummaryFlag("/api/queue/jobs?limit=200"));
      if (!pageActive) {
        return;
      }
      currentRows = sortRows(toQueueRows(payload?.items || []));
      renderQueueList(currentRows);
      refreshTelemetryIfStale().catch(() => {
        console.warn("queue telemetry refresh failed");
      });
    };

    startPolling(load, pollIntervals.list);
  }

  async function renderManifestListPage() {
    setView(
      "Manifest Runs",
      "All manifest ingestion jobs (type=manifest).",
      "<p class='loading'>Loading manifest jobs...</p>",
    );

    const load = async () => {
      const endpoint =
        manifestsSourceConfig.list ||
        "/api/queue/jobs?type=manifest&limit=200";
      const payload = await fetchJson(withQueueSummaryFlag(endpoint));
      const rows = sortRows(
        toQueueRows(payload?.items || []).map((row) => ({
          ...row,
          source: "manifests",
          sourceLabel: "Manifests",
        })),
      );
      setView(
        "Manifest Runs",
        "All manifest ingestion jobs (type=manifest).",
        `<div class="actions"><a href="/tasks/manifests/new"><button type="button">New Manifest Run</button></a></div>${renderRowsTable(rows)}`,
      );
    };

    startPolling(load, pollIntervals.list);
  }

  function renderManifestSubmitPage() {
    setView(
      "Submit Manifest Run",
      "Queue a manifest ingestion job via the shared Agent Queue.",
      `
      <form id="manifest-submit-form">
        <label>Manifest Name
          <input name="manifestName" required />
        </label>
        <div class="grid-2">
          <label>Action
            <select name="action">
              <option value="run" selected>run</option>
              <option value="plan">plan</option>
            </select>
          </label>
          <label>Source Type
            <select name="sourceKind">
              <option value="inline" selected>Inline YAML</option>
              <option value="registry">Registry</option>
            </select>
          </label>
        </div>
        <label id="manifest-inline-field">
          Manifest YAML
          <textarea name="manifestContent" placeholder="Paste manifest YAML..." rows="12" required></textarea>
        </label>
        <label id="manifest-registry-field" class="hidden">
          Registry Name
          <input name="registryName" placeholder="existing manifest name" />
        </label>
        <div class="grid-3">
          <label class="checkbox">
            <input type="checkbox" name="dryRun" />
            Dry Run
          </label>
          <label class="checkbox">
            <input type="checkbox" name="forceFull" />
            Force Full Sync
          </label>
          <label>Max Docs
            <input type="number" min="1" name="maxDocs" placeholder="optional" />
          </label>
        </div>
        <label>Queue Priority
          <input type="number" name="priority" value="0" />
        </label>
        <div class="actions">
          <button type="submit">Create Manifest Job</button>
          <a href="/tasks/manifests"><button class="secondary" type="button">Cancel</button></a>
        </div>
        <p class="small" id="manifest-submit-message"></p>
      </form>
      `,
    );

    const form = document.getElementById("manifest-submit-form");
    const message = document.getElementById("manifest-submit-message");
    const inlineField = document.getElementById("manifest-inline-field");
    const registryField = document.getElementById("manifest-registry-field");
    const sourceSelect = form.querySelector('select[name="sourceKind"]');

    const syncSourceFields = () => {
      const kind = String(sourceSelect.value || "inline").toLowerCase();
      inlineField.classList.toggle("hidden", kind !== "inline");
      registryField.classList.toggle("hidden", kind !== "registry");
      const contentField = form.elements.namedItem("manifestContent");
      const registryInput = form.elements.namedItem("registryName");
      if (contentField) {
        contentField.required = kind === "inline";
      }
      if (registryInput) {
        registryInput.required = kind === "registry";
      }
    };
    syncSourceFields();
    sourceSelect.addEventListener("change", syncSourceFields);

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      message.textContent = "";

      const formData = new FormData(form);
      const manifestName = String(formData.get("manifestName") || "").trim();
      const action = String(formData.get("action") || "run").trim() || "run";
      const sourceKind = String(formData.get("sourceKind") || "inline").trim().toLowerCase();
      const manifestPayload = {
        manifest: {
          name: manifestName,
          action,
          source: { kind: sourceKind },
        },
      };

      if (!manifestName) {
        message.textContent = "Manifest name is required.";
        return;
      }

      if (sourceKind === "registry") {
        const registryName = String(formData.get("registryName") || "").trim();
        if (!registryName) {
          message.textContent = "Registry name is required for registry submissions.";
          return;
        }
        manifestPayload.manifest.source = { kind: "registry", name: registryName };
      } else {
        const manifestContent = String(formData.get("manifestContent") || "").trim();
        if (!manifestContent) {
          message.textContent = "Manifest YAML is required.";
          return;
        }
        manifestPayload.manifest.source = { kind: "inline", content: manifestContent };
      }

      const options = {};
      if (formData.get("dryRun")) {
        options.dryRun = true;
      }
      if (formData.get("forceFull")) {
        options.forceFull = true;
      }
      const maxDocsRaw = String(formData.get("maxDocs") || "").trim();
      if (maxDocsRaw) {
        const parsed = Number(maxDocsRaw);
        if (Number.isFinite(parsed) && parsed >= 1) {
          options.maxDocs = parsed;
        }
      }
      if (Object.keys(options).length > 0) {
        manifestPayload.manifest.options = options;
      }

      const priorityValue = Number(String(formData.get("priority") || "").trim());
      const priority = Number.isFinite(priorityValue) ? priorityValue : 0;

      try {
        let created;
        if (sourceKind === "registry") {
          const registryName = String(formData.get("registryName") || "").trim();
          const registryRunUrlTemplate =
            queueSourceConfig.registryRun || "/api/manifests/{name}/runs";
          const registryRunUrl = registryRunUrlTemplate.replace(
            "{name}",
            encodeURIComponent(registryName),
          );
          created = await fetchJson(registryRunUrl, {
            method: "POST",
            body: JSON.stringify({
              action,
              options: Object.keys(options).length > 0 ? options : undefined,
            }),
          });
          window.location.href = `/tasks/queue/${encodeURIComponent(
            String(created.jobId || ""),
          )}`;
          return;
        }

        created = await fetchJson(queueSourceConfig.create || "/api/queue/jobs", {
          method: "POST",
          body: JSON.stringify({
            type: "manifest",
            priority,
            payload: manifestPayload,
          }),
        });
        window.location.href = `/tasks/queue/${encodeURIComponent(
          String(created.id || ""),
        )}`;
      } catch (error) {
        console.error("manifest submit failed", error);
        message.textContent = "Failed to create manifest job.";
      }
    });
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
    const templateControlsHtml = taskTemplateCatalogEnabled
      ? `
        <div class="card">
          <div class="actions">
            <strong>Task Presets (optional)</strong>
          </div>
          <div class="grid-2">
            <label>Scope
              <select id="queue-template-scope">
                <option value="global">global</option>
                <option value="personal">personal</option>
                <option value="team">team</option>
              </select>
            </label>
            <label>Scope Ref (optional)
              <input id="queue-template-scope-ref" placeholder="team-id (team scope only)" />
            </label>
          </div>
          <div class="grid-2">
            <label>Search
              <input id="queue-template-search" placeholder="filter by title, slug, tags" />
            </label>
            <label>Preset
              <select id="queue-template-select">
                <option value="">Select preset...</option>
              </select>
            </label>
          </div>
          <label>Feature Request / Initial Instructions
            <textarea id="queue-template-feature-request" placeholder="Describe the feature request this preset should execute."></textarea>
            <span class="small">Used as <span class="inline-code">feature_request</span> input when required by the preset. If left blank, the primary step instructions are used.</span>
          </label>
          <div class="grid-2">
            <label>Apply Mode
              <select id="queue-template-apply-mode">
                <option value="append">append</option>
                <option value="replace">replace</option>
              </select>
            </label>
            <label>Version
              <input id="queue-template-version" readonly />
            </label>
          </div>
          <div class="actions">
            <button type="button" id="queue-template-reload">Reload Presets</button>
            <button type="button" id="queue-template-preview">Preview</button>
            <button type="button" id="queue-template-apply">Apply</button>
            ${
              taskTemplateSaveEnabled
                ? '<button type="button" id="queue-template-save-current">Save Current Steps as Preset</button>'
                : ""
            }
          </div>
          <p class="small" id="queue-template-message"></p>
        </div>
        `
      : "";

    setView(
      "Submit Queue Task",
      `Create a typed Task job. Jobs are consumed from the shared queue ${defaultQueueName}.`,
      `
      <form id="queue-submit-form" class="queue-submit-form">
        <section class="queue-steps-section stack">
          <strong>Steps</strong>
          <span class="small">At least one step is required to submit. You can remove all steps while editing, but submit stays disabled by validation until a step is added back.</span>
          <div id="queue-steps-list" class="stack"></div>
        </section>
        ${templateControlsHtml}
        <label>Runtime
          <select name="runtime">
            ${runtimeOptions}
          </select>
        </label>
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
          <label>Target Branch (optional)
            <input name="newBranch" placeholder="auto-generated unless starting branch is non-default" />
          </label>
        </div>
        <label>Publish Mode
          <select name="publishMode">
            <option value="pr" ${defaultPublishMode === "pr" ? "selected" : ""}>pr</option>
            <option value="branch" ${defaultPublishMode === "branch" ? "selected" : ""}>branch</option>
            <option value="none" ${defaultPublishMode === "none" ? "selected" : ""}>none</option>
          </select>
          <span class="small">Defaults: no branch fields resolve at execution time; publish default is <span class="inline-code">${escapeHtml(
            defaultPublishMode,
          )}</span>.</span>
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
        <div class="queue-submit-actions" role="group" aria-label="Queue submission actions">
          <p class="small queue-submit-message" id="queue-submit-message"></p>
          <div class="actions queue-submit-actions-row">
            <button type="submit">Submit</button>
          </div>
        </div>
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
      instructions: "",
      skillId: "",
      skillArgs: "",
      skillRequiredCapabilities: "",
      templateStepId: "",
      templateInstructions: "",
      ...overrides,
    });
    const stepState = [createStepStateEntry()];
    let appliedTemplateState = [];
    const renderStepEditor = () => {
      if (!stepsList) {
        console.error("[dashboard] #queue-steps-list not found; step editor unavailable");
        return;
      }
      const rows = stepState
        .map((step, index) => {
          const isPrimaryStep = index === 0;
          const stepLabel = isPrimaryStep ? " (Primary)" : "";
          const skillLabel = isPrimaryStep ? "Skill (default)" : "Skill (optional)";
          const skillPlaceholder = isPrimaryStep
            ? "auto (default), speckit-orchestrate, ..."
            : "inherit primary step skill";
          const instructionsLabel = isPrimaryStep
            ? "Instructions (required for primary step)"
            : "Instructions (optional)";
          const instructionsPlaceholder = isPrimaryStep
            ? "Describe the task to execute against the repository."
            : "Step-specific instructions (leave blank to continue from the task objective).";
          const upDisabled = index === 0 ? "disabled" : "";
          const downDisabled = index === stepState.length - 1 ? "disabled" : "";
          const removeDisabled = "";
          const defaultHint = isPrimaryStep
            ? "Primary step skill values are forwarded to <span class=\"inline-code\">task.skill</span>."
            : "Leave skill blank to inherit primary step defaults.";
          return `
            <section class="queue-step-section stack" data-step-index="${index}">
              <div class="queue-step-header">
                <strong>Step ${index + 1}${stepLabel}</strong>
                <div class="queue-step-controls" role="group" aria-label="Step ${index + 1} controls">
                  <button
                    type="button"
                    class="queue-step-icon-button"
                    data-step-action="up"
                    data-step-index="${index}"
                    ${upDisabled}
                    aria-label="Move step up"
                    title="Move step up"
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                      <path d="M12 6v12m0-12-4 4m4-4 4 4" />
                    </svg>
                    <span class="sr-only">Move step up</span>
                  </button>
                  <button
                    type="button"
                    class="queue-step-icon-button"
                    data-step-action="down"
                    data-step-index="${index}"
                    ${downDisabled}
                    aria-label="Move step down"
                    title="Move step down"
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                      <path d="M12 18V6m0 12 4-4m-4 4-4-4" />
                    </svg>
                    <span class="sr-only">Move step down</span>
                  </button>
                  <button
                    type="button"
                    class="queue-step-icon-button destructive"
                    data-step-action="remove"
                    data-step-index="${index}"
                    ${removeDisabled}
                    aria-label="Remove step"
                    title="Remove step"
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                      <path d="M7 7l10 10M17 7l-10 10" />
                    </svg>
                    <span class="sr-only">Remove step</span>
                  </button>
                </div>
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
            </section>
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
      if (!(target instanceof Element)) {
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
        if (!(target instanceof Element)) {
          return;
        }
        const actionButton = target.closest("[data-step-action]");
        if (!(actionButton instanceof HTMLButtonElement)) {
          return;
        }
        if (actionButton.disabled) {
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
          stepState.splice(index, 1);
          renderStepEditor();
          return;
        }
        if (action === "up" && index > 0) {
          const current = stepState[index];
          stepState[index] = stepState[index - 1];
          stepState[index - 1] = current;
          renderStepEditor();
          return;
        }
        if (action === "down" && index < stepState.length - 1) {
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
        if (
          field === "instructions" &&
          stepState[index].templateStepId &&
          stepState[index].id === stepState[index].templateStepId &&
          fieldInput.value !== stepState[index].templateInstructions
        ) {
          stepState[index].id = "";
        }
      });
    }
    renderStepEditor();
    loadAvailableSkillIds().then((skillIds) => {
      populateSkillDatalist("queue-skill-options", skillIds);
    });

    const templateMessage = document.getElementById("queue-template-message");
    const templateScope = document.getElementById("queue-template-scope");
    const templateScopeRef = document.getElementById("queue-template-scope-ref");
    const templateSearch = document.getElementById("queue-template-search");
    const templateSelect = document.getElementById("queue-template-select");
    const templateFeatureRequest = document.getElementById("queue-template-feature-request");
    const templateVersion = document.getElementById("queue-template-version");
    const templateApplyMode = document.getElementById("queue-template-apply-mode");
    const templateReload = document.getElementById("queue-template-reload");
    const templatePreview = document.getElementById("queue-template-preview");
    const templateApply = document.getElementById("queue-template-apply");
    const templateSaveCurrent = document.getElementById("queue-template-save-current");
    let templateItems = [];
    const templateInputMemory = {};
    const preferredTemplateSlug = "speckit-orchestrate";
    const preferredTemplateVersion = "1.0.0";

    const setTemplateMessage = (text, isError = false) => {
      if (!templateMessage) {
        return;
      }
      templateMessage.className = isError ? "notice error" : "small";
      templateMessage.textContent = text;
    };

    const currentTemplateScope = () =>
      templateScope instanceof HTMLSelectElement ? templateScope.value : "global";
    const currentTemplateScopeRef = () =>
      templateScopeRef instanceof HTMLInputElement
        ? String(templateScopeRef.value || "").trim()
        : "";
    const currentTemplateSearch = () =>
      templateSearch instanceof HTMLInputElement
        ? String(templateSearch.value || "").trim()
        : "";
    const currentTemplateFeatureRequest = () =>
      templateFeatureRequest instanceof HTMLTextAreaElement
        ? String(templateFeatureRequest.value || "").trim()
        : "";
    const normalizeTemplateInputKey = (key) =>
      String(key || "")
        .trim()
        .toLowerCase()
        .replaceAll(/[^a-z0-9]/g, "");
    const valueForFeatureRequestInput = (rawInputs) => {
      if (!rawInputs || typeof rawInputs !== "object" || Array.isArray(rawInputs)) {
        return "";
      }
      let fallback = "";
      let preferred = "";
      for (const [rawKey, rawValue] of Object.entries(rawInputs)) {
        if (normalizeTemplateInputKey(rawKey) !== "featurerequest") {
          continue;
        }
        const candidate = String(rawValue || "").trim();
        if (!candidate) {
          continue;
        }
        if (String(rawKey || "").trim().toLowerCase() === "feature_request") {
          preferred = candidate;
          break;
        }
        if (!fallback) {
          fallback = candidate;
        }
      }
      return preferred || fallback;
    };
    const resolveObjectiveInstructions = (primaryInstructions) => {
      const explicitFeatureRequest = currentTemplateFeatureRequest();
      if (explicitFeatureRequest) {
        return explicitFeatureRequest;
      }
      if (primaryInstructions) {
        return primaryInstructions;
      }
      for (let index = appliedTemplateState.length - 1; index >= 0; index -= 1) {
        const candidate = valueForFeatureRequestInput(appliedTemplateState[index]?.inputs);
        if (candidate) {
          return candidate;
        }
      }
      return primaryInstructions;
    };
    const templateVersionForItem = (item) =>
      String(item?.latestVersion || item?.version || "1.0.0").trim();
    const preferredTemplateFrom = (items) => {
      const exact = items.find(
        (item) =>
          String(item?.slug || "").trim() === preferredTemplateSlug &&
          templateVersionForItem(item) === preferredTemplateVersion,
      );
      if (exact) {
        return exact;
      }
      return (
        items.find((item) => String(item?.slug || "").trim() === preferredTemplateSlug) || null
      );
    };
    const syncTemplateVersion = () => {
      if (!(templateVersion instanceof HTMLInputElement)) {
        return;
      }
      const selected = selectedTemplate();
      templateVersion.value = selected ? templateVersionForItem(selected) : "";
    };

    const renderTemplateSelect = () => {
      if (!(templateSelect instanceof HTMLSelectElement)) {
        return;
      }
      const previousSelection = String(templateSelect.value || "").trim();
      const searchFilter = currentTemplateSearch().toLowerCase();
      const filtered = templateItems.filter((item) => {
        if (!searchFilter) {
          return true;
        }
        const haystack = `${item.slug} ${item.title} ${(item.tags || []).join(" ")}`.toLowerCase();
        return haystack.includes(searchFilter);
      });
      templateSelect.innerHTML = [
        '<option value="">Select preset...</option>',
        ...filtered.map(
          (item) =>
            `<option value="${escapeHtml(item.slug)}">${escapeHtml(item.title)} (${escapeHtml(
              templateVersionForItem(item),
            )})</option>`,
        ),
      ].join("");
      const hasPreviousSelection = filtered.some(
        (item) => String(item?.slug || "").trim() === previousSelection,
      );
      const preferredTemplate = preferredTemplateFrom(filtered);
      const fallbackTemplate = filtered[0] || null;
      const nextSelection = hasPreviousSelection
        ? previousSelection
        : String(
            preferredTemplate?.slug ||
              fallbackTemplate?.slug ||
              "",
          ).trim();
      templateSelect.value = nextSelection;
      if (!nextSelection && filtered.length === 0) {
        templateSelect.value = "";
      }
      syncTemplateVersion();
    };

    const fetchTemplateList = async () => {
      if (!taskTemplateCatalogEnabled) {
        return;
      }
      const scope = currentTemplateScope();
      const scopeRef = currentTemplateScopeRef();
      const params = new URLSearchParams();
      params.set("scope", scope);
      if (scopeRef) {
        params.set("scopeRef", scopeRef);
      }
      setTemplateMessage("Loading presets...");
      try {
        const payload = await fetchJson(`${taskTemplateEndpoints.list}?${params.toString()}`);
        templateItems = Array.isArray(payload?.items) ? payload.items : [];
        renderTemplateSelect();
        setTemplateMessage(`Loaded ${templateItems.length} presets.`);
      } catch (error) {
        console.error("template list fetch failed", error);
        templateItems = [];
        renderTemplateSelect();
        setTemplateMessage(
          "Failed to load presets: " + String(error?.message || "request failed"),
          true,
        );
      }
    };

    const selectedTemplate = () => {
      if (!(templateSelect instanceof HTMLSelectElement)) {
        return null;
      }
      const slug = String(templateSelect.value || "").trim();
      if (!slug) {
        return null;
      }
      return templateItems.find((item) => String(item.slug || "").trim() === slug) || null;
    };

    const resolveTemplateInputs = (inputs) => {
      const values = {};
      const assumptions = [];
      const primaryInstructions = String(stepState[0]?.instructions || "").trim();
      const explicitFeatureRequest = currentTemplateFeatureRequest();
      const repositoryInput = form.querySelector('input[name="repository"]');
      const repositoryValue =
        repositoryInput instanceof HTMLInputElement
          ? String(repositoryInput.value || "").trim()
          : "";

      const normalizeBoolean = (value) => {
        if (typeof value === "boolean") {
          return value;
        }
        const lowered = String(value ?? "").trim().toLowerCase();
        if (["1", "true", "yes", "on"].includes(lowered)) {
          return true;
        }
        if (["0", "false", "no", "off"].includes(lowered)) {
          return false;
        }
        return false;
      };

      for (const definition of Array.isArray(inputs) ? inputs : []) {
        const name = String(definition?.name || "").trim();
        const label = String(definition?.label || name).trim() || name;
        if (!name) {
          continue;
        }
        const required = Boolean(definition?.required);
        const inputType = String(definition?.type || "").toLowerCase();
        const options = Array.isArray(definition?.options)
          ? definition.options.map((option) => String(option).trim()).filter((option) => option)
          : [];
        const key = name.toLowerCase();
        const isFeatureRequestKey =
          key.includes("feature_request") || key === "feature" || key === "request";

        let value = null;
        let valueSource = "";
        const remembered = templateInputMemory[name];
        const defaultValue = definition?.default;

        if (isFeatureRequestKey && explicitFeatureRequest) {
          value = explicitFeatureRequest;
          valueSource = "manual";
        } else if (remembered !== null && remembered !== undefined && String(remembered).trim() !== "") {
          value = remembered;
          valueSource = "memory";
        } else if (defaultValue !== null && defaultValue !== undefined && String(defaultValue).trim() !== "") {
          value = defaultValue;
          valueSource = "default";
        } else if (
          key.includes("instruction") ||
          isFeatureRequestKey
        ) {
          value = explicitFeatureRequest || primaryInstructions;
          valueSource = "draft";
        } else if (key.includes("repo")) {
          value = repositoryValue;
          valueSource = "draft";
        } else if (inputType === "enum") {
          if (key.includes("mode") && options.includes("runtime")) {
            value = "runtime";
          } else if (options.length > 0) {
            value = options[0];
          }
          valueSource = "assumed";
        } else if (inputType === "boolean") {
          value = false;
          valueSource = "assumed";
        }

        const hasValue = value !== null && value !== undefined && String(value).trim() !== "";
        if (!hasValue && required) {
          if (inputType === "enum" && options.length > 0) {
            value = options[0];
          } else if (inputType === "boolean") {
            value = false;
          } else if (explicitFeatureRequest || primaryInstructions) {
            value = explicitFeatureRequest || primaryInstructions;
          } else {
            value = `auto-${key.replaceAll(/[^a-z0-9]+/g, "-").replaceAll(/^-+|-+$/g, "") || "value"}`;
          }
          valueSource = "assumed";
        } else if (!hasValue) {
          continue;
        }

        let normalized = value;
        if (inputType === "boolean") {
          normalized = normalizeBoolean(value);
        } else {
          normalized = String(value).trim();
        }

        if (inputType === "enum") {
          const candidate = String(normalized).trim();
          if (options.length > 0 && !options.includes(candidate)) {
            normalized = options[0];
            valueSource = "assumed";
          } else {
            normalized = candidate;
          }
        }

        if ((normalized === null || normalized === undefined || String(normalized).trim() === "") && required) {
          throw new Error(`Input '${label}' could not be inferred.`);
        }
        if (normalized === null || normalized === undefined || String(normalized).trim() === "") {
          continue;
        }

        values[name] = normalized;
        templateInputMemory[name] = normalized;
        if (valueSource === "assumed" || valueSource === "draft") {
          assumptions.push(label);
        }
      }
      return { values, assumptions };
    };

    const mapExpandedStepToState = (step) => {
      const skill = step && typeof step.skill === "object" && !Array.isArray(step.skill) ? step.skill : null;
      const caps = Array.isArray(skill?.requiredCapabilities)
        ? skill.requiredCapabilities.join(",")
        : "";
      const args = skill && skill.args && typeof skill.args === "object" && !Array.isArray(skill.args)
        ? JSON.stringify(skill.args)
        : "";
      const stepId = String(step?.id || "").trim();
      const instructions = String(step?.instructions || "").trim();
      return createStepStateEntry({
        id: stepId,
        instructions,
        skillId: String(skill?.id || "").trim(),
        skillArgs: args,
        skillRequiredCapabilities: caps,
        templateStepId: stepId,
        templateInstructions: instructions,
      });
    };

    const isEmptyStepStateEntry = (step) =>
      !String(step?.id || "").trim() &&
      !String(step?.instructions || "").trim() &&
      !String(step?.skillId || "").trim() &&
      !String(step?.skillArgs || "").trim() &&
      !String(step?.skillRequiredCapabilities || "").trim() &&
      !String(step?.templateStepId || "").trim() &&
      !String(step?.templateInstructions || "").trim();

    const hasOnlyEmptyDefaultStep = () =>
      stepState.length === 1 && isEmptyStepStateEntry(stepState[0]);

    const applySelectedTemplate = async ({ previewOnly }) => {
      const selected = selectedTemplate();
      if (!selected) {
        setTemplateMessage("Choose a preset first.", true);
        return;
      }
      const scope = currentTemplateScope();
      const scopeRef = currentTemplateScopeRef();
      const scopeParams = new URLSearchParams({ scope });
      if (scopeRef) {
        scopeParams.set("scopeRef", scopeRef);
      }

      setTemplateMessage(previewOnly ? "Loading preview..." : "Applying preset...");
      try {
        const detail = await fetchJson(
          `${endpoint(taskTemplateEndpoints.detail, { slug: selected.slug })}?${scopeParams.toString()}`,
        );
        const { values: inputs, assumptions } = resolveTemplateInputs(detail?.inputs || []);
        const expanded = await fetchJson(
          `${endpoint(taskTemplateEndpoints.expand, { slug: selected.slug })}?${scopeParams.toString()}`,
          {
            method: "POST",
            body: JSON.stringify({
              version: detail?.version || detail?.latestVersion || selected.latestVersion || "1.0.0",
              inputs,
              options: { enforceStepLimit: true },
            }),
          },
        );
        const expandedSteps = Array.isArray(expanded?.steps) ? expanded.steps : [];
        if (templateVersion instanceof HTMLInputElement) {
          templateVersion.value = String(
            expanded?.appliedTemplate?.version ||
              detail?.version ||
              detail?.latestVersion ||
              selected.latestVersion ||
              "",
          );
        }
        if (previewOnly) {
          const previewLines = expandedSteps.map((step, index) => {
            const title = String(step?.title || "").trim() || `Step ${index + 1}`;
            return `${index + 1}. ${title}`;
          });
          const warningText =
            Array.isArray(expanded?.warnings) && expanded.warnings.length > 0
              ? `\\nWarnings: ${expanded.warnings.join("; ")}`
              : "";
          window.alert(
            `Preset preview (${expandedSteps.length} steps):\\n${previewLines.join(
              "\\n",
            )}${warningText}`,
          );
          setTemplateMessage(`Preview loaded (${expandedSteps.length} steps).`);
          return;
        }

        const mappedSteps = expandedSteps.map(mapExpandedStepToState);
        const applyMode =
          templateApplyMode instanceof HTMLSelectElement ? templateApplyMode.value : "append";
        const shouldReplaceEmptyDefaultStep =
          applyMode !== "replace" && hasOnlyEmptyDefaultStep();
        if (applyMode === "replace" || shouldReplaceEmptyDefaultStep) {
          stepState.splice(0, stepState.length, ...(mappedSteps.length > 0 ? mappedSteps : [createStepStateEntry()]));
          appliedTemplateState = [];
        } else {
          stepState.push(...mappedSteps);
        }
        if (mappedSteps.length > 0) {
          const appliedTemplate = expanded?.appliedTemplate || {};
          appliedTemplateState.push({
            slug: String(appliedTemplate.slug || selected.slug),
            version: String(
              appliedTemplate.version ||
                detail?.version ||
                selected.latestVersion ||
                "1.0.0",
            ),
            inputs:
              appliedTemplate.inputs && typeof appliedTemplate.inputs === "object"
                ? appliedTemplate.inputs
                : inputs,
            stepIds: Array.isArray(appliedTemplate.stepIds)
              ? appliedTemplate.stepIds
              : mappedSteps.map((step) => step.id).filter((id) => Boolean(id)),
            appliedAt:
              String(appliedTemplate.appliedAt || "").trim() ||
              new Date().toISOString(),
            capabilities: Array.isArray(expanded?.capabilities) ? expanded.capabilities : [],
          });
        }
        renderStepEditor();
        const autoFillSuffix =
          assumptions.length > 0
            ? ` Auto-filled ${assumptions.length} input(s): ${assumptions.join(", ")}.`
            : "";
        setTemplateMessage(
          `Applied preset '${selected.title}' (${mappedSteps.length} steps).${autoFillSuffix}`,
        );
      } catch (error) {
        console.error("template apply failed", error);
        setTemplateMessage(
          "Failed to apply preset: " + String(error?.message || "request failed"),
          true,
        );
      }
    };

    const saveCurrentStepsAsTemplate = async () => {
      if (!taskTemplateSaveEnabled) {
        return;
      }
      const title = window.prompt("Preset title", "");
      if (title === null || !String(title).trim()) {
        setTemplateMessage("Preset save cancelled.");
        return;
      }
      const description = window.prompt("Preset description", `Saved from queue draft: ${title}`) || "";
      const scope = (window.prompt("Scope (personal/team)", "personal") || "personal")
        .trim()
        .toLowerCase();
      if (!["personal", "team"].includes(scope)) {
        setTemplateMessage("Scope must be personal or team.", true);
        return;
      }
      const scopeRef =
        scope === "team"
          ? String(window.prompt("Team scopeRef (required for team)", "") || "").trim()
          : "";
      if (scope === "team" && !scopeRef) {
        setTemplateMessage("Team scopeRef is required for team presets.", true);
        return;
      }

      const steps = stepState
        .map((step) => {
          const instructions = String(step.instructions || "").trim();
          if (!instructions) {
            return null;
          }
          const blueprint = {
            instructions,
          };
          const skillId = String(step.skillId || "").trim();
          const caps = parseCapabilitiesCsv(step.skillRequiredCapabilities || "");
          const skillArgsRaw = String(step.skillArgs || "").trim();
          if (skillId || skillArgsRaw || caps.length > 0) {
            let skillArgs = {};
            if (skillArgsRaw) {
              try {
                const parsed = JSON.parse(skillArgsRaw);
                if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
                  skillArgs = parsed;
                }
              } catch (_error) {
                // Best-effort save path: drop invalid JSON skill args instead of aborting.
                skillArgs = {};
              }
            }
            blueprint.skill = {
              id: skillId || "auto",
              args: skillArgs,
              ...(caps.length > 0 ? { requiredCapabilities: caps } : {}),
            };
          }
          return blueprint;
        })
        .filter((item) => Boolean(item));

      if (steps.length === 0) {
        setTemplateMessage("Add at least one step with instructions before saving.", true);
        return;
      }

      setTemplateMessage("Saving preset...");
      try {
        const body = {
          scope,
          ...(scopeRef ? { scopeRef } : {}),
          title: String(title).trim(),
          description: String(description).trim() || String(title).trim(),
          steps,
          suggestedInputs: [],
          tags: [],
        };
        const created = await fetchJson(taskTemplateEndpoints.save, {
          method: "POST",
          body: JSON.stringify(body),
        });
        setTemplateMessage(`Saved preset '${created?.title || body.title}'. Reloading catalog...`);
        if (templateScope instanceof HTMLSelectElement) {
          templateScope.value = scope;
        }
        if (templateScopeRef instanceof HTMLInputElement) {
          templateScopeRef.value = scope === "team" ? scopeRef : "";
        }
        await fetchTemplateList();
      } catch (error) {
        console.error("template save failed", error);
        setTemplateMessage(
          "Failed to save preset: " + String(error?.message || "request failed"),
          true,
        );
      }
    };

    if (templateScope instanceof HTMLSelectElement) {
      templateScope.addEventListener("change", () => {
        fetchTemplateList().catch((error) => {
          console.error("template scope change failed", error);
        });
      });
    }
    if (templateScopeRef instanceof HTMLInputElement) {
      templateScopeRef.addEventListener("change", () => {
        fetchTemplateList().catch((error) => {
          console.error("template scopeRef change failed", error);
        });
      });
    }
    if (templateSearch instanceof HTMLInputElement) {
      templateSearch.addEventListener("input", renderTemplateSelect);
    }
    if (templateSelect instanceof HTMLSelectElement) {
      templateSelect.addEventListener("change", () => {
        syncTemplateVersion();
      });
    }
    if (templateReload instanceof HTMLButtonElement) {
      templateReload.addEventListener("click", () => {
        fetchTemplateList().catch((error) => {
          console.error("template reload failed", error);
        });
      });
    }
    if (templatePreview instanceof HTMLButtonElement) {
      templatePreview.addEventListener("click", () => {
        applySelectedTemplate({ previewOnly: true }).catch((error) => {
          console.error("template preview failed", error);
        });
      });
    }
    if (templateApply instanceof HTMLButtonElement) {
      templateApply.addEventListener("click", () => {
        applySelectedTemplate({ previewOnly: false }).catch((error) => {
          console.error("template apply failed", error);
        });
      });
    }
    if (templateSaveCurrent instanceof HTMLButtonElement) {
      templateSaveCurrent.addEventListener("click", () => {
        saveCurrentStepsAsTemplate().catch((error) => {
          console.error("template save-current failed", error);
        });
      });
    }
    if (taskTemplateCatalogEnabled) {
      fetchTemplateList().catch((error) => {
        console.error("initial template load failed", error);
      });
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      message.className = "small queue-submit-message";
      message.textContent = "Submitting...";

      const formData = new FormData(form);
      const primaryStep = stepState[0] || null;
      if (!primaryStep) {
        message.className = "notice error queue-submit-message";
        message.textContent = "Add at least one step before submitting.";
        return;
      }
      const instructions = String(primaryStep.instructions || "").trim();
      if (!instructions) {
        message.className = "notice error queue-submit-message";
        message.textContent = "Primary step instructions are required.";
        return;
      }
      const objectiveInstructions = resolveObjectiveInstructions(instructions);

      const repositoryInput = String(formData.get("repository") || "").trim();
      const repository = repositoryInput || defaultRepository;
      if (!repository) {
        message.className = "notice error queue-submit-message";
        message.textContent =
          "Repository is required because no system default repository is configured.";
        return;
      }
      if (!isValidRepositoryInput(repository)) {
        message.className = "notice error queue-submit-message";
        message.textContent =
          "Repository must be owner/repo, https://<host>/<path>, or git@<host>:<path> (token-free).";
        return;
      }

      const affinityKey = String(formData.get("affinityKey") || "").trim();
      const rawRuntime = String(formData.get("runtime") || "").trim();
      const runtimeCandidate = rawRuntime || defaultTaskRuntime;
      const runtimeMode = normalizeTaskRuntimeInput(runtimeCandidate);
      if (!runtimeMode) {
        message.className = "notice error queue-submit-message";
        message.textContent =
          "Runtime must be one of: " + supportedTaskRuntimes.join(", ") + ".";
        return;
      }

      const publishMode = String(formData.get("publishMode") || defaultPublishMode)
        .trim()
        .toLowerCase();
      if (!["none", "branch", "pr"].includes(publishMode)) {
        message.className = "notice error queue-submit-message";
        message.textContent =
          "Publish mode must be one of: none, branch, pr.";
        return;
      }

      const priority = Number(formData.get("priority") || 0);
      if (!Number.isInteger(priority)) {
        message.className = "notice error queue-submit-message";
        message.textContent = "Priority must be an integer.";
        return;
      }

      const maxAttempts = Number(formData.get("maxAttempts") || 3);
      if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
        message.className = "notice error queue-submit-message";
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
          message.className = "notice error queue-submit-message";
          message.textContent =
            "Primary step Skill Args must be valid JSON object text (for example: {\"featureKey\":\"...\"}).";
          return;
        }
      }
      const model = String(formData.get("model") || "").trim() || null;
      const effort = String(formData.get("effort") || "").trim() || null;
      const startingBranch = String(formData.get("startingBranch") || "").trim() || null;
      const newBranch = String(formData.get("newBranch") || "").trim() || null;
      const additionalSteps = [];
      const stepSkillRequiredCapabilities = [];
      for (let index = 1; index < stepState.length; index += 1) {
        const rawStep = stepState[index] || {};
        const stepInstructions = String(rawStep.instructions || "").trim();
        const stepSkillId = String(rawStep.skillId || "").trim();
        const stepSkillArgsRaw = String(rawStep.skillArgs || "").trim();
        const stepSkillCaps = parseCapabilitiesCsv(rawStep.skillRequiredCapabilities || "");
        const hasStepContent =
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
            message.className = "notice error queue-submit-message";
            message.textContent = `Step ${index + 1} Skill Args must be valid JSON object text.`;
            return;
          }
        }
        const stepPayload = {};
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
        additionalSteps.push({ sourceIndex: index, payload: stepPayload });
      }
      const includePrimaryStepForObjectiveOverride =
        Boolean(instructions) && objectiveInstructions !== instructions;
      const hasTemplateBoundStep = stepState.some((step) => Boolean(String(step?.id || "").trim()));
      const includeExplicitSteps =
        additionalSteps.length > 0 ||
        includePrimaryStepForObjectiveOverride ||
        hasTemplateBoundStep;
      const normalizedStepEntries = includeExplicitSteps
        ? [
            {
              sourceIndex: 0,
              payload: {
                instructions,
              },
            },
            ...additionalSteps,
          ]
        : [];
      const templateIdToSequential = new Map();
      const normalizedSteps = normalizedStepEntries.map((entry, index) => {
        const assignedId = `step-${index + 1}`;
        const sourceState = stepState[entry.sourceIndex] || {};
        const templateBindingId = String(sourceState.id || "").trim();
        if (templateBindingId) {
          if (!templateIdToSequential.has(templateBindingId)) {
            templateIdToSequential.set(templateBindingId, []);
          }
          templateIdToSequential.get(templateBindingId).push(assignedId);
        }
        return { ...entry.payload, id: assignedId };
      });

      const templateCapabilities = [];
      const appliedStepTemplates = [];
      const templateIdCursor = new Map();
      for (const entry of appliedTemplateState) {
        if (!entry || typeof entry !== "object") {
          continue;
        }
        const slug = String(entry.slug || "").trim();
        const version = String(entry.version || "").trim();
        if (!slug || !version) {
          continue;
        }
        const rawTemplateStepIds = Array.isArray(entry.stepIds) ? entry.stepIds : [];
        const remappedStepIds = [];
        for (const rawId of rawTemplateStepIds) {
          const templateId = String(rawId || "").trim();
          if (!templateId) {
            continue;
          }
          const availableStepIds = templateIdToSequential.get(templateId) || [];
          const nextIndex = templateIdCursor.get(templateId) || 0;
          const remappedStepId = availableStepIds[nextIndex];
          if (!remappedStepId) {
            message.className = "notice error queue-submit-message";
            message.textContent = `Applied template references unknown step binding ID: ${templateId}. Please re-apply the template.`;
            return;
          }
          remappedStepIds.push(remappedStepId);
          templateIdCursor.set(templateId, nextIndex + 1);
        }
        const templateEntry = {
          slug,
          version,
          inputs: entry.inputs && typeof entry.inputs === "object" ? entry.inputs : {},
          stepIds: remappedStepIds,
          appliedAt: String(entry.appliedAt || "").trim() || new Date().toISOString(),
        };
        if (Array.isArray(entry.capabilities)) {
          templateCapabilities.push(...entry.capabilities);
          templateEntry.capabilities = entry.capabilities;
        }
        appliedStepTemplates.push(templateEntry);
      }
      const mergedCapabilities = Array.from(
        new Set(
          deriveRequiredCapabilities({
            runtimeMode,
            publishMode,
            taskSkillRequiredCapabilities,
            stepSkillRequiredCapabilities,
          }).concat(parseCapabilitiesCsv(templateCapabilities.join(","))),
        ),
      );

      const payload = {
        repository,
        requiredCapabilities: mergedCapabilities,
        targetRuntime: runtimeMode,
        task: {
          instructions: objectiveInstructions,
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
          ...(appliedStepTemplates.length > 0
            ? { appliedStepTemplates }
            : {}),
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
        message.className = "notice error queue-submit-message";
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
      `
        <div id="queue-detail-page">
          <div id="queue-detail-notice"></div>
          <div id="queue-cancel-notice"></div>
          <div id="queue-cancel-actions"></div>
          <div id="queue-job-summary"></div>
          <div class="stack">
            <section id="queue-live-session-section"></section>
            <section>
              <h3>Events</h3>
              <p class="small" id="queue-events-summary">Loading events...</p>
              <div class="queue-events-table-wrap">
                <table>
                  <thead><tr><th>Time</th><th>Stage</th><th>Level</th><th>Message</th></tr></thead>
                  <tbody id="queue-events-body"><tr><td colspan="4" class="small">Loading events...</td></tr></tbody>
                </table>
              </div>
              <div class="actions">
                <button type="button" id="queue-load-older-events" class="secondary" disabled>Load Older Events</button>
                <span class="small" id="queue-load-older-status"></span>
              </div>
            </section>
            <section>
              <div class="actions queue-live-output-toolbar">
                <label class="queue-inline-toggle">
                  <input type="checkbox" id="queue-follow-output" checked />
                  Follow output
                </label>
                <label class="queue-inline-filter">
                  Filter
                  <select id="queue-output-filter">
                    <option value="all" selected>All</option>
                    <option value="stages">Stages</option>
                    <option value="logs">Logs</option>
                    <option value="warnings">Warnings/Errors</option>
                  </select>
                </label>
                <button type="button" class="secondary" id="queue-copy-output">Copy</button>
                <span id="queue-full-log-action" class="small">Download full logs unavailable.</span>
                <span class="small" id="queue-live-transport-status">Live transport: Polling (idle)</span>
              </div>
              <pre id="queue-live-output" class="queue-live-output"></pre>
            </section>
            <section>
              <h3>Artifacts</h3>
              <table>
                <thead><tr><th>Name</th><th>Stage</th><th>Size</th><th>Content Type</th><th>Action</th></tr></thead>
                <tbody id="queue-artifacts-body"><tr><td colspan="5" class="small">Loading artifacts...</td></tr></tbody>
              </table>
            </section>
          </div>
        </div>
      `,
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
      liveActionNotice: "",
      liveActionNoticeIsError: false,
      eventIds: new Set(),
      after: null,
      afterEventId: null,
      oldest: null,
      oldestEventId: null,
      hasOlderEvents: false,
      loadingOlderEvents: false,
      outputFilter: "all",
      followOutput: true,
      eventsTransport: "polling",
      eventsTransportStatus: "idle",
      eventsPollingStarted: false,
      eventsRenderTimer: null,
      eventsRenderIntervalMs: 120,
      maxEvents: 20000,
      maxVisibleEventRows: 100,
      maxEventMessageChars: 320,
      maxLiveOutputLines: 1500,
      liveOutputLines: [],
      liveOutputRenderedEventCount: 0,
      liveOutputRenderedFilter: "all",
      forceLiveOutputRebuild: true,
      pendingLiveControlAction: "",
    };

    const detailPage = document.getElementById("queue-detail-page");
    if (!detailPage) {
      return;
    }

    const toSortableTimestamp = (value) => Date.parse(String(value || "")) || 0;
    const compareEventsAsc = (left, right) => {
      const leftTs = toSortableTimestamp(pick(left, "createdAt"));
      const rightTs = toSortableTimestamp(pick(right, "createdAt"));
      if (leftTs !== rightTs) {
        return leftTs - rightTs;
      }
      return String(pick(left, "id") || "").localeCompare(String(pick(right, "id") || ""));
    };

    const normalizeIncomingEventsAsc = (incomingEvents) =>
      (incomingEvents || []).slice().sort(compareEventsAsc);

    const setDetailNotice = (message, isError = true) => {
      const noticeNode = document.getElementById("queue-detail-notice");
      if (!noticeNode) {
        return;
      }
      if (!message) {
        noticeNode.innerHTML = "";
        return;
      }
      noticeNode.innerHTML = `<div class="notice ${isError ? "error" : ""}">${escapeHtml(
        message,
      )}</div>`;
    };

    const setCancelNotice = (message, isError = false) => {
      const noticeNode = document.getElementById("queue-cancel-notice");
      if (!noticeNode) {
        return;
      }
      if (!message) {
        noticeNode.innerHTML = "";
        return;
      }
      noticeNode.innerHTML = `<div class="notice ${isError ? "error" : ""}">${escapeHtml(
        message,
      )}</div>`;
    };

    const setLiveNotice = (message, isError = false) => {
      state.liveActionNotice = String(message || "");
      state.liveActionNoticeIsError = Boolean(isError);
      renderLiveSession();
    };

    const refreshEventCursors = () => {
      const oldestEvent = state.events.length > 0 ? state.events[0] : null;
      const newestEvent =
        state.events.length > 0 ? state.events[state.events.length - 1] : null;
      state.oldest = oldestEvent ? pick(oldestEvent, "createdAt") || null : null;
      state.oldestEventId = oldestEvent ? String(pick(oldestEvent, "id") || "") || null : null;
      state.after = newestEvent ? pick(newestEvent, "createdAt") || null : null;
      state.afterEventId = newestEvent ? String(pick(newestEvent, "id") || "") || null : null;
    };

    const trimEventsToLimit = () => {
      if (state.events.length <= state.maxEvents) {
        return;
      }
      const overflow = state.events.length - state.maxEvents;
      const removed = state.events.splice(0, overflow);
      removed.forEach((event) => {
        const eventId = String(pick(event, "id") || "");
        if (eventId) {
          state.eventIds.delete(eventId);
        }
      });
      state.forceLiveOutputRebuild = true;
      state.hasOlderEvents = true;
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

    const renderTransportStatus = () => {
      const transportNode = document.getElementById("queue-live-transport-status");
      if (!transportNode) {
        return;
      }
      const transportLabel = state.eventsTransport === "sse" ? "SSE" : "Polling";
      transportNode.textContent = `Live transport: ${transportLabel} (${state.eventsTransportStatus})`;
    };

    const renderLoadOlderControls = () => {
      const button = document.getElementById("queue-load-older-events");
      const status = document.getElementById("queue-load-older-status");
      if (!button || !status) {
        return;
      }
      const canLoadOlder = Boolean(
        state.oldest &&
          state.oldestEventId &&
          state.hasOlderEvents &&
          !state.loadingOlderEvents,
      );
      button.disabled = !canLoadOlder;
      if (state.loadingOlderEvents) {
        status.textContent = "Loading older events...";
        return;
      }
      if (!state.hasOlderEvents && state.events.length > 0) {
        status.textContent = "No older events available.";
        return;
      }
      status.textContent = "";
    };

    const renderArtifacts = () => {
      const bodyNode = document.getElementById("queue-artifacts-body");
      const fullLogNode = document.getElementById("queue-full-log-action");
      if (!bodyNode || !fullLogNode) {
        return;
      }
      const artifacts = Array.isArray(state.artifacts) ? state.artifacts : [];
      const rows = artifacts
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
      bodyNode.innerHTML =
        rows || "<tr><td colspan='5' class='small'>No artifacts.</td></tr>";

      const fullLogArtifact = resolveFullLogArtifact(artifacts);
      if (fullLogArtifact) {
        const fullLogDownloadUrl = endpoint(
          "/api/queue/jobs/{id}/artifacts/{artifactId}/download",
          {
            id: jobId,
            artifactId: pick(fullLogArtifact, "id"),
          },
        );
        fullLogNode.innerHTML = `<a href="${escapeHtml(fullLogDownloadUrl)}"><button type="button" class="secondary">Download Full Logs</button></a>`;
      } else {
        fullLogNode.textContent = "Download full logs unavailable.";
      }
    };

    const renderJobSummary = () => {
      const summaryNode = document.getElementById("queue-job-summary");
      const cancelActionsNode = document.getElementById("queue-cancel-actions");
      if (!summaryNode || !cancelActionsNode) {
        return;
      }
      const job = state.job;
      if (!job) {
        cancelActionsNode.innerHTML = "";
        summaryNode.innerHTML = "<div class='notice error'>Queue job not found.</div>";
        return;
      }

      const normalizedStatus = normalizeStatus("queue", pick(job, "status"));
      const cancelRequestedAt = pick(job, "cancelRequestedAt");
      const cancelPending = Boolean(cancelRequestedAt) && normalizedStatus === "running";
      const canCancel = normalizedStatus === "queued" || normalizedStatus === "running";
      const cancelButtonDisabled = !canCancel || cancelPending;
      const cancelButtonLabel = cancelPending ? "Cancellation Requested" : "Cancel Job";
      cancelActionsNode.innerHTML = `<div class="actions"><button type="button" id="queue-cancel-button" ${
        cancelButtonDisabled ? "disabled" : ""
      }>${escapeHtml(cancelButtonLabel)}</button></div>`;

      const payload = pick(job, "payload") || {};
      const runtimeTarget = extractRuntimeFromPayload(payload) || "any";
      const runtimeModel = extractRuntimeModelFromPayload(payload) || "default";
      const runtimeEffort = extractRuntimeEffortFromPayload(payload) || "default";
      const selectedSkill = extractSkillFromPayload(payload) || "auto";
      summaryNode.innerHTML = `
        <p class="small">Effective queue: <span class="inline-code">${escapeHtml(
          pick(job, "queueName") || defaultQueueName,
        )}</span></p>
        <div class="grid-2">
          <div class="card"><strong>Status:</strong> ${statusBadge("queue", pick(job, "status"))}</div>
          <div class="card"><strong>Type:</strong> ${escapeHtml(pick(job, "type") || "")}</div>
          <div class="card"><strong>Created:</strong> ${formatTimestamp(pick(job, "createdAt"))}</div>
          <div class="card"><strong>Started:</strong> ${formatTimestamp(pick(job, "startedAt"))}</div>
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
    };

    const renderLiveSession = () => {
      const node = document.getElementById("queue-live-session-section");
      if (!node) {
        return;
      }
      const job = state.job;
      if (!job) {
        node.innerHTML = "";
        return;
      }

      const jobPayload =
        typeof pick(job, "payload") === "object" && !Array.isArray(pick(job, "payload"))
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

      node.innerHTML = `
        <h3>Live Session</h3>
        ${
          state.liveSessionError
            ? `<div class="notice error">${escapeHtml(state.liveSessionError)}</div>`
            : ""
        }
        ${
          state.liveActionNotice
            ? `<div class="notice ${state.liveActionNoticeIsError ? "error" : ""}">${escapeHtml(
                state.liveActionNotice,
              )}</div>`
            : ""
        }
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
            state.pendingLiveControlAction === "enable"
              ? "disabled"
              : liveSessionActionsDisabled
                ? "disabled"
                : liveSessionCreated && ["starting", "ready"].includes(liveSessionStatus)
                  ? "disabled"
                  : ""
          }>Enable Live Session</button>
          <button type="button" id="queue-live-grant" ${
            state.pendingLiveControlAction === "grant"
              ? "disabled"
              : liveSessionReady && !liveSessionActionsDisabled
                ? ""
                : "disabled"
          }>Grant Write (15m)</button>
          <button type="button" id="queue-live-revoke" ${
            state.pendingLiveControlAction === "revoke"
              ? "disabled"
              : liveSessionCreated && !liveSessionActionsDisabled
                ? ""
                : "disabled"
          }>Revoke Session</button>
          <button type="button" id="queue-live-pause" ${
            state.pendingLiveControlAction === "pause" ? "disabled" : ""
          }>${pauseActive ? "Resume" : "Pause"}</button>
          <button type="button" id="queue-live-takeover" ${
            state.pendingLiveControlAction === "takeover" ? "disabled" : ""
          }>Takeover</button>
        </div>
        <div class="actions">
          <input id="queue-operator-message" placeholder="Send operator message..." />
          <button type="button" id="queue-operator-send" ${
            state.pendingLiveControlAction === "operator-message" ? "disabled" : ""
          }>Send</button>
        </div>
      `;
    };

    const renderEventsTable = () => {
      const bodyNode = document.getElementById("queue-events-body");
      const summaryNode = document.getElementById("queue-events-summary");
      if (!bodyNode || !summaryNode) {
        return;
      }
      if (state.events.length === 0) {
        bodyNode.innerHTML = "<tr><td colspan='4' class='small'>No events.</td></tr>";
        summaryNode.textContent = "No events loaded.";
        return;
      }

      const visibleEvents = state.hasOlderEvents
        ? state.events.slice(0, state.maxVisibleEventRows)
        : state.events.slice(-state.maxVisibleEventRows);
      const hiddenCount = Math.max(0, state.events.length - visibleEvents.length);
      const rows = visibleEvents
        .map((event) => {
          const rawMessage = String(pick(event, "message") || "").replaceAll("\r", "");
          const singleLine = rawMessage.replaceAll("\n", " ");
          const truncated =
            singleLine.length > state.maxEventMessageChars
              ? `${singleLine.slice(0, state.maxEventMessageChars - 1)}...`
              : singleLine;
          const titleText = rawMessage.length > 2048 ? `${rawMessage.slice(0, 2048)}...` : rawMessage;
          return `
            <tr>
              <td>${formatTimestamp(pick(event, "createdAt"))}</td>
              <td>${escapeHtml(deriveStageFromEvent(event))}</td>
              <td>${escapeHtml(pick(event, "level") || "info")}</td>
              <td class="queue-event-message" title="${escapeHtml(titleText)}">${escapeHtml(
                truncated,
              )}</td>
            </tr>
          `;
        })
        .join("");
      bodyNode.innerHTML = rows;
      summaryNode.textContent =
        hiddenCount > 0
          ? `Showing latest ${visibleEvents.length} rows of ${state.events.length} loaded events.`
          : `Showing ${state.events.length} loaded events.`;
    };

    const updateLiveOutputLines = () => {
      const shouldRebuild =
        state.forceLiveOutputRebuild ||
        state.liveOutputRenderedFilter !== state.outputFilter ||
        state.liveOutputRenderedEventCount > state.events.length;

      if (shouldRebuild) {
        const lines = [];
        state.events.forEach((event) => {
          if (eventMatchesOutputFilter(event, state.outputFilter)) {
            lines.push(formatLiveOutputLine(event));
          }
        });
        if (lines.length > state.maxLiveOutputLines) {
          lines.splice(0, lines.length - state.maxLiveOutputLines);
        }
        state.liveOutputLines = lines;
        state.liveOutputRenderedEventCount = state.events.length;
        state.liveOutputRenderedFilter = state.outputFilter;
        state.forceLiveOutputRebuild = false;
        return;
      }

      if (state.liveOutputRenderedEventCount < state.events.length) {
        for (
          let index = state.liveOutputRenderedEventCount;
          index < state.events.length;
          index += 1
        ) {
          const event = state.events[index];
          if (eventMatchesOutputFilter(event, state.outputFilter)) {
            state.liveOutputLines.push(formatLiveOutputLine(event));
          }
        }
        if (state.liveOutputLines.length > state.maxLiveOutputLines) {
          state.liveOutputLines.splice(
            0,
            state.liveOutputLines.length - state.maxLiveOutputLines,
          );
        }
        state.liveOutputRenderedEventCount = state.events.length;
      }
    };

    const renderLiveOutput = () => {
      const outputNode = document.getElementById("queue-live-output");
      if (!outputNode) {
        return;
      }
      updateLiveOutputLines();
      outputNode.textContent = state.liveOutputLines.join("\n");
      if (state.followOutput) {
        outputNode.scrollTop = outputNode.scrollHeight;
      }
    };

    const flushEventPanelsRender = () => {
      renderEventsTable();
      renderLiveOutput();
      renderLoadOlderControls();
    };

    const scheduleEventPanelsRender = ({ forceLiveOutputRebuild = false } = {}) => {
      if (forceLiveOutputRebuild) {
        state.forceLiveOutputRebuild = true;
      }
      if (state.eventsRenderTimer !== null) {
        return;
      }
      state.eventsRenderTimer = window.setTimeout(() => {
        state.eventsRenderTimer = null;
        flushEventPanelsRender();
      }, state.eventsRenderIntervalMs);
    };

    registerDisposer(() => {
      if (state.eventsRenderTimer !== null) {
        clearTimeout(state.eventsRenderTimer);
        state.eventsRenderTimer = null;
      }
    });

    const appendIncomingEvents = (incomingEvents) => {
      let changed = false;
      const ordered = normalizeIncomingEventsAsc(incomingEvents);
      ordered.forEach((event) => {
        const eventId = String(pick(event, "id") || "");
        if (!eventId || state.eventIds.has(eventId)) {
          return;
        }
        state.eventIds.add(eventId);
        state.events.push(event);
        changed = true;
      });
      if (!changed) {
        return false;
      }
      trimEventsToLimit();
      refreshEventCursors();
      scheduleEventPanelsRender();
      return true;
    };

    const prependOlderEvents = (incomingEvents) => {
      const ordered = normalizeIncomingEventsAsc(incomingEvents);
      const toPrepend = [];
      ordered.forEach((event) => {
        const eventId = String(pick(event, "id") || "");
        if (!eventId || state.eventIds.has(eventId)) {
          return;
        }
        state.eventIds.add(eventId);
        toPrepend.push(event);
      });
      if (toPrepend.length === 0) {
        return false;
      }
      state.events = [...toPrepend, ...state.events];
      trimEventsToLimit();
      refreshEventCursors();
      scheduleEventPanelsRender({ forceLiveOutputRebuild: true });
      return true;
    };

    const buildEventsQuery = ({
      limit = 200,
      after = null,
      afterEventId = null,
      before = null,
      beforeEventId = null,
      sort = "asc",
    }) => {
      const queryParams = [`limit=${encodeURIComponent(String(limit))}`];
      if (after) {
        queryParams.push(`after=${encodeURIComponent(String(after))}`);
      }
      if (afterEventId) {
        queryParams.push(`afterEventId=${encodeURIComponent(String(afterEventId))}`);
      }
      if (before) {
        queryParams.push(`before=${encodeURIComponent(String(before))}`);
      }
      if (beforeEventId) {
        queryParams.push(`beforeEventId=${encodeURIComponent(String(beforeEventId))}`);
      }
      if (sort && sort !== "asc") {
        queryParams.push(`sort=${encodeURIComponent(String(sort))}`);
      }
      return `?${queryParams.join("&")}`;
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
        setDetailNotice("");
        renderJobSummary();
        renderLiveSession();
        renderArtifacts();
      } catch (error) {
        console.error("queue detail load failed", error);
        state.job = null;
        state.artifacts = [];
        state.liveSession = null;
        state.liveSessionError = null;
        state.liveSessionRouteMissing = false;
        setDetailNotice("Failed to load queue detail.");
        renderJobSummary();
        renderLiveSession();
        renderArtifacts();
      }
    };

    const loadLatestEvents = async () => {
      const query = buildEventsQuery({ limit: 200, sort: "desc" });
      try {
        const payload = await fetchJson(
          endpoint(queueSourceConfig.events || "/api/queue/jobs/{id}/events", { id: jobId }) +
            query,
        );
        state.events = [];
        state.eventIds.clear();
        const newestFirst = Array.isArray(payload?.items) ? payload.items : [];
        const newestFirstCount = newestFirst.length;
        const orderedAsc = normalizeIncomingEventsAsc(newestFirst);
        orderedAsc.forEach((event) => {
          const eventId = String(pick(event, "id") || "");
          if (!eventId || state.eventIds.has(eventId)) {
            return;
          }
          state.eventIds.add(eventId);
          state.events.push(event);
        });
        refreshEventCursors();
        state.hasOlderEvents = newestFirstCount >= 200;
        scheduleEventPanelsRender({ forceLiveOutputRebuild: true });
      } catch (error) {
        console.error("queue initial event load failed", error);
      }
    };

    const loadNewEvents = async () => {
      const query = buildEventsQuery({
        limit: 200,
        after: state.after,
        afterEventId: state.afterEventId,
      });
      try {
        const payload = await fetchJson(
          endpoint(queueSourceConfig.events || "/api/queue/jobs/{id}/events", { id: jobId }) +
            query,
        );
        appendIncomingEvents(payload?.items || []);
      } catch (error) {
        console.error("queue event poll failed", error);
      }
    };

    const loadOlderEvents = async () => {
      if (state.loadingOlderEvents || !state.oldest || !state.oldestEventId) {
        return;
      }
      state.loadingOlderEvents = true;
      renderLoadOlderControls();
      const query = buildEventsQuery({
        limit: 200,
        before: state.oldest,
        beforeEventId: state.oldestEventId,
        sort: "desc",
      });
      try {
        const payload = await fetchJson(
          endpoint(queueSourceConfig.events || "/api/queue/jobs/{id}/events", { id: jobId }) +
            query,
        );
        const older = Array.isArray(payload?.items) ? payload.items : [];
        const added = prependOlderEvents(older);
        state.hasOlderEvents = older.length >= 200;
        if (!added && older.length === 0) {
          state.hasOlderEvents = false;
        }
      } catch (error) {
        console.error("queue load older events failed", error);
      } finally {
        state.loadingOlderEvents = false;
        renderLoadOlderControls();
      }
    };

    let eventSource = null;

    const stopEventStream = () => {
      if (!eventSource) {
        return;
      }
      eventSource.onmessage = null;
      eventSource.onerror = null;
      eventSource.close();
      eventSource = null;
    };

    registerDisposer(() => stopEventStream());

    const beginPollingEvents = () => {
      if (state.eventsPollingStarted) {
        return;
      }
      state.eventsPollingStarted = true;
      state.eventsTransport = "polling";
      state.eventsTransportStatus = isAutoRefreshActive() ? "active" : "paused";
      renderTransportStatus();
      startPolling(loadNewEvents, pollIntervals.events, {
        runImmediately: isAutoRefreshActive(),
      });
    };

    const startEventStream = () => {
      if (!isAutoRefreshActive()) {
        state.eventsTransport = "sse";
        state.eventsTransportStatus = "paused";
        renderTransportStatus();
        return;
      }
      if (eventSource || state.eventsPollingStarted) {
        return;
      }
      const streamTemplate =
        queueSourceConfig.eventsStream || "/api/queue/jobs/{id}/events/stream";
      if (typeof window.EventSource !== "function") {
        state.eventsTransport = "polling";
        state.eventsTransportStatus = "unsupported";
        renderTransportStatus();
        beginPollingEvents();
        return;
      }

      const query = buildEventsQuery({
        limit: 200,
        after: state.after,
        afterEventId: state.afterEventId,
      });
      const streamUrl = endpoint(streamTemplate, { id: jobId }) + query;
      state.eventsTransport = "sse";
      state.eventsTransportStatus = "connecting";
      renderTransportStatus();

      eventSource = new window.EventSource(streamUrl);

      const handleMessage = (rawData) => {
        if (!rawData || !isAutoRefreshActive()) {
          return;
        }
        try {
          const parsed = JSON.parse(rawData);
          appendIncomingEvents([parsed]);
        } catch (error) {
          console.error("queue event stream parse failed", error);
        }
      };

      eventSource.addEventListener("open", () => {
        state.eventsTransport = "sse";
        state.eventsTransportStatus = "active";
        renderTransportStatus();
      });

      eventSource.addEventListener("queue_event", (event) => {
        if (state.eventsTransportStatus !== "active") {
          state.eventsTransport = "sse";
          state.eventsTransportStatus = "active";
          renderTransportStatus();
        }
        handleMessage(event.data);
      });

      eventSource.onmessage = (event) => {
        if (state.eventsTransportStatus !== "active") {
          state.eventsTransport = "sse";
          state.eventsTransportStatus = "active";
          renderTransportStatus();
        }
        handleMessage(event.data);
      };

      eventSource.onerror = (error) => {
        console.error("queue event stream failed; switching to polling", error);
        state.eventsTransport = "polling";
        state.eventsTransportStatus = "error";
        renderTransportStatus();
        stopEventStream();
        beginPollingEvents();
      };
    };

    const onDetailClick = async (event) => {
      const button = event.target instanceof HTMLElement ? event.target.closest("button") : null;
      if (!(button instanceof HTMLButtonElement)) {
        return;
      }

      if (button.id === "queue-cancel-button") {
        button.disabled = true;
        setCancelNotice("Submitting cancellation request...");
        try {
          await fetchJson(
            endpoint(queueSourceConfig.cancel || "/api/queue/jobs/{id}/cancel", { id: jobId }),
            {
              method: "POST",
              body: JSON.stringify({ reason: "Cancellation requested from dashboard" }),
            },
          );
          setCancelNotice("Cancellation request submitted.");
          await Promise.all([loadNewEvents(), loadDetail()]);
        } catch (error) {
          console.error("queue cancellation request failed", error);
          setCancelNotice("Failed to cancel queue job.", true);
          button.disabled = false;
        }
        return;
      }

      if (button.id === "queue-load-older-events") {
        await loadOlderEvents();
        return;
      }

      const runLiveAction = async (actionKey, action) => {
        state.pendingLiveControlAction = actionKey;
        renderLiveSession();
        try {
          await action();
        } finally {
          state.pendingLiveControlAction = "";
          renderLiveSession();
        }
      };

      if (button.id === "queue-live-enable") {
        await runLiveAction("enable", async () => {
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
            await Promise.all([loadDetail(), loadNewEvents()]);
            setLiveNotice("Live session enabled.");
          } catch (error) {
            console.error("live session enable failed", error);
            setLiveNotice("Failed to enable live session.", true);
          }
        });
        return;
      }

      if (button.id === "queue-live-grant") {
        await runLiveAction("grant", async () => {
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
            await Promise.all([loadDetail(), loadNewEvents()]);
            setLiveNotice("RW access granted.");
          } catch (error) {
            console.error("live session grant failed", error);
            setLiveNotice("Failed to grant write access.", true);
          }
        });
        return;
      }

      if (button.id === "queue-live-revoke") {
        await runLiveAction("revoke", async () => {
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
            await Promise.all([loadDetail(), loadNewEvents()]);
            setLiveNotice("Live session revoked.");
          } catch (error) {
            console.error("live session revoke failed", error);
            setLiveNotice("Failed to revoke live session.", true);
          }
        });
        return;
      }

      if (button.id === "queue-live-pause") {
        await runLiveAction("pause", async () => {
          const action = button.textContent === "Resume" ? "resume" : "pause";
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
            await Promise.all([loadDetail(), loadNewEvents()]);
            setLiveNotice(action === "pause" ? "Pause requested." : "Resume requested.");
          } catch (error) {
            console.error("task control action failed", error);
            setLiveNotice("Failed to apply control action.", true);
          }
        });
        return;
      }

      if (button.id === "queue-live-takeover") {
        await runLiveAction("takeover", async () => {
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
            await Promise.all([loadDetail(), loadNewEvents()]);
            setLiveNotice("Takeover requested.");
          } catch (error) {
            console.error("task takeover action failed", error);
            setLiveNotice("Failed to request takeover.", true);
          }
        });
        return;
      }

      if (button.id === "queue-operator-send") {
        const input = document.getElementById("queue-operator-message");
        const messageText =
          input instanceof HTMLInputElement ? String(input.value || "").trim() : "";
        if (!messageText) {
          return;
        }
        await runLiveAction("operator-message", async () => {
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
            if (input instanceof HTMLInputElement) {
              input.value = "";
            }
            await loadNewEvents();
            setLiveNotice("Operator message sent.");
          } catch (error) {
            console.error("operator message failed", error);
            setLiveNotice("Failed to send operator message.", true);
          }
        });
      }
    };

    detailPage.addEventListener("click", onDetailClick);
    registerDisposer(() => detailPage.removeEventListener("click", onDetailClick));

    const followOutput = document.getElementById("queue-follow-output");
    if (followOutput instanceof HTMLInputElement) {
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
    if (outputFilter instanceof HTMLSelectElement) {
      outputFilter.addEventListener("change", () => {
        state.outputFilter = String(outputFilter.value || "all");
        scheduleEventPanelsRender({ forceLiveOutputRebuild: true });
      });
    }

    const copyOutput = document.getElementById("queue-copy-output");
    if (copyOutput) {
      copyOutput.addEventListener("click", async () => {
        const outputNode = document.getElementById("queue-live-output");
        const content = outputNode ? String(outputNode.textContent || "") : "";
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

    registerDisposer(
      onAutoRefreshChange((enabled) => {
        if (!enabled) {
          stopEventStream();
          state.eventsTransportStatus = "paused";
          renderTransportStatus();
          return;
        }
        if (state.eventsPollingStarted) {
          state.eventsTransport = "polling";
          state.eventsTransportStatus = "active";
          renderTransportStatus();
        } else {
          startEventStream();
        }
        loadDetail();
        loadNewEvents();
      }),
    );

    renderTransportStatus();
    renderLoadOlderControls();
    await loadDetail();
    await loadLatestEvents();
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

  async function renderProposalsListPage() {
    const repoStorageKey = "task-dashboard-proposals-repo";
    const state = {
      status: "open",
      repository: localStorage.getItem(repoStorageKey) || "",
      category: "",
      tag: "",
      includeSnoozed: false,
      rows: [],
      notice: "",
    };

    const renderFilters = () => {
      const statusOptions = [
        ["", "(all)"],
        ["open", "open"],
        ["promoted", "promoted"],
        ["dismissed", "dismissed"],
        ["accepted", "accepted"],
        ["rejected", "rejected"],
        ["snoozed", "snoozed"],
      ]
        .map(
          ([value, label]) =>
            `<option value="${escapeHtml(value)}" ${
              state.status === value ? "selected" : ""
            }>${escapeHtml(label)}</option>`,
        )
        .join("");
      return `
        <form id="proposals-filter-form" class="stack">
          <div class="grid-2">
            <label>Status
              <select name="status">${statusOptions}</select>
            </label>
            <label>Repository
              <input name="repository" placeholder="owner/repo" value="${escapeHtml(
                state.repository,
              )}" />
            </label>
          </div>
          <label>Category
            <input name="category" placeholder="security, tests, ..." value="${escapeHtml(
              state.category,
            )}" />
          </label>
          <label>Signal Tag
            <input name="tag" placeholder="loop_detected" value="${escapeHtml(
              state.tag,
            )}" />
          </label>
          <label class="checkbox stack">
            <span>
              <input type="checkbox" name="includeSnoozed" ${
                state.includeSnoozed ? "checked" : ""
              } />
              Include snoozed proposals
            </span>
          </label>
        </form>
      `;
    };

    const renderTable = () => {
      if (!state.rows.length) {
        return "<p class='small'>No proposals found for the current filters.</p>";
      }
      const filteredRows = state.rows.filter((row) => {
        if (!state.tag) {
          return true;
        }
        const tagNeedle = state.tag.toLowerCase();
        const tagList = (pick(row, "tags") || []).map((tag) =>
          String(tag || "").toLowerCase(),
        );
        return tagList.includes(tagNeedle);
      });
      const rows = filteredRows
        .map((row) => {
          const id = pick(row, "id");
          const preview = pick(row, "taskPreview") || {};
          const origin = pick(row, "origin") || {};
          const originSource = pick(origin, "source") || "-";
          const originLink =
            originSource === "queue" && pick(origin, "id")
              ? `<a href="/tasks/queue/${escapeHtml(
                  String(pick(origin, "id") || ""),
                )}">queue/${escapeHtml(String(pick(origin, "id") || ""))}</a>`
              : escapeHtml(originSource);
          const repo = pick(row, "repository") || pick(preview, "repository") || "-";
          const instructions = pick(preview, "instructions") || "";
          const tags = (pick(row, "tags") || []).map((tag) => escapeHtml(tag)).join(", ");
          const priority = (pick(row, "reviewPriority") || "normal").toUpperCase();
          const overrideReason = pick(row, "priorityOverrideReason");
          const priorityBadge = `<span class="badge priority-${escapeHtml(
            priority.toLowerCase(),
          )}" ${
            overrideReason
              ? `title="Override: ${escapeHtml(String(overrideReason))}"`
              : ""
          }>${escapeHtml(priority)}</span>`;
          const snoozedUntil = pick(row, "snoozedUntil");
          const snoozedDisplay = snoozedUntil
            ? `${formatTimestamp(snoozedUntil)}`
            : "-";
          const dedupHash = (pick(row, "dedupHash") || "").toString();
          const dedupShort = dedupHash ? dedupHash.slice(0, 8) : "-";
          return `
            <tr>
              <td><a href="/tasks/proposals/${encodeURIComponent(
                String(id || ""),
              )}">${escapeHtml(String(id || "").slice(0, 8) || "-")}</a></td>
              <td>${escapeHtml(pick(row, "title") || "(untitled)")}</td>
              <td>${escapeHtml(repo)}</td>
              <td>${escapeHtml(pick(row, "category") || "-")}</td>
              <td>${priorityBadge}</td>
              <td>${statusBadge("proposals", pick(row, "status"))}</td>
              <td>${formatTimestamp(pick(row, "createdAt"))}</td>
              <td>${originLink}</td>
              <td>${escapeHtml(tags || "-")}</td>
              <td>${escapeHtml(snoozedDisplay)}</td>
              <td><code>${escapeHtml(dedupShort)}</code></td>
              <td>
                <div class="stack compact">
                  <button type="button" class="secondary proposal-action" data-action="promote" data-proposal-id="${escapeHtml(
                    String(id || ""),
                  )}">Promote</button>
                  <button type="button" class="danger proposal-action" data-action="dismiss" data-proposal-id="${escapeHtml(
                    String(id || ""),
                  )}">Dismiss</button>
                </div>
              </td>
            </tr>
            ${
              instructions
                ? `<tr><td colspan="12"><span class="small">${escapeHtml(
                    instructions,
                  )}</span><br/><span class="tiny">Dedup Hash: <code>${escapeHtml(
                    dedupHash || "-",
                  )}</code></span></td></tr>`
                : ""
            }
          `;
        })
        .join("");

      return `
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Title</th>
              <th>Repository</th>
              <th>Category</th>
              <th>Priority</th>
              <th>Status</th>
              <th>Created</th>
              <th>Origin</th>
              <th>Tags</th>
              <th>Snoozed Until</th>
              <th>Dedup</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    };

    const attachHandlers = () => {
      const filterForm = document.getElementById("proposals-filter-form");
      if (!filterForm) {
        return;
      }
      const statusField = filterForm.elements.namedItem("status");
      const repositoryField = filterForm.elements.namedItem("repository");
      const categoryField = filterForm.elements.namedItem("category");
      const tagField = filterForm.elements.namedItem("tag");
      const includeSnoozedField = filterForm.elements.namedItem("includeSnoozed");
      if (statusField) {
        statusField.addEventListener("change", () => {
          state.status = String(statusField.value || "").trim();
          load();
        });
      }
      if (repositoryField) {
        repositoryField.addEventListener("change", () => {
          state.repository = String(repositoryField.value || "").trim();
          localStorage.setItem(repoStorageKey, state.repository);
          load();
        });
      }
      if (categoryField) {
        categoryField.addEventListener("change", () => {
          state.category = String(categoryField.value || "").trim();
          load();
        });
      }
      if (tagField) {
        tagField.addEventListener("change", () => {
          state.tag = String(tagField.value || "").trim();
          load();
        });
      }
      if (includeSnoozedField) {
        includeSnoozedField.addEventListener("change", () => {
          state.includeSnoozed = Boolean(includeSnoozedField.checked);
          load();
        });
      }
      document.querySelectorAll(".proposal-action").forEach((button) => {
        button.addEventListener("click", async () => {
          const proposalId = button.getAttribute("data-proposal-id");
          const action = button.getAttribute("data-action");
          if (!proposalId || !action) {
            return;
          }
          button.disabled = true;
          try {
            if (action === "promote") {
              const response = await apiPromoteProposal(proposalId);
              const jobId = pick(response, "job", "id");
              if (jobId) {
                window.location.href = `/tasks/queue/${encodeURIComponent(String(jobId))}`;
                return;
              }
            } else if (action === "dismiss") {
              await apiDismissProposal(proposalId);
            }
            await load();
          } catch (error) {
            console.error(`proposal ${action} failed`, error);
            state.notice = `Failed to ${action} proposal ${proposalId}.`;
            renderView();
          } finally {
            button.disabled = false;
          }
        });
      });
    };

    const renderView = () => {
      const noticeHtml = state.notice
        ? `<div class="notice error">${escapeHtml(state.notice)}</div>`
        : "";
      setView(
        "Task Proposals",
        "Worker follow-up queue (promote to Task jobs).",
        `${noticeHtml}${renderFilters()}${renderTable()}`,
      );
      attachHandlers();
    };

    const load = async () => {
      try {
        const params = new URLSearchParams();
        params.set("limit", "200");
        if (state.status) {
          params.set("status", state.status);
        }
        if (state.repository) {
          params.set("repository", state.repository);
        }
        if (state.category) {
          params.set("category", state.category);
        }
        if (state.includeSnoozed) {
          params.set("includeSnoozed", "true");
        }
        const listEndpoint = proposalsSourceConfig.list || "/api/proposals";
        const payload = await fetchJson(`${listEndpoint}?${params.toString()}`);
        state.rows = payload?.items || [];
        state.notice = "";
      } catch (error) {
        console.error("proposals list load failed", error);
        state.rows = [];
        state.notice = "Failed to load proposals.";
      }
      renderView();
    };

    setView(
      "Task Proposals",
      "Worker follow-up queue (promote to Task jobs).",
      "<p class='loading'>Loading proposals...</p>",
    );
    await load();
    startPolling(load, pollIntervals.list);
  }

  async function renderProposalDetailPage(proposalId) {
    setView(
      "Proposal Detail",
      `Proposal ${proposalId}`,
      "<p class='loading'>Loading proposal...</p>",
    );

    const renderDetail = (row) => {
      const origin = pick(row, "origin") || {};
      const preview = pick(row, "taskPreview") || {};
      const taskRequest = pick(row, "taskCreateRequest") || {};
      const payloadNode =
        taskRequest && typeof taskRequest === "object" && taskRequest.payload && typeof taskRequest.payload === "object"
          ? taskRequest.payload
          : {};
      const taskNode =
        payloadNode && payloadNode.task && typeof payloadNode.task === "object"
          ? payloadNode.task
          : {};
      const instructions = preview.instructions || taskNode.instructions || "";
      const originSource = pick(origin, "source") || "-";
      const metadata = pick(origin, "metadata") || {};
      const originLink =
        originSource === "queue" && pick(origin, "id")
          ? `<a href="/tasks/queue/${escapeHtml(
              String(pick(origin, "id") || ""),
            )}">queue/${escapeHtml(String(pick(origin, "id") || ""))}</a>`
          : escapeHtml(originSource);
      const priority = (pick(row, "reviewPriority") || "normal").toUpperCase();
      const priorityOverride = pick(row, "priorityOverrideReason") || "";
      const dedupHash = pick(row, "dedupHash") || "-";
      const snoozedUntil = pick(row, "snoozedUntil");
      const snoozeNote = pick(row, "snoozeNote") || "";
      const triggerRepo = pick(metadata, "triggerRepo") || "-";
      const triggerJobId = pick(metadata, "triggerJobId") || "-";
      const signalMetadata = pick(metadata, "signal");
      const signalMarkup = signalMetadata
        ? `<pre>${escapeHtml(JSON.stringify(signalMetadata, null, 2))}</pre>`
        : "<p class='small'>No signal metadata supplied.</p>";
      const snoozedDisplay = snoozedUntil ? formatTimestamp(snoozedUntil) : "-";
      const similar = pick(row, "similar") || [];
      const similarMarkup = similar.length
        ? `<ul class="stack">${similar
            .map(
              (item) =>
                `<li><a href="/tasks/proposals/${encodeURIComponent(
                  String(pick(item, "id") || ""),
                )}">${escapeHtml(pick(item, "title") || "(untitled)")}</a> &middot; ${escapeHtml(
                  pick(item, "repository") || "-",
                )} &middot; ${formatTimestamp(pick(item, "createdAt"))}</li>`,
            )
            .join("")}</ul>`
        : "<p class='small'>No similar proposals.</p>";
      const priorityOptions = ["low", "normal", "high", "urgent"]
        .map(
          (value) =>
            `<option value="${escapeHtml(value)}" ${
              value.toUpperCase() === priority ? "selected" : ""
            }>${escapeHtml(value.toUpperCase())}</option>`,
        )
        .join("");
      setView(
        "Proposal Detail",
        `Proposal ${proposalId}`,
        `
          <div class="grid-2">
            <div class="card"><strong>Status:</strong> ${statusBadge(
              "proposals",
              pick(row, "status"),
            )}</div>
            <div class="card"><strong>Repository:</strong> ${escapeHtml(
              pick(row, "repository") || pick(preview, "repository") || "-",
            )}</div>
            <div class="card"><strong>Runtime:</strong> ${escapeHtml(
              pick(preview, "runtimeMode") || "-",
            )}</div>
            <div class="card"><strong>Publish Mode:</strong> ${escapeHtml(
              pick(preview, "publishMode") || "-",
            )}</div>
            <div class="card"><strong>Category:</strong> ${escapeHtml(
              pick(row, "category") || "-",
            )}</div>
            <div class="card"><strong>Origin:</strong> ${originLink}</div>
            <div class="card"><strong>Priority:</strong> ${escapeHtml(priority)}${
              priorityOverride
                ? `<br/><span class="tiny">Override: ${escapeHtml(priorityOverride)}</span>`
                : ""
            }</div>
            <div class="card"><strong>Dedup Hash:</strong> <code>${escapeHtml(
              dedupHash,
            )}</code></div>
            <div class="card"><strong>Snoozed Until:</strong> ${escapeHtml(
              snoozedDisplay,
            )}</div>
            <div class="card"><strong>Trigger Repo:</strong> ${escapeHtml(
              triggerRepo,
            )}</div>
            <div class="card"><strong>Trigger Job ID:</strong> ${escapeHtml(
              triggerJobId,
            )}</div>
          </div>
          <section>
            <h3>Summary</h3>
            <p>${escapeHtml(pick(row, "summary") || "-")}</p>
          </section>
          <section>
            <h3>Instructions</h3>
            <pre>${escapeHtml(instructions || "-")}</pre>
          </section>
          <section>
            <h3>Signal Metadata</h3>
            ${signalMarkup}
          </section>
          <div class="actions">
            <button type="button" id="proposal-promote-button">Promote to Task</button>
            <button type="button" class="secondary" id="proposal-edit-button">Edit & Promote</button>
            <button type="button" class="secondary" id="proposal-dismiss-button">Dismiss</button>
            <a href="/tasks/proposals"><button type="button" class="secondary">Back</button></a>
          </div>
          <section class="stack">
            <h3>Priority & Snooze</h3>
            <div class="grid-2">
              <form id="proposal-priority-form" class="stack card">
                <label>Priority
                  <select name="priority">${priorityOptions}</select>
                </label>
                <button type="submit">Update Priority</button>
              </form>
              <form id="proposal-snooze-form" class="stack card">
                <label>Snooze Until
                  <input type="datetime-local" name="until" />
                </label>
                <label>Note
                  <input type="text" name="note" placeholder="Optional context" />
                </label>
                <div class="stack compact">
                  <button type="submit">Snooze</button>
                  <button type="button" class="secondary" id="proposal-unsnooze-button"${
                    snoozedUntil ? "" : " disabled"
                  }>Unsnooze</button>
                </div>
                ${
                  snoozeNote
                    ? `<p class="small">Latest note: ${escapeHtml(snoozeNote)}</p>`
                    : ""
                }
              </form>
            </div>
          </section>
          <section>
            <h3>Similar Proposals</h3>
            ${similarMarkup}
          </section>
        `,
      );
      const promoteButton = document.getElementById("proposal-promote-button");
      if (promoteButton) {
        promoteButton.addEventListener("click", async () => {
          promoteButton.disabled = true;
          try {
            const response = await apiPromoteProposal(proposalId);
            const jobId = pick(response, "job", "id");
            if (jobId) {
              window.location.href = `/tasks/queue/${encodeURIComponent(String(jobId))}`;
              return;
            }
            await load(true);
          } catch (error) {
            console.error("proposal promote failed", error);
            setView(
              "Proposal Detail",
              `Proposal ${proposalId}`,
              "<div class='notice error'>Promotion failed.</div>",
            );
          } finally {
            promoteButton.disabled = false;
          }
        });
      }
      const dismissButton = document.getElementById("proposal-dismiss-button");
      if (dismissButton) {
        dismissButton.addEventListener("click", async () => {
          dismissButton.disabled = true;
          try {
            await apiDismissProposal(proposalId);
            await load(true);
          } catch (error) {
            console.error("proposal dismiss failed", error);
            setView(
              "Proposal Detail",
              `Proposal ${proposalId}`,
              "<div class='notice error'>Dismissal failed.</div>",
            );
          } finally {
            dismissButton.disabled = false;
          }
        });
      }
      const editButton = document.getElementById("proposal-edit-button");
      if (editButton) {
        editButton.addEventListener("click", async () => {
          const overrides = buildEditOverrides(row);
          if (!overrides) {
            return;
          }
          editButton.disabled = true;
          try {
            const response = await apiPromoteProposal(proposalId, overrides);
            const jobId = pick(response, "job", "id");
            if (jobId) {
              window.location.href = `/tasks/queue/${encodeURIComponent(String(jobId))}`;
              return;
            }
            await load(true);
          } catch (error) {
            console.error("proposal edit-promote failed", error);
            setView(
              "Proposal Detail",
              `Proposal ${proposalId}`,
              "<div class='notice error'>Edit & Promote failed.</div>",
            );
          } finally {
            editButton.disabled = false;
          }
        });
      }
      const priorityForm = document.getElementById("proposal-priority-form");
      if (priorityForm) {
        priorityForm.addEventListener("submit", async (event) => {
          event.preventDefault();
          const priorityField = priorityForm.elements.namedItem("priority");
          const value = priorityField ? priorityField.value : null;
          if (!value) {
            return;
          }
          priorityForm.classList.add("loading");
          try {
            await apiUpdateProposalPriority(proposalId, value);
            await load(true);
          } catch (error) {
            console.error("priority update failed", error);
            alert("Failed to update priority.");
          } finally {
            priorityForm.classList.remove("loading");
          }
        });
      }
      const snoozeForm = document.getElementById("proposal-snooze-form");
      if (snoozeForm) {
        snoozeForm.addEventListener("submit", async (event) => {
          event.preventDefault();
          const untilField = snoozeForm.elements.namedItem("until");
          const noteField = snoozeForm.elements.namedItem("note");
          const rawValue = untilField ? untilField.value : "";
          if (!rawValue) {
            alert("Select a snooze timestamp first.");
            return;
          }
          const isoValue = new Date(rawValue).toISOString();
          snoozeForm.classList.add("loading");
          try {
            await apiSnoozeProposal(
              proposalId,
              isoValue,
              noteField ? noteField.value : null,
            );
            await load(true);
          } catch (error) {
            console.error("snooze failed", error);
            alert("Failed to snooze proposal.");
          } finally {
            snoozeForm.classList.remove("loading");
          }
        });
      }
      const unsnoozeButton = document.getElementById("proposal-unsnooze-button");
      if (unsnoozeButton) {
        unsnoozeButton.addEventListener("click", async () => {
          unsnoozeButton.disabled = true;
          try {
            await apiUnsnoozeProposal(proposalId);
            await load(true);
          } catch (error) {
            console.error("unsnooze failed", error);
            alert("Failed to unsnooze proposal.");
          } finally {
            unsnoozeButton.disabled = false;
          }
        });
      }
    };

    const load = async (silent = false) => {
      try {
        const detail = await fetchJson(
          endpoint(
            proposalsSourceConfig.detail || "/api/proposals/{id}",
            { id: proposalId },
          ),
        );
        renderDetail(detail);
      } catch (error) {
        console.error("proposal detail load failed", error);
        if (!silent) {
          setView(
            "Proposal Detail",
            `Proposal ${proposalId}`,
            "<div class='notice error'>Failed to load proposal.</div>",
          );
        }
      }
    };

    await load();
    startPolling(() => load(true), pollIntervals.detail);
  }

  async function renderSystemSettingsPage() {
    if (!workerPauseTransport) {
      setView(
        "System Settings",
        "Pause or resume worker processing.",
        "<div class='notice error'>Worker pause controls are not configured for this deployment.</div>",
      );
      return;
    }

    const numberFormatter = new Intl.NumberFormat();
    const state = {
      snapshot: null,
      notice: null,
      requestInFlight: false,
    };
    let hasRendered = false;

    function setNotice(level, text) {
      if (!text) {
        state.notice = null;
        return;
      }
      state.notice = {
        level: level === "error" ? "error" : "ok",
        text,
      };
    }

    function buildMetricsMarkup(metrics = {}) {
      const entries = [
        { key: "queued", label: "Queued" },
        { key: "running", label: "Running" },
        { key: "staleRunning", label: "Stale" },
        { key: "isDrained", label: "Drained" },
      ];
      return `
        <div class="system-settings-metrics">
          ${entries
            .map((entry) => {
              const value =
                entry.key === "isDrained"
                  ? metrics.isDrained
                    ? "Yes"
                    : "No"
                  : numberFormatter.format(
                      typeof metrics[entry.key] === "number" ? metrics[entry.key] : 0,
                    );
              return `
                <div class="system-settings-metric">
                  <span class="label">${escapeHtml(entry.label)}</span>
                  <span class="value">${escapeHtml(value)}</span>
                </div>
              `;
            })
            .join("")}
        </div>
      `;
    }

    function buildAuditMarkup(events = []) {
      if (!Array.isArray(events) || events.length === 0) {
        return "<p class='small'>No recent pause or resume actions.</p>";
      }
      return `
        <ul>
          ${events
            .map((event) => {
              const actionLabel =
                String(event?.action || "")
                  .trim()
                  .toUpperCase() || "-";
              const modeLabel = event?.mode
                ? ` | ${String(event.mode).toUpperCase()}`
                : "";
              const reason = event?.reason ? escapeHtml(event.reason) : "(no reason)";
              const timestamp = formatTimestamp(event?.createdAt);
              return `
                <li>
                  <strong>${escapeHtml(actionLabel)}${escapeHtml(modeLabel)}</strong>
                  <span>${reason}</span>
                  <time datetime="${escapeHtml(event?.createdAt || "")}">${escapeHtml(timestamp)}</time>
                </li>
              `;
            })
            .join("")}
        </ul>
      `;
    }

    function renderView() {
      const noticeHtml = state.notice
        ? `<div class="notice ${state.notice.level}">${escapeHtml(state.notice.text)}</div>`
        : "";
      const controlsMarkup = `
        <section class="card system-settings-forms">
          <h3>Worker Controls</h3>
          <p class="form-caption">
            Drain lets running jobs finish; Quiesce stops new claims immediately.
          </p>
          <form data-system-settings-form="pause" class="stack">
            <fieldset>
              <legend>Pause Workers</legend>
              <label>
                Mode
                <select name="mode" required>
                  <option value="drain" selected>Drain (default)</option>
                  <option value="quiesce">Quiesce</option>
                </select>
              </label>
              <label>
                Reason
                <input type="text" name="reason" maxlength="160" required />
              </label>
              <button type="submit">Pause Workers</button>
            </fieldset>
          </form>
          <div class="system-settings-divider"></div>
          <form data-system-settings-form="resume" class="stack">
            <fieldset>
              <legend>Resume Workers</legend>
              <label>
                Reason
                <input type="text" name="reason" maxlength="160" required />
              </label>
              <button type="submit">Resume Workers</button>
            </fieldset>
          </form>
        </section>
      `;
      const layout = `
        <div data-system-settings-notice>${noticeHtml}</div>
        <div class="system-settings">
          <section class="card">
            <div data-system-settings-summary></div>
          </section>
          <div class="system-settings-grid">
            ${controlsMarkup}
            <section class="system-settings-audit">
              <h3>Recent Actions</h3>
              <div data-system-settings-audit></div>
            </section>
          </div>
        </div>
      `;
      setView(
        "System Settings",
        "Pause or resume worker processing.",
        layout,
      );
      attachHandlers();
      syncDynamicView();
      hasRendered = true;
    }

    function syncDynamicView() {
      const summaryNode = document.querySelector("[data-system-settings-summary]");
      const auditNode = document.querySelector("[data-system-settings-audit]");
      const noticeNode = document.querySelector("[data-system-settings-notice]");
      if (!summaryNode || !auditNode || !noticeNode) {
        return;
      }

      const hasSnapshot = Boolean(state.snapshot);
      const system = (state.snapshot && state.snapshot.system) || {};
      const metrics = (state.snapshot && state.snapshot.metrics) || {};
      const auditEvents =
        (state.snapshot && state.snapshot.audit && state.snapshot.audit.latest) || [];
      const description = describeWorkerPauseState(system, metrics);

      summaryNode.innerHTML = hasSnapshot
        ? `
            <div class="stack">
              <div class="stack">
                <h3>${escapeHtml(description.label)}</h3>
                <p class="page-meta">${escapeHtml(description.reason)}</p>
                <p class="small">
                  Mode: ${escapeHtml((system.mode || description.state).toString())} | Version: ${escapeHtml(
            (system.version ?? "-").toString(),
          )} | Updated: ${escapeHtml(
            system.updatedAt ? formatTimestamp(system.updatedAt) : "-",
          )}
                </p>
              </div>
              ${buildMetricsMarkup(metrics)}
            </div>
          `
        : "<p class='loading'>Loading worker status...</p>";
      auditNode.innerHTML = buildAuditMarkup(auditEvents);
      noticeNode.innerHTML = state.notice
        ? `<div class="notice ${state.notice.level}">${escapeHtml(state.notice.text)}</div>`
        : "";
    }

    function attachHandlers() {
      const pauseForm = document.querySelector('[data-system-settings-form="pause"]');
      const resumeForm = document.querySelector('[data-system-settings-form="resume"]');
      const formsRoot = document.querySelector(".system-settings-forms");

      const toggleForms = (disabled) => {
        if (!formsRoot) {
          return;
        }
        const inputs = formsRoot.querySelectorAll("input, select, textarea, button");
        inputs.forEach((node) => {
          if (disabled) {
            node.setAttribute("disabled", "disabled");
          } else {
            node.removeAttribute("disabled");
          }
        });
      };

      if (state.requestInFlight) {
        toggleForms(true);
      }

      pauseForm?.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (state.requestInFlight) {
          return;
        }
        const formData = new FormData(pauseForm);
        const mode = String(formData.get("mode") || "").trim();
        const reason = String(formData.get("reason") || "").trim();
        if (!mode || !reason) {
          setNotice("error", "Pause mode and reason are required.");
          renderView();
          return;
        }
        state.requestInFlight = true;
        toggleForms(true);
        try {
          const snapshot = await workerPauseTransport.submitAction({
            action: "pause",
            mode,
            reason,
          });
          state.snapshot = snapshot;
          setNotice("ok", "Workers paused successfully.");
          pauseForm.reset();
        } catch (error) {
          console.error("worker pause request failed", error);
          setNotice("error", "Failed to pause workers.");
        } finally {
          state.requestInFlight = false;
          renderView();
        }
      });

      resumeForm?.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (state.requestInFlight) {
          return;
        }
        const formData = new FormData(resumeForm);
        const reason = String(formData.get("reason") || "").trim();
        if (!reason) {
          setNotice("error", "Resume reason is required.");
          renderView();
          return;
        }
        let forceResume = false;
        if (requiresResumeConfirmation(state.snapshot)) {
          const confirmed = window.confirm(
            "Workers are not drained yet. Resume anyway?",
          );
          if (!confirmed) {
            return;
          }
          forceResume = true;
        }
        state.requestInFlight = true;
        toggleForms(true);
        try {
          const snapshot = await workerPauseTransport.submitAction({
            action: "resume",
            reason,
            forceResume,
          });
          state.snapshot = snapshot;
          setNotice("ok", "Workers resumed successfully.");
          resumeForm.reset();
        } catch (error) {
          console.error("worker resume request failed", error);
          setNotice("error", "Failed to resume workers.");
        } finally {
          state.requestInFlight = false;
          renderView();
        }
      });
    }

    const load = async (silent = false) => {
      try {
        state.snapshot = await workerPauseTransport.fetchState();
        if (!silent) {
          setNotice(null, "");
        }
      } catch (error) {
        console.error("system settings load failed", error);
        if (!silent) {
          const message =
            error instanceof Error && error.message
              ? error.message
              : "Failed to load worker pause status.";
          setNotice("error", message);
        }
      }
      if (silent && hasRendered) {
        syncDynamicView();
        return;
      }
      renderView();
    };

    setView(
      "System Settings",
      "Pause or resume worker processing.",
      "<p class='loading'>Loading system controls...</p>",
    );
    await load();
    startPolling(() => load(true), workerPauseTransport.pollInterval);
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
    const proposalDetailMatch = pathname.match(/^\/tasks\/proposals\/([^/]+)$/);

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
    if (pathname === "/tasks/manifests") {
      await renderManifestListPage();
      return;
    }
    if (pathname === "/tasks/manifests/new") {
      renderManifestSubmitPage();
      return;
    }
    if (pathname === "/tasks/proposals") {
      await renderProposalsListPage();
      return;
    }
    if (pathname === "/tasks/settings") {
      await renderSystemSettingsPage();
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
    if (proposalDetailMatch) {
      await renderProposalDetailPage(proposalDetailMatch[1]);
      return;
    }

    renderNotFound();
  }

  const workerPauseController = initWorkerPauseBanner(workerPauseTransport);
  if (workerPauseController) {
    startPolling(
      () => workerPauseController.refresh(),
      workerPauseController.pollInterval,
      { runImmediately: true, skipAutoRefresh: true, persistent: true },
    );
  }

  const disposeTheme = initTheme();
  window.addEventListener("beforeunload", () => {
    stopPolling();
    stopPersistentPolling();
    if (typeof disposeTheme === "function") {
      disposeTheme();
    }
  });
  renderForPath(window.location.pathname).catch((error) => {
    console.error("dashboard render failed", error);
    setView(
      "Dashboard Error",
      "Unexpected rendering failure.",
      "<div class='notice error'>Unexpected dashboard rendering failure.</div>",
    );
  });
})();
