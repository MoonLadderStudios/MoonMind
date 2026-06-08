import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { screen, waitFor } from "@testing-library/react";

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
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/tenant/api/recurring-tasks?scope=personal");
  });
});
