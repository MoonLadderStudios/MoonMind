// One-line summaries for the Create page's collapsed option disclosures. A
// closed disclosure keeps its fields mounted and submitted, so its summary must
// state every non-default value it holds.

export type CreateScheduleMode = "immediate" | "once" | "deferred_minutes" | "recurring";

export function summarizeSchedule({
  mode,
  scheduledFor = "",
  deferredMinutes = "",
  cron = "",
  timezone = "",
}: {
  mode: CreateScheduleMode;
  scheduledFor?: string;
  deferredMinutes?: string;
  cron?: string;
  timezone?: string;
}): string {
  if (mode === "once") {
    const when = scheduledFor.trim().replace("T", " ");
    return when ? `Runs once at ${when}` : "Runs once (choose a time)";
  }
  if (mode === "deferred_minutes") {
    const minutes = deferredMinutes.trim();
    if (!minutes) {
      return "Runs later (choose minutes)";
    }
    return `Runs in ${minutes} ${minutes === "1" ? "minute" : "minutes"}`;
  }
  if (mode === "recurring") {
    const expression = cron.trim();
    return expression
      ? `Recurring: ${expression} (${timezone.trim() || "UTC"})`
      : "Recurring (choose a cron schedule)";
  }
  return "Runs now";
}

export function summarizeDependencies(titles: string[]): string {
  if (titles.length === 0) {
    return "No prerequisites";
  }
  if (titles.length === 1) {
    return `Waits for ${titles[0]}`;
  }
  return `Waits for ${titles.length} workflows`;
}

export function summarizeReport(enabled: boolean): string {
  return enabled ? "Report: on" : "Report: off";
}

export function summarizeOverrides({
  priority,
  maxAttempts,
  hostPolicy,
  stepOverrideCount,
}: {
  /** Non-default values only; null when the default applies. */
  priority: number | null;
  maxAttempts: number | null;
  hostPolicy: string | null;
  stepOverrideCount: number;
}): string {
  const parts = [
    priority !== null ? `priority ${priority}` : null,
    maxAttempts !== null ? `max attempts ${maxAttempts}` : null,
    hostPolicy ? `host policy ${hostPolicy}` : null,
    stepOverrideCount > 0
      ? `${stepOverrideCount} step ${stepOverrideCount === 1 ? "override" : "overrides"}`
      : null,
  ].filter(Boolean);
  return parts.length > 0 ? `Overrides: ${parts.join(", ")}` : "";
}
