import re

with open("api_service/static/task_dashboard/dashboard.js", "r") as f:
    content = f.read()

content = re.sub(
    r'  const temporalCompatibilityConfig =.*?  const taskResolutionEndpoint = String\([^)]+\);\n',
    '',
    content,
    flags=re.DOTALL
)

extract_new = """  function extractTemporalCompatibilityExecution(payload) {
    if (!payload || typeof payload !== "object") {
      return null;
    }
    const executionField = "execution";
    const candidate = payload[executionField];
    return candidate && typeof candidate === "object" ? candidate : null;
  }"""

content = re.sub(
    r'  function extractTemporalCompatibilityExecution\(payload\) \{.*?return candidate && typeof candidate === "object" \? candidate : null;\n  \}',
    extract_new,
    content,
    flags=re.DOTALL
)

freshness_new = """  function describeTemporalCompatibilityFreshness(payload) {
    const refreshField = "refresh";
    const staleField = "staleState";
    const refreshedAtField = "refreshedAt";
    const degradedCountField = "degradedCount";
    const refresh =
      payload &&
        typeof payload === "object" &&
        payload[refreshField] &&
        typeof payload[refreshField] === "object"
        ? payload[refreshField]
        : null;
    return {
      stale: Boolean(
        (payload && typeof payload === "object" && payload[staleField]) ||
        (refresh && refresh.listStale),
      ),
      refetchSuggested: Boolean(refresh && refresh.refetchSuggested),
      refreshedAt:
        (payload && typeof payload === "object" && payload[refreshedAtField]) ||
        (refresh && refresh.refreshedAt) ||
        null,
      degradedCount: Boolean(
        payload && typeof payload === "object" && payload[degradedCountField],
      ),
    };
  }"""

content = re.sub(
    r'  function describeTemporalCompatibilityFreshness\(payload\) \{.*?    \};\n  \}',
    freshness_new,
    content,
    flags=re.DOTALL
)

resolve_new = """  async function resolveUnifiedTaskSource(taskId, sourceHint = "") {
    const safeTaskId = normalizeDashboardDetailSegment(taskId);
    if (!safeTaskId) {
      return { source: "", resolvedId: "" };
    }
    return { source: "temporal", resolvedId: safeTaskId };
  }"""

content = re.sub(
    r'  async function resolveUnifiedTaskSource\(taskId, sourceHint = ""\) \{.*?      \}\n    \}\n  \}',
    resolve_new,
    content,
    flags=re.DOTALL
)

with open("api_service/static/task_dashboard/dashboard.js", "w") as f:
    f.write(content)
