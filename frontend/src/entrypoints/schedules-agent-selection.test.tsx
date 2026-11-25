import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import type { BootPayload } from "../boot/parseBootPayload";
import { renderWithClient } from "../utils/test-utils";
import { SchedulesPage } from "./schedules";

vi.mock("../lib/navigation", () => ({ navigateTo: vi.fn() }));

const payload: BootPayload = {
  page: "schedules",
  apiBase: "/api",
  initialData: { dashboardConfig: { initialPath: "/schedules/schedule-alpha" } },
};

// An Omnigent schedule as Create saves it: the profile's default tier was
// resolved and compiled, and no model selection was authored.
const admittedTarget = {
  workflowType: "MoonMind.UserWorkflow",
  initialParameters: {
    targetRuntime: "omnigent",
    profileId: "claude-work",
    model: "claude-opus-5-5",
    modelSource: "profile_default_tier",
    modelTierResolution: { effortSource: "profile_default_tier" },
    effort: "high",
    omnigentExecutionPlan: { planRef: "omnigent-execution-plan:sha256:abc" },
    workflow: {
      instructions: "Review open pull requests.",
      runtime: { mode: "omnigent", profileId: "claude-work", resolvedModel: "claude-opus-5-5" },
    },
  },
};

const schedule = {
  id: "schedule-alpha",
  name: "Nightly review",
  description: null,
  enabled: true,
  scheduleType: "cron",
  cron: "0 2 * * *",
  timezone: "UTC",
  target: admittedTarget,
  policy: { overlap: { mode: "skip" }, catchup: { mode: "last" } },
  version: 4,
};

const profiles = [
  {
    profile_id: "claude-work",
    runtime_id: "claude_code",
    account_label: "Anthropic · Work",
    model_tiers: [
      { model: "claude-opus-5-5", effort: "high" },
      { model: "claude-sonnet-5-5", effort: "medium" },
    ],
    default_model_tier: 1,
  },
  {
    profile_id: "codex-work",
    runtime_id: "codex_cli",
    account_label: "OpenAI · Work",
    model_tiers: [{ model: "gpt-5", effort: "medium" }],
    default_model_tier: 1,
  },
];

function json(body: unknown): Response {
  return { ok: true, json: async () => body } as Response;
}

function mockFetch(fetchSpy: MockInstance, target: unknown = admittedTarget) {
  fetchSpy.mockImplementation(async (input, init) => {
    const url = String(input);
    const method = String(init?.method || "GET").toUpperCase();
    if (url === "/api/v1/provider-profiles") return json(profiles);
    if (url.endsWith("/model-tiers:preview")) return json({ items: [] });
    if (url.endsWith("/capabilities")) {
      return json({ model: { options: [], allow_custom: true, runtime_default: null }, effort: { options: [] }, diagnostics: [] });
    }
    if (url.startsWith("/api/recurring-workflows/schedule-alpha/runs")) return json({ items: [] });
    if (url === "/api/recurring-workflows/schedule-alpha") {
      return json(method === "PATCH" ? { ...schedule, target, version: 5 } : { ...schedule, target });
    }
    if (url.startsWith("/api/recurring-workflows")) return json({ items: [{ ...schedule, target }] });
    throw new Error(`Unexpected fetch ${method} ${url}`);
  });
}

function patchBodies(fetchSpy: MockInstance): Record<string, unknown>[] {
  return fetchSpy.mock.calls
    .filter(([, init]) => init?.method === "PATCH")
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);
}

async function openEditor() {
  renderWithClient(<SchedulesPage payload={payload} />);
  await screen.findByRole("heading", { name: "Nightly review" });
  fireEvent.click(screen.getByRole("button", { name: "Edit schedule" }));
  // The saved profile's default tier previews once the inventory loads.
  await waitFor(() => expect((screen.getByLabelText("Schedule Tier") as HTMLSelectElement).value).toBe("1"));
}

async function save(fetchSpy: MockInstance): Promise<Record<string, unknown>> {
  fireEvent.click(screen.getByRole("button", { name: "Save schedule" }));
  await waitFor(() => expect(patchBodies(fetchSpy)).toHaveLength(1));
  return patchBodies(fetchSpy)[0]!;
}

describe("schedule edit agent selection", () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, "", "/schedules/schedule-alpha");
    fetchSpy = vi.spyOn(window, "fetch");
    mockFetch(fetchSpy);
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("shows the saved Harness, Provider Profile and model selection", async () => {
    await openEditor();

    expect((screen.getByLabelText("Schedule Harness") as HTMLSelectElement).value).toBe("claude_code");
    expect((screen.getByLabelText("Schedule Provider Profile") as HTMLSelectElement).value).toBe("claude-work");
    expect((screen.getByLabelText("Schedule Model") as HTMLInputElement).value).toBe("claude-opus-5-5");
    expect((screen.getByLabelText("Schedule Effort") as HTMLInputElement).value).toBe("high");
  });

  it("changes the model without editing the target JSON", async () => {
    await openEditor();

    fireEvent.change(screen.getByLabelText("Schedule Model"), { target: { value: "claude-sonnet-5-5" } });
    expect((screen.getByLabelText("Schedule Tier") as HTMLSelectElement).value).toBe("custom");

    // Editing one field authors the pair; the untouched effort is kept.
    expect(await save(fetchSpy)).toEqual({
      runtime: { model: "claude-sonnet-5-5", effort: "high" },
      version: 4,
    });
  });

  it("follows a profile tier without pinning its current model", async () => {
    await openEditor();

    fireEvent.change(screen.getByLabelText("Schedule Tier"), { target: { value: "2" } });
    expect((screen.getByLabelText("Schedule Model") as HTMLInputElement).value).toBe("claude-sonnet-5-5");

    expect(await save(fetchSpy)).toEqual({ runtime: { modelTier: 2 }, version: 4 });
  });

  it("moves to another Harness only once one of its profiles is chosen", async () => {
    await openEditor();

    fireEvent.change(screen.getByLabelText("Schedule Harness"), { target: { value: "codex_cli" } });
    const profile = screen.getByLabelText("Schedule Provider Profile") as HTMLSelectElement;
    expect(profile.value).toBe("");
    expect(Array.from(profile.options).map((option) => option.textContent)).toEqual([
      "Choose a Codex CLI Provider Profile",
      "OpenAI · Work",
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText("Choose a Provider Profile for the selected Harness.")).toBeTruthy();
    expect(patchBodies(fetchSpy)).toHaveLength(0);

    fireEvent.change(profile, { target: { value: "codex-work" } });
    await waitFor(() => expect((screen.getByLabelText("Schedule Model") as HTMLInputElement).value).toBe("gpt-5"));

    // The untouched selection stays omitted so it follows the new profile's default.
    expect(await save(fetchSpy)).toEqual({ runtime: { profileId: "codex-work" }, version: 4 });
  });

  it("returns the saved profile when its Harness is chosen again", async () => {
    await openEditor();

    fireEvent.change(screen.getByLabelText("Schedule Harness"), { target: { value: "codex_cli" } });
    fireEvent.change(screen.getByLabelText("Schedule Harness"), { target: { value: "claude_code" } });

    expect((screen.getByLabelText("Schedule Provider Profile") as HTMLSelectElement).value).toBe("claude-work");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Renamed" } });
    expect(await save(fetchSpy)).toEqual({ name: "Renamed", version: 4 });
  });

  it("offers no agent selection for tool-only work", async () => {
    mockFetch(fetchSpy, {
      workflowType: "MoonMind.UserWorkflow",
      initialParameters: { workflow: { instructions: "Scan", runtime: { mode: "omnigent", authored: false } } },
    });
    renderWithClient(<SchedulesPage payload={payload} />);
    await screen.findByRole("heading", { name: "Nightly review" });
    fireEvent.click(screen.getByRole("button", { name: "Edit schedule" }));

    expect(screen.getByLabelText("Target Workflow Parameters")).toBeTruthy();
    expect(screen.queryByLabelText("Schedule Harness")).toBeNull();
  });
});
