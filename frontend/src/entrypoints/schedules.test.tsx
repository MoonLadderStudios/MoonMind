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
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/tenant/api/recurring-workflows?scope=personal");
  });

  it("loads schedule detail and runs from configured definitionId templates", async () => {
    fetchSpy.mockImplementation((url) => {
      if (String(url) === "/console/schedules/schedule-1") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id: "schedule-1",
            name: "Daily repository sweep",
            description: "Runs every morning",
            enabled: true,
            scheduleType: "cron",
            cron: "0 9 * * *",
            timezone: "UTC",
            temporalScheduleId: "mm-schedule:schedule-1",
            lastDispatchStatus: "enqueued",
            lastDispatchError: null,
            nextRunAt: "2026-05-31T12:00:00Z",
            lastScheduledFor: "2026-05-30T12:00:00Z",
            scopeType: "personal",
            scopeRef: "owner",
            target: {
              kind: "queue_task",
              job: {
                payload: {
                  repository: "MoonLadderStudios/MoonMind",
                  runtime: "codex",
                  model: "gpt-5",
                },
              },
            },
            policy: {
              overlap: { mode: "skip" },
              catchup: { mode: "latest" },
            },
            version: 3,
            createdAt: "2026-05-01T12:00:00Z",
            updatedAt: "2026-05-30T12:00:00Z",
          }),
        } as Response);
      }
      if (String(url) === "/console/schedules/schedule-1/runs?limit=200") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [
              {
                id: "run-1",
                definitionId: "schedule-1",
                scheduledFor: "2026-05-30T12:00:00Z",
                trigger: "schedule",
                outcome: "dispatched",
                dispatchAttempts: 1,
                dispatchAfter: "2026-05-30T12:01:00Z",
                temporalWorkflowId: "workflow-1",
                temporalRunId: "temporal-run-1",
                message: "Queued",
              },
            ],
          }),
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, statusText: "Not Found", json: async () => ({}) } as Response);
    });

    renderWithClient(
      <SchedulesPage
        payload={{
          page: "schedules",
          apiBase: "/api",
          initialData: {
            initialPath: "/schedules/schedule-1",
            dashboardConfig: {
              sources: {
                schedules: {
                  detail: "/console/schedules/{definitionId}",
                  update: "/console/schedules/{definitionId}",
                  runNow: "/console/schedules/{definitionId}/run",
                  runs: "/console/schedules/{definitionId}/runs?limit=200",
                },
              },
            },
          },
        }}
      />,
    );

    expect(await screen.findByRole("heading", { name: "Daily repository sweep" })).not.toBeNull();
    expect(screen.getByText("mm-schedule:schedule-1")).not.toBeNull();
    expect(screen.getByText("MoonLadderStudios/MoonMind")).not.toBeNull();
    expect(screen.getByText("codex")).not.toBeNull();
    expect(screen.getByText("gpt-5")).not.toBeNull();
    expect((await screen.findByRole("link", { name: "workflow-1" })).getAttribute("href")).toBe(
      "/workflows/workflow-1?source=temporal",
    );
    expect(fetchSpy.mock.calls.map(([url]) => String(url))).toEqual([
      "/console/schedules/schedule-1",
      "/console/schedules/schedule-1/runs?limit=200",
    ]);
  });

  it("keeps controls available when run history fails", async () => {
    fetchSpy.mockImplementation((url) => {
      if (String(url) === "/console/schedules/schedule-2") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id: "schedule-2",
            name: "Paused schedule",
            description: null,
            enabled: false,
            scheduleType: "cron",
            cron: "15 * * * *",
            timezone: "UTC",
            lastDispatchStatus: "dispatch_error",
            lastDispatchError: "Adapter rejected schedule",
            nextRunAt: null,
            lastScheduledFor: null,
            scopeType: "personal",
            target: {},
            policy: {},
          }),
        } as Response);
      }
      if (String(url) === "/console/schedules/schedule-2/runs") {
        return Promise.resolve({ ok: false, status: 500, statusText: "Server Error", json: async () => ({}) } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, statusText: "Not Found", json: async () => ({}) } as Response);
    });

    renderWithClient(
      <SchedulesPage
        payload={{
          page: "schedules",
          apiBase: "/api",
          initialData: {
            initialPath: "/schedules/schedule-2",
            sources: {
              schedules: {
                detail: "/console/schedules/{definitionId}",
                update: "/console/schedules/{definitionId}",
                runNow: "/console/schedules/{definitionId}/run",
                runs: "/console/schedules/{definitionId}/runs",
              },
            },
          },
        }}
      />,
    );

    expect(await screen.findByRole("heading", { name: "Paused schedule" })).not.toBeNull();
    expect(screen.getAllByText("Paused").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Dispatch needs attention: Adapter rejected schedule")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Edit schedule" })).not.toBeNull();
    expect(screen.getByRole("button", { name: "Run now" })).not.toBeNull();
    expect(screen.getByRole("button", { name: "Resume" })).not.toBeNull();
    const runsPanel = screen.getByLabelText("Run history");
    await waitFor(() => expect(within(runsPanel).getByRole("alert").textContent).toContain("Server Error"));
    expect(fetchSpy.mock.calls.map(([url]) => String(url))).toEqual([
      "/console/schedules/schedule-2",
      "/console/schedules/schedule-2/runs",
    ]);
  });

  it("shows a not-found state with a schedules link when detail loading returns 404", async () => {
    fetchSpy.mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: async () => ({}),
    } as Response);
    renderWithClient(
      <SchedulesPage
        payload={{
          page: "schedules",
          apiBase: "/api",
          initialData: { initialPath: "/schedules/missing-schedule" },
        }}
      />,
    );

    expect(await screen.findByRole("heading", { name: "Schedule not found" })).not.toBeNull();
    expect(screen.getByRole("link", { name: "Back to schedules" }).getAttribute("href")).toBe("/schedules");
  });

  it("submits editable schedule configuration through the update template", async () => {
    fetchSpy.mockImplementation((url, init) => {
      if (String(url) === "/api/recurring-workflows/schedule-1" && String(init?.method || "GET").toUpperCase() === "PATCH") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id: "schedule-1",
            name: "Daily repository sweep updated",
            description: "",
            enabled: true,
            scheduleType: "cron",
            cron: "5 9 * * *",
            timezone: "UTC",
            target: {},
            policy: {},
            scopeType: "personal",
          }),
        } as Response);
      }
      if (String(url) === "/api/recurring-workflows/schedule-1") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id: "schedule-1",
            name: "Daily repository sweep",
            description: "Runs every morning",
            enabled: true,
            scheduleType: "cron",
            cron: "0 9 * * *",
            timezone: "UTC",
            target: {},
            policy: {},
            scopeType: "personal",
          }),
        } as Response);
      }
      if (String(url) === "/api/recurring-workflows/schedule-1/runs?limit=200") {
        return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, statusText: "Not Found", json: async () => ({}) } as Response);
    });

    renderWithClient(
      <SchedulesPage
        payload={{
          page: "schedules",
          apiBase: "/api",
          initialData: { initialPath: "/schedules/schedule-1" },
        }}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Edit schedule" }));
    fireEvent.change(screen.getByLabelText("Cron"), { target: { value: "5 9 * * *" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      const patchCall = fetchSpy.mock.calls.find(([, init]) => String(init?.method || "").toUpperCase() === "PATCH");
      expect(patchCall).toBeTruthy();
      expect(patchCall?.[0]).toBe("/api/recurring-workflows/schedule-1");
      expect(JSON.parse(String(patchCall?.[1]?.body))).toMatchObject({
        cron: "5 9 * * *",
        description: "",
      });
    });
  });
});
