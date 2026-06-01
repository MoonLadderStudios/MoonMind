import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import type { BootPayload } from "../boot/parseBootPayload";
import { renderWithClient } from "../utils/test-utils";
import { SchedulesPage } from "./schedules";

const mockPayload: BootPayload = {
  page: "schedules",
  apiBase: "/api",
  initialData: {
    sources: {
      schedules: {
        list: "/api/recurring-tasks?scope=personal",
        update: "/api/recurring-tasks/{id}",
        runNow: "/api/recurring-tasks/{id}/run",
      },
    },
  },
};

const scheduleOne = {
  id: "schedule-1",
  name: "Daily roadmap follow-up",
  description: "Check Milestone 3 follow-up work.",
  enabled: true,
  scheduleType: "cron",
  cron: "0 9 * * *",
  timezone: "UTC",
  lastDispatchStatus: "failed",
  lastDispatchError: "Worker unavailable",
  nextRunAt: "2026-06-01T09:00:00Z",
  target: { kind: "queue_task", workflowType: "MoonMind.Run" },
  policy: {},
  updatedAt: "2026-05-31T16:00:00Z",
};

const scheduleTwo = {
  id: "schedule-2",
  name: "Sparse schedule",
  enabled: false,
  scheduleType: "cron",
  cron: "*/30 * * * *",
  timezone: "America/New_York",
  lastDispatchStatus: null,
  nextRunAt: null,
  target: {},
  policy: {},
  updatedAt: "2026-05-31T16:00:00Z",
};

function response(body: unknown, ok = true): Response {
  return {
    ok,
    statusText: ok ? "OK" : "Failure",
    json: async () => body,
  } as Response;
}

describe("SchedulesPage", () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    fetchSpy = vi.spyOn(window, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      const method = String(init?.method || "GET").toUpperCase();
      if (url === "/api/recurring-tasks?scope=personal" && method === "GET") {
        return response({ items: [scheduleOne, scheduleTwo] });
      }
      if (url === "/api/recurring-tasks/schedule-1/run" && method === "POST") {
        return response({
          id: "run-1",
          definitionId: "schedule-1",
          scheduledFor: "2026-05-31T16:05:00Z",
          trigger: "manual",
          outcome: "enqueued",
          dispatchAttempts: 1,
          createdAt: "2026-05-31T16:05:00Z",
          updatedAt: "2026-05-31T16:05:00Z",
        }, true);
      }
      if (url === "/api/recurring-tasks/schedule-1" && method === "PATCH") {
        return response({ ...scheduleOne, enabled: false });
      }
      return response({}, false);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("renders operational schedule summary, rows, and nullable fields", async () => {
    renderWithClient(<SchedulesPage payload={mockPayload} />);

    expect(await screen.findByText("Daily roadmap follow-up")).not.toBeNull();
    expect(screen.getByText("Sparse schedule")).not.toBeNull();
    expect(screen.getByText("Total")).not.toBeNull();
    expect(screen.getAllByText("Enabled").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Paused").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Needs Attention")).not.toBeNull();
    expect(screen.getByText("Worker unavailable")).not.toBeNull();
    expect(
      screen.getByText(
        new Date(scheduleOne.nextRunAt).toLocaleString(undefined, {
          timeZone: scheduleOne.timezone,
        }),
      ),
    ).not.toBeNull();
    expect(screen.getAllByText("-").length).toBeGreaterThanOrEqual(1);
  });

  it("filters schedules that need attention", async () => {
    renderWithClient(<SchedulesPage payload={mockPayload} />);

    fireEvent.click(await screen.findByRole("button", { name: /Needs Attention/i }));

    expect(screen.getByText("Daily roadmap follow-up")).not.toBeNull();
    expect(screen.queryByText("Sparse schedule")).toBeNull();
  });

  it("routes creation to the workflow create page and exposes schedule actions", async () => {
    renderWithClient(<SchedulesPage payload={mockPayload} />);

    const createLink = await screen.findByRole("link", { name: "Create Schedule" });
    expect(createLink.getAttribute("href")).toBe("/workflows/new?scheduleMode=recurring");
    await screen.findByText("Daily roadmap follow-up");

    const runNowButton = screen.getAllByRole("button", { name: "Run Now" })[0];
    if (!runNowButton) throw new Error("Run Now button was not rendered.");
    fireEvent.click(runNowButton);
    await screen.findByText("Daily roadmap follow-up queued for an immediate run.");

    const pauseButton = screen.getAllByRole("button", { name: "Pause" })[0];
    if (!pauseButton) throw new Error("Pause button was not rendered.");
    fireEvent.click(pauseButton);
    await screen.findByText("Daily roadmap follow-up paused.");

    await waitFor(() =>
      expect(
        fetchSpy.mock.calls.some(
          ([url, init]) =>
            String(url) === "/api/recurring-tasks/schedule-1/run" &&
            String(init?.method).toUpperCase() === "POST",
        ),
      ).toBe(true),
    );
    expect(
      fetchSpy.mock.calls.some(
        ([url, init]) =>
          String(url) === "/api/recurring-tasks/schedule-1" &&
          String(init?.method).toUpperCase() === "PATCH",
      ),
    ).toBe(true);
  });

  it("honors dashboard schedule sources from the boot payload", async () => {
    fetchSpy.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = String(init?.method || "GET").toUpperCase();
      if (url === "/console/schedules?scope=personal" && method === "GET") {
        return response({ items: [scheduleOne] });
      }
      if (url === "/console/schedules/schedule-1/toggle" && method === "PATCH") {
        return response({ ...scheduleOne, name: "Server schedule", enabled: false });
      }
      return response({}, false);
    });

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
                  update: "/console/schedules/{id}/toggle",
                  runNow: "/console/schedules/{id}/run-now",
                },
              },
            },
          },
        }}
      />,
    );

    await screen.findByText("Daily roadmap follow-up");
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    await screen.findByText("Server schedule paused.");

    expect(
      fetchSpy.mock.calls.some(
        ([url, init]) =>
          String(url) === "/console/schedules/schedule-1/toggle" &&
          String(init?.method).toUpperCase() === "PATCH",
      ),
    ).toBe(true);
  });

  it("uses apiBase for default schedule endpoints", async () => {
    fetchSpy.mockImplementation(async (input, init) => {
      const url = String(input);
      const method = String(init?.method || "GET").toUpperCase();
      if (url === "/tenant/api/recurring-tasks?scope=personal" && method === "GET") {
        return response({ items: [scheduleOne] });
      }
      return response({}, false);
    });

    renderWithClient(
      <SchedulesPage
        payload={{
          page: "schedules",
          apiBase: "/tenant/api",
          initialData: {},
        }}
      />,
    );

    await screen.findByText("Daily roadmap follow-up");
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/tenant/api/recurring-tasks?scope=personal");
  });
});
