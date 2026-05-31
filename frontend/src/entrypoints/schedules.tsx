import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { BootPayload } from "../boot/parseBootPayload";
import { DataTable } from "../components/tables/DataTable";

const ScheduleSchema = z
  .object({
    id: z.string(),
    name: z.string(),
    description: z.string().nullable().optional(),
    enabled: z.boolean().default(true),
    scheduleType: z.string().optional(),
    cron: z.string().optional(),
    timezone: z.string().optional(),
    nextRunAt: z.string().nullable().optional(),
    lastScheduledFor: z.string().nullable().optional(),
    lastDispatchStatus: z.string().nullable().optional(),
    lastDispatchError: z.string().nullable().optional(),
    scopeType: z.string().nullable().optional(),
    scopeRef: z.string().nullable().optional(),
    target: z.record(z.string(), z.unknown()).optional(),
    policy: z.record(z.string(), z.unknown()).optional(),
    updatedAt: z.string().nullable().optional(),
  })
  .passthrough();

const SchedulesResponseSchema = z.object({
  items: z.array(ScheduleSchema),
});

type Schedule = z.infer<typeof ScheduleSchema>;
type ScheduleFilter = "all" | "enabled" | "paused" | "needs_attention";

const SchedulesBootDataSchema = z
  .object({
    sources: z
      .object({
        schedules: z
          .object({
            list: z.string().optional(),
            update: z.string().optional(),
            runNow: z.string().optional(),
          })
          .partial()
          .optional(),
      })
      .partial()
      .optional(),
  })
  .passthrough();

function endpoint(template: string, scheduleId: string): string {
  return template.replace("{id}", encodeURIComponent(scheduleId));
}

function scheduleSources(payload: BootPayload) {
  const parsed = SchedulesBootDataSchema.safeParse(payload.initialData || {});
  const schedules = parsed.success ? parsed.data.sources?.schedules : undefined;
  return {
    list: schedules?.list || "/api/recurring-tasks?scope=personal",
    update: schedules?.update || "/api/recurring-tasks/{id}",
    runNow: schedules?.runNow || "/api/recurring-tasks/{id}/run",
  };
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "-";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

function formatStatus(value: string | null | undefined): string {
  if (!value) return "No dispatch yet";
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function targetLabel(schedule: Schedule): string {
  const target = schedule.target || {};
  const kind = typeof target.kind === "string" ? target.kind : "";
  const workflow =
    typeof target.workflowType === "string"
      ? target.workflowType
      : typeof target.workflow_type === "string"
        ? target.workflow_type
        : "";
  if (kind && workflow) return `${kind} -> ${workflow}`;
  if (kind) return kind;
  if (workflow) return workflow;
  return "Task";
}

function needsAttention(schedule: Schedule): boolean {
  const status = (schedule.lastDispatchStatus || "").toLowerCase();
  return (
    Boolean(schedule.lastDispatchError) ||
    status.includes("fail") ||
    status.includes("error")
  );
}

function filterSchedules(items: Schedule[], filter: ScheduleFilter): Schedule[] {
  if (filter === "enabled") return items.filter((item) => item.enabled);
  if (filter === "paused") return items.filter((item) => !item.enabled);
  if (filter === "needs_attention") return items.filter(needsAttention);
  return items;
}

export function SchedulesPage({ payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const sources = useMemo(() => scheduleSources(payload), [payload]);
  const [filter, setFilter] = useState<ScheduleFilter>("all");
  const [feedback, setFeedback] = useState<{
    tone: "ok" | "error";
    text: string;
  } | null>(null);

  const queryKey = ["schedules", sources.list] as const;
  const { data, isLoading, isError, error } = useQuery({
    queryKey,
    queryFn: async () => {
      const response = await fetch(sources.list, { credentials: "include" });
      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }
      return SchedulesResponseSchema.parse(await response.json());
    },
  });

  const toggleMutation = useMutation({
    mutationFn: async (schedule: Schedule) => {
      const response = await fetch(endpoint(sources.update, schedule.id), {
        method: "PATCH",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ enabled: !schedule.enabled }),
      });
      if (!response.ok) {
        throw new Error(`Failed to update schedule: ${response.statusText}`);
      }
      return ScheduleSchema.parse(await response.json());
    },
    onSuccess: (_result, schedule) => {
      setFeedback({
        tone: "ok",
        text: `${schedule.name} ${schedule.enabled ? "paused" : "enabled"}.`,
      });
      queryClient.invalidateQueries({ queryKey });
    },
    onError: (mutationError) => {
      const message =
        mutationError instanceof Error
          ? mutationError.message
          : "Schedule update failed.";
      setFeedback({ tone: "error", text: message });
    },
  });

  const runNowMutation = useMutation({
    mutationFn: async (schedule: Schedule) => {
      const response = await fetch(endpoint(sources.runNow, schedule.id), {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(`Failed to run schedule: ${response.statusText}`);
      }
      return response.json();
    },
    onSuccess: (_result, schedule) => {
      setFeedback({
        tone: "ok",
        text: `${schedule.name} queued for an immediate run.`,
      });
      queryClient.invalidateQueries({ queryKey });
    },
    onError: (mutationError) => {
      const message =
        mutationError instanceof Error
          ? mutationError.message
          : "Run-now request failed.";
      setFeedback({ tone: "error", text: message });
    },
  });

  const schedules = data?.items || [];
  const filteredSchedules = filterSchedules(schedules, filter);
  const enabledCount = schedules.filter((item) => item.enabled).length;
  const pausedCount = schedules.length - enabledCount;
  const attentionCount = schedules.filter(needsAttention).length;

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h2 className="page-title">Recurring Schedules</h2>
          <p className="page-meta">
            Review cadence, target, dispatch health, and manual controls for
            scheduled workflows.
          </p>
        </div>
        <div className="toolbar-controls">
          <a href="/workflows/new?scheduleMode=recurring" className="button">
            Create Schedule
          </a>
        </div>
      </div>

      <div className="schedule-summary-grid" aria-label="Schedule summary">
        <button
          type="button"
          className={`schedule-summary-tile${filter === "all" ? " active" : ""}`}
          onClick={() => setFilter("all")}
        >
          <span className="schedule-summary-value">{schedules.length}</span>
          <span className="schedule-summary-label">Total</span>
        </button>
        <button
          type="button"
          className={`schedule-summary-tile${
            filter === "enabled" ? " active" : ""
          }`}
          onClick={() => setFilter("enabled")}
        >
          <span className="schedule-summary-value">{enabledCount}</span>
          <span className="schedule-summary-label">Enabled</span>
        </button>
        <button
          type="button"
          className={`schedule-summary-tile${
            filter === "paused" ? " active" : ""
          }`}
          onClick={() => setFilter("paused")}
        >
          <span className="schedule-summary-value">{pausedCount}</span>
          <span className="schedule-summary-label">Paused</span>
        </button>
        <button
          type="button"
          className={`schedule-summary-tile${
            filter === "needs_attention" ? " active" : ""
          }`}
          onClick={() => setFilter("needs_attention")}
        >
          <span className="schedule-summary-value">{attentionCount}</span>
          <span className="schedule-summary-label">Needs Attention</span>
        </button>
      </div>

      {feedback ? <div className={`notice ${feedback.tone}`}>{feedback.text}</div> : null}

      {isLoading ? (
        <p className="loading">Loading recurring schedules...</p>
      ) : isError ? (
        <div className="notice error">{(error as Error).message}</div>
      ) : (
        <DataTable
          ariaLabel="Recurring schedules"
          data={filteredSchedules}
          columns={[
            {
              key: "name",
              header: "Schedule",
              render: (item) => (
                <div className="schedule-name-cell">
                  <strong>{item.name}</strong>
                  {item.description ? (
                    <span>{item.description}</span>
                  ) : null}
                </div>
              ),
            },
            {
              key: "enabled",
              header: "State",
              render: (item) => (
                <span
                  className={`status ${
                    item.enabled ? "status-completed" : "status-neutral"
                  }`}
                >
                  {item.enabled ? "Enabled" : "Paused"}
                </span>
              ),
            },
            { key: "cron", header: "Cron", render: (item) => item.cron || "-" },
            {
              key: "timezone",
              header: "Timezone",
              render: (item) => item.timezone || "UTC",
            },
            { key: "target", header: "Target", render: targetLabel },
            {
              key: "lastDispatchStatus",
              header: "Last Dispatch",
              render: (item) => (
                <div className="schedule-dispatch-cell">
                  <span>{formatStatus(item.lastDispatchStatus)}</span>
                  {item.lastDispatchError ? (
                    <span className="schedule-dispatch-error">
                      {item.lastDispatchError}
                    </span>
                  ) : null}
                </div>
              ),
            },
            {
              key: "nextRunAt",
              header: "Next Run",
              render: (item) => formatWhen(item.nextRunAt),
            },
            {
              key: "actions",
              header: "Actions",
              render: (item) => (
                <div className="schedule-actions">
                  <button
                    type="button"
                    className="button small-button"
                    disabled={
                      runNowMutation.isPending || toggleMutation.isPending
                    }
                    onClick={() => runNowMutation.mutate(item)}
                  >
                    Run Now
                  </button>
                  <button
                    type="button"
                    className="button secondary small-button"
                    disabled={
                      runNowMutation.isPending || toggleMutation.isPending
                    }
                    onClick={() => toggleMutation.mutate(item)}
                  >
                    {item.enabled ? "Pause" : "Enable"}
                  </button>
                </div>
              ),
            },
          ]}
          emptyMessage={
            filter === "all"
              ? "No recurring schedules found."
              : "No schedules match this filter."
          }
          getRowKey={(item) => item.id}
        />
      )}
    </div>
  );
}

export default SchedulesPage;
