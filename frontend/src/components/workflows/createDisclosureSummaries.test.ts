import { describe, expect, it } from "vitest";

import {
  summarizeDependencies,
  summarizeOverrides,
  summarizeReport,
  summarizeSchedule,
} from "./createDisclosureSummaries";

describe("summarizeSchedule", () => {
  it("describes an immediate run", () => {
    expect(summarizeSchedule({ mode: "immediate" })).toBe("Runs now");
  });

  it("describes a one-time run with or without a chosen time", () => {
    expect(summarizeSchedule({ mode: "once", scheduledFor: "2026-10-20T09:30" })).toBe(
      "Runs once at 2026-10-20 09:30",
    );
    expect(summarizeSchedule({ mode: "once", scheduledFor: "" })).toBe(
      "Runs once (choose a time)",
    );
  });

  it("describes a deferred run in minutes", () => {
    expect(summarizeSchedule({ mode: "deferred_minutes", deferredMinutes: "1" })).toBe(
      "Runs in 1 minute",
    );
    expect(summarizeSchedule({ mode: "deferred_minutes", deferredMinutes: " 15 " })).toBe(
      "Runs in 15 minutes",
    );
    expect(summarizeSchedule({ mode: "deferred_minutes", deferredMinutes: "" })).toBe(
      "Runs later (choose minutes)",
    );
  });

  it("describes a recurring schedule with its timezone", () => {
    expect(
      summarizeSchedule({ mode: "recurring", cron: "0 9 * * 1", timezone: "Europe/Paris" }),
    ).toBe("Recurring: 0 9 * * 1 (Europe/Paris)");
    expect(summarizeSchedule({ mode: "recurring", cron: "*/30 * * * *", timezone: "" })).toBe(
      "Recurring: */30 * * * * (UTC)",
    );
    expect(summarizeSchedule({ mode: "recurring", cron: " ", timezone: "UTC" })).toBe(
      "Recurring (choose a cron schedule)",
    );
  });
});

describe("summarizeDependencies", () => {
  it("counts prerequisites", () => {
    expect(summarizeDependencies([])).toBe("No prerequisites");
    expect(summarizeDependencies(["Nightly build"])).toBe("Waits for Nightly build");
    expect(summarizeDependencies(["A", "B"])).toBe("Waits for 2 workflows");
  });
});

describe("summarizeReport", () => {
  it("states whether a report artifact is produced", () => {
    expect(summarizeReport(false)).toBe("Report: off");
    expect(summarizeReport(true)).toBe("Report: on");
  });
});

describe("summarizeOverrides", () => {
  it("is empty when nothing differs from defaults", () => {
    expect(
      summarizeOverrides({ priority: null, maxAttempts: null, hostPolicy: null, stepOverrideCount: 0 }),
    ).toBe("");
  });

  it("names each override with its value", () => {
    expect(
      summarizeOverrides({ priority: 7, maxAttempts: 5, hostPolicy: "Locked down", stepOverrideCount: 2 }),
    ).toBe("Overrides: priority 7, max attempts 5, host policy Locked down, 2 step overrides");
    expect(
      summarizeOverrides({ priority: -2, maxAttempts: null, hostPolicy: null, stepOverrideCount: 1 }),
    ).toBe("Overrides: priority -2, 1 step override");
  });
});
