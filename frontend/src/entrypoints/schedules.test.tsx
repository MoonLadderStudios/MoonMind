import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { BootPayload } from "../boot/parseBootPayload";
import { renderWithClient } from "../utils/test-utils";
import { SchedulesPage } from "./schedules";

const mockPayload: BootPayload = {
  page: "schedules",
  apiBase: "/api",
  initialData: {},
};

describe("SchedulesPage", () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [
          {
            id: "schedule-1",
            name: "Daily repository sweep",
            enabled: true,
            cron: "0 9 * * *",
            timezone: "UTC",
            lastDispatchStatus: "enqueued",
            lastDispatchError: null,
            nextRunAt: "2026-05-31T12:00:00Z",
            lastScheduledFor: "2026-05-30T12:00:00Z",
            scopeType: "personal",
            target: {
              kind: "queue_task",
              job: {
                payload: {
                  repository: "MoonLadderStudios/MoonMind",
                },
              },
            },
            policy: {
              overlap: { mode: "skip" },
              catchup: { mode: "latest" },
            },
          },
          {
            id: "schedule-2",
            name: "Sparse schedule",
            enabled: false,
            cron: "15 * * * *",
            timezone: "UTC",
            lastDispatchStatus: null,
            lastDispatchError: null,
            nextRunAt: null,
            lastScheduledFor: null,
            target: {},
            policy: {},
          },
          {
            id: "schedule-3",
            name: "Failing schedule",
            enabled: true,
            cron: "30 4 * * 1",
            timezone: "America/New_York",
            lastDispatchStatus: "dispatch_error",
            lastDispatchError: "Adapter rejected schedule",
            nextRunAt: null,
            lastScheduledFor: null,
            target: {},
            policy: {},
          },
        ],
      }),
    } as Response);
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    window.history.pushState({}, "Schedules", "/schedules");
  });

  it("renders streamlined schedule status, target, cadence, and policy columns", async () => {
    renderWithClient(<SchedulesPage payload={mockPayload} />);

    expect(await screen.findByText("Daily repository sweep")).not.toBeNull();
    expect(await screen.findByText("Sparse schedule")).not.toBeNull();
    expect(screen.getByText("Failing schedule")).not.toBeNull();
    expect(screen.getAllByText("Active").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Paused")).not.toBeNull();
    expect(screen.getByText("Needs attention")).not.toBeNull();
    expect(screen.getByText("MoonLadderStudios/MoonMind")).not.toBeNull();
    expect(screen.getByText("0 9 * * *")).not.toBeNull();
    expect(screen.getByText("Skip / Latest")).not.toBeNull();
    expect(screen.getByText("Adapter rejected schedule")).not.toBeNull();
    expect(screen.getByText("Total").nextElementSibling?.textContent).toBe("3");
  });

  it("routes creation to the workflow create page without local mutations", async () => {
    renderWithClient(<SchedulesPage payload={mockPayload} />);

    const createLink = await screen.findByRole("link", {
      name: "Create from workflow page",
    });
    expect(createLink.getAttribute("href")).toBe(
      "/workflows/new?scheduleMode=recurring",
    );
    expect(screen.queryByRole("button", { name: "Create Schedule" })).toBeNull();
    expect(screen.queryByText("Edit")).toBeNull();
    expect(screen.queryByText("Schedule (Cron)")).toBeNull();

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const mutationCalls = fetchSpy.mock.calls.filter(([, init]) => {
      const method = String(init?.method || "GET").toUpperCase();
      return method === "POST" || method === "PUT";
    });
    expect(mutationCalls).toEqual([]);
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url) === "/api/recurring-tasks"),
    ).toBe(false);
  });

  it("shows an empty state without adding local create controls", async () => {
    fetchSpy.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [] }),
    } as Response);

    renderWithClient(<SchedulesPage payload={mockPayload} />);

    expect(await screen.findByText("No recurring schedules yet. Create one from the workflow page.")).not.toBeNull();
    expect(screen.getByText("Total").nextElementSibling?.textContent).toBe("0");
    expect(screen.queryByRole("button", { name: "Create Schedule" })).toBeNull();
  });

  it("honors dashboard schedule list sources from the boot payload", async () => {
    fetchSpy.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [] }),
    } as Response);

    renderWithClient(
      <SchedulesPage
        payload={{
          page: "schedules",
          apiBase: "/tenant/api",
          initialData: {
            dashboardConfig: {
              sources: {
                schedules: {
                  list: "/console/schedules?scope=personal",
                },
              },
            },
          },
        }}
      />,
    );

    expect(await screen.findByText("No recurring schedules yet. Create one from the workflow page.")).not.toBeNull();
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/console/schedules?scope=personal");
  });

  it("uses apiBase for the default schedule list endpoint", async () => {
    fetchSpy.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [] }),
    } as Response);

    renderWithClient(
      <SchedulesPage
        payload={{
          page: "schedules",
          apiBase: "/tenant/api",
          initialData: {},
        }}
      />,
    );

    expect(await screen.findByText("No recurring schedules yet. Create one from the workflow page.")).not.toBeNull();
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/tenant/api/recurring-tasks?scope=personal");
  });

  it("MM-894 renders schedule detail with workflow-derived shell and schedule labels", async () => {
    window.history.pushState({}, "Schedule detail", "/schedules/schedule-1");
    fetchSpy.mockImplementation(async (url, init) => {
      if (String(url) === "/api/recurring-workflows/schedule-1/runs?limit=200") {
        return {
          ok: true,
          json: async () => ({
            items: [
              {
                id: "run-1",
                definitionId: "schedule-1",
                scheduledFor: "2026-06-01T09:00:00Z",
                trigger: "schedule",
                outcome: "dispatched",
                dispatchAttempts: 1,
                temporalWorkflowId: "workflow-123",
                temporalRunId: "run-123",
                createdAt: "2026-06-01T09:00:02Z",
                updatedAt: "2026-06-01T09:00:03Z",
              },
            ],
          }),
        } as Response;
      }
      if (String(url) === "/api/recurring-workflows/schedule-1/run" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({ id: "manual-run" }),
        } as Response;
      }
      if (String(url) === "/api/recurring-workflows/schedule-1") {
        return {
          ok: true,
          json: async () => ({
            id: "schedule-1",
            name: "Daily repository sweep",
            description: "Sweep the repository every weekday.",
            enabled: true,
            cron: "0 9 * * 1-5",
            timezone: "UTC",
            nextRunAt: "2026-06-02T09:00:00Z",
            lastScheduledFor: "2026-06-01T09:00:00Z",
            lastDispatchStatus: "dispatched",
            lastDispatchError: null,
            temporalScheduleId: "temporal-schedule-1",
            scopeType: "personal",
            scopeRef: "user-1",
            target: {
              kind: "workflow",
              runtime: "codex",
              model: "gpt-5",
              publishMode: "pull_request",
              job: {
                payload: {
                  repository: "MoonLadderStudios/MoonMind",
                  runtime: "codex",
                  model: "gpt-5",
                  publishMode: "pull_request",
                },
              },
            },
            policy: {
              overlap: { mode: "skip" },
              catchup: { mode: "latest" },
            },
            updatedAt: "2026-06-01T10:00:00Z",
          }),
        } as Response;
      }
      throw new Error(`Unexpected fetch ${String(url)}`);
    });

    renderWithClient(<SchedulesPage payload={mockPayload} />);

    expect(await screen.findByRole("heading", { name: "Schedule Detail" })).not.toBeNull();
    expect(screen.getByText("Schedules / schedule-1")).not.toBeNull();
    expect(await screen.findByRole("heading", { name: "Daily repository sweep" })).not.toBeNull();
    expect(document.querySelector(".workflow-detail-page.schedule-detail-page")).toBeTruthy();
    expect(document.querySelector(".toolbar-identity-row .status.status-running")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Overview" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Runs" })).not.toBeNull();
    expect(screen.getByRole("link", { name: "Configuration" })).not.toBeNull();
    expect(screen.queryByRole("link", { name: "Steps" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Artifacts" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Activity" })).toBeNull();

    expect(screen.getAllByText("Schedule name").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Schedule state").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Schedule definition ID").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Temporal Schedule ID").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Target facts").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Next run").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Last run").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Dispatch result").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Updated time").length).toBeGreaterThan(0);
    expect(screen.getAllByText("temporal-schedule-1").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/MoonLadderStudios\/MoonMind/).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("link", { name: "Runs" }));
    expect((await screen.findByRole("link", { name: "workflow-123" })).getAttribute("href")).toBe(
      "/workflows/workflow-123?source=temporal",
    );

    fireEvent.click(screen.getByRole("link", { name: "Configuration" }));
    const detailPanel = screen.getByLabelText("Schedule detail panel");
    expect(within(detailPanel).getByText("Cron")).not.toBeNull();
    expect(within(detailPanel).getByText("Timezone")).not.toBeNull();
    expect(within(detailPanel).getByText("Policy")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    await waitFor(() => {
      expect(fetchSpy.mock.calls.some(([url, init]) => (
        String(url) === "/api/recurring-workflows/schedule-1/run" && init?.method === "POST"
      ))).toBe(true);
    });
  });

  it("MM-894 renders Activity only when schedule activity data is available", async () => {
    window.history.pushState({}, "Schedule detail", "/schedules/schedule-activity");
    fetchSpy.mockImplementation(async (url) => {
      if (String(url).endsWith("/runs?limit=200")) {
        return { ok: true, json: async () => ({ items: [] }) } as Response;
      }
      return {
        ok: true,
        json: async () => ({
          id: "schedule-activity",
          name: "Schedule with activity",
          enabled: false,
          cron: "15 * * * *",
          timezone: "UTC",
          temporalScheduleId: "temporal-activity",
          lastDispatchStatus: null,
          lastDispatchError: null,
          nextRunAt: null,
          lastScheduledFor: null,
          target: {},
          policy: {},
          updatedAt: "2026-06-01T10:00:00Z",
          activity: [
            {
              id: "activity-1",
              title: "Temporal describe refreshed",
              message: "Schedule metadata was refreshed.",
              updatedAt: "2026-06-01T10:00:00Z",
            },
          ],
        }),
      } as Response;
    });

    renderWithClient(<SchedulesPage payload={mockPayload} />);

    const activityLink = await screen.findByRole("link", { name: "Activity" });
    fireEvent.click(activityLink);
    expect(await screen.findByText("Temporal describe refreshed")).not.toBeNull();
    expect(screen.getByText("Schedule metadata was refreshed.")).not.toBeNull();
  });
});
