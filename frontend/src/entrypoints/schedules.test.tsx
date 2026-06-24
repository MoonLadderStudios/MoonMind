import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import type { BootPayload } from "../boot/parseBootPayload";
import { renderWithClient } from "../utils/test-utils";
import { SchedulesPage } from "./schedules";

const mockPayload: BootPayload = {
  page: "schedules",
  apiBase: "/api",
  initialData: {},
};

const detailSchedule = {
  id: "schedule-alpha",
  name: "Nightly detail sweep",
  description: "Keeps the repository current.",
  enabled: true,
  scheduleType: "cron",
  cron: "0 2 * * *",
  timezone: "UTC",
  lastDispatchStatus: "enqueued",
  lastDispatchError: null,
  nextRunAt: "2026-06-25T02:00:00Z",
  lastScheduledFor: "2026-06-24T02:00:00Z",
  scopeType: "personal",
  scopeRef: "user-1",
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
  version: 1,
  createdAt: "2026-06-20T00:00:00Z",
  updatedAt: "2026-06-24T00:00:00Z",
};

const detailRuns = {
  items: [
    {
      id: "run-1",
      definitionId: "schedule-alpha",
      scheduledFor: "2026-06-24T02:00:00Z",
      trigger: "schedule",
      outcome: "enqueued",
      dispatchAttempts: 1,
      dispatchAfter: null,
      temporalWorkflowId: "workflow-from-schedule",
      temporalRunId: "temporal-run-1",
      message: "Started",
      createdAt: "2026-06-24T02:00:01Z",
      updatedAt: "2026-06-24T02:00:02Z",
    },
  ],
};

const detailPayload: BootPayload = {
  page: "schedules",
  apiBase: "/api",
  initialData: {
    dashboardConfig: {
      initialPath: "/schedules/schedule-alpha",
      sources: {
        schedules: {
          list: "/console/schedules?scope=personal",
          detail: "/console/schedules/{definitionId}",
          update: "/console/schedules/{definitionId}",
          runNow: "/console/schedules/{definitionId}/run",
          runs: "/console/schedules/{definitionId}/runs?limit=200",
        },
      },
    },
  },
};

function mockScheduleDetailFetch(fetchSpy: MockInstance, overrides: Partial<typeof detailSchedule> = {}) {
  const schedule = { ...detailSchedule, ...overrides };
  fetchSpy.mockImplementation(async (input, init) => {
    const url = String(input);
    const method = String(init?.method || "GET").toUpperCase();
    if (url === "/console/schedules/schedule-alpha" && method === "PATCH") {
      return {
        ok: true,
        json: async () => ({ ...schedule, ...(JSON.parse(String(init?.body || "{}")) as object) }),
      } as Response;
    }
    if (url === "/console/schedules/schedule-alpha/run" && method === "POST") {
      return {
        ok: true,
        json: async () => ({ ...detailRuns.items[0], id: "run-manual", trigger: "manual" }),
      } as Response;
    }
    if (url === "/console/schedules/schedule-alpha/runs?limit=200") {
      return {
        ok: true,
        json: async () => detailRuns,
      } as Response;
    }
    if (url === "/console/schedules/schedule-alpha") {
      return {
        ok: true,
        json: async () => schedule,
      } as Response;
    }
    throw new Error(`Unexpected fetch ${method} ${url}`);
  });
}

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
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/tenant/api/recurring-tasks?scope=personal");
  });

  it("loads the recurring schedule detail route by the routed definition id", async () => {
    mockScheduleDetailFetch(fetchSpy);

    renderWithClient(<SchedulesPage payload={detailPayload} />);

    expect(await screen.findByRole("heading", { name: "Nightly detail sweep" })).not.toBeNull();
    expect(screen.getAllByText("schedule-alpha").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("MoonLadderStudios/MoonMind")).not.toBeNull();
    expect(screen.getByText("Started")).not.toBeNull();
    expect(screen.getByRole("link", { name: "workflow-from-schedule" }).getAttribute("href")).toBe(
      "/workflows/workflow-from-schedule?source=temporal",
    );
    expect(fetchSpy.mock.calls.some(([url]) => String(url) === "/console/schedules?scope=personal")).toBe(false);
    expect(fetchSpy.mock.calls.some(([url]) => String(url) === "/console/schedules/schedule-alpha")).toBe(true);
    expect(fetchSpy.mock.calls.some(([url]) => String(url) === "/console/schedules/schedule-alpha/runs?limit=200")).toBe(true);
    expect(screen.getByRole("button", { name: "Edit schedule" })).not.toBeNull();
    expect(screen.getByRole("button", { name: "Run now" })).not.toBeNull();
    expect(screen.getByRole("button", { name: "Pause schedule" })).not.toBeNull();
    expect(screen.queryByRole("button", { name: /delete schedule/i })).toBeNull();
  });

  it("ignores malformed schedule route ids instead of throwing during render", async () => {
    fetchSpy.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [] }),
    } as Response);

    renderWithClient(
      <SchedulesPage
        payload={{
          ...detailPayload,
          initialData: {
            dashboardConfig: {
              initialPath: "/schedules/%",
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

  it("keeps update requests keyed by the route definition id", async () => {
    mockScheduleDetailFetch(fetchSpy);

    renderWithClient(<SchedulesPage payload={detailPayload} />);

    expect(await screen.findByRole("heading", { name: "Nightly detail sweep" })).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Edit schedule" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Nightly detail sweep updated" } });
    fireEvent.click(screen.getByRole("button", { name: "Save schedule" }));

    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(([url, init]) => (
          String(url) === "/console/schedules/schedule-alpha"
          && String(init?.method || "GET").toUpperCase() === "PATCH"
          && String(init?.body || "").includes("Nightly detail sweep updated")
        )),
      ).toBe(true);
    });
    expect(screen.getAllByText("schedule-alpha").length).toBeGreaterThanOrEqual(1);
  });

  it("sends an empty description when the edit form clears an existing description", async () => {
    mockScheduleDetailFetch(fetchSpy);

    renderWithClient(<SchedulesPage payload={detailPayload} />);

    expect(await screen.findByRole("heading", { name: "Nightly detail sweep" })).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Edit schedule" }));
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save schedule" }));

    await waitFor(() => {
      const updateCall = fetchSpy.mock.calls.find(([url, init]) => (
        String(url) === "/console/schedules/schedule-alpha"
        && String(init?.method || "GET").toUpperCase() === "PATCH"
      ));
      expect(updateCall).toBeDefined();
      expect(JSON.parse(String(updateCall?.[1]?.body || "{}")).description).toBe("");
    });
  });

  it("reports update and run failures separately", async () => {
    fetchSpy.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = String(init?.method || "GET").toUpperCase();
      if (url === "/console/schedules/schedule-alpha" && method === "PATCH") {
        return { ok: false, statusText: "Update rejected", json: async () => ({}) } as Response;
      }
      if (url === "/console/schedules/schedule-alpha/run" && method === "POST") {
        return { ok: false, statusText: "Run rejected", json: async () => ({}) } as Response;
      }
      if (url === "/console/schedules/schedule-alpha/runs?limit=200") {
        return { ok: true, json: async () => detailRuns } as Response;
      }
      if (url === "/console/schedules/schedule-alpha") {
        return { ok: true, json: async () => detailSchedule } as Response;
      }
      throw new Error(`Unexpected fetch ${method} ${url}`);
    });

    renderWithClient(<SchedulesPage payload={detailPayload} />);

    expect(await screen.findByRole("heading", { name: "Nightly detail sweep" })).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Edit schedule" }));
    fireEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    fireEvent.click(screen.getByRole("button", { name: "Run now" }));

    expect(await screen.findByText("Failed to update schedule: Update rejected")).not.toBeNull();
    expect(await screen.findByText("Failed to run schedule: Run rejected")).not.toBeNull();
  });

  it("runs schedules from the same routed definition id and leaves run links on workflow detail routes", async () => {
    mockScheduleDetailFetch(fetchSpy);

    renderWithClient(<SchedulesPage payload={detailPayload} />);

    expect(await screen.findByRole("heading", { name: "Nightly detail sweep" })).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Run now" }));

    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(([url, init]) => (
          String(url) === "/console/schedules/schedule-alpha/run"
          && String(init?.method || "GET").toUpperCase() === "POST"
        )),
      ).toBe(true);
    });
    expect(screen.getByRole("link", { name: "workflow-from-schedule" }).getAttribute("href")).toBe(
      "/workflows/workflow-from-schedule?source=temporal",
    );
    expect(screen.getAllByText("schedule-alpha").length).toBeGreaterThanOrEqual(1);
  });

  it("pauses schedules through the detail update contract", async () => {
    mockScheduleDetailFetch(fetchSpy);

    renderWithClient(<SchedulesPage payload={detailPayload} />);

    expect(await screen.findByRole("heading", { name: "Nightly detail sweep" })).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Pause schedule" }));

    await waitFor(() => {
      const pauseCall = fetchSpy.mock.calls.find(([url, init]) => (
        String(url) === "/console/schedules/schedule-alpha"
        && String(init?.method || "GET").toUpperCase() === "PATCH"
        && JSON.parse(String(init?.body || "{}")).enabled === false
      ));
      expect(pauseCall).toBeDefined();
    });
  });

  it("resumes paused schedules through the detail update contract", async () => {
    mockScheduleDetailFetch(fetchSpy, { enabled: false });

    renderWithClient(<SchedulesPage payload={detailPayload} />);

    expect(await screen.findByRole("heading", { name: "Nightly detail sweep" })).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Resume schedule" }));

    await waitFor(() => {
      const resumeCall = fetchSpy.mock.calls.find(([url, init]) => (
        String(url) === "/console/schedules/schedule-alpha"
        && String(init?.method || "GET").toUpperCase() === "PATCH"
        && JSON.parse(String(init?.body || "{}")).enabled === true
      ));
      expect(resumeCall).toBeDefined();
    });
  });
});
