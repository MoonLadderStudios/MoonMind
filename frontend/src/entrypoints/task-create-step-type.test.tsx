import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { BootPayload } from "../boot/parseBootPayload";
import { navigateTo } from "../lib/navigation";
import { renderWithClient } from "../utils/test-utils";
import { TaskCreatePage } from "./task-create";

vi.mock("../lib/navigation", () => ({
  navigateTo: vi.fn(),
}));

const mockPayload: BootPayload = {
  page: "task-create",
  apiBase: "/api",
  initialData: {
    dashboardConfig: {
      sources: {
        temporal: {
          create: "/api/executions",
          artifactCreate: "/api/artifacts",
        },
        github: {
          branches: "/api/github/branches?repository={repository}",
        },
      },
      system: {
        defaultRepository: "MoonLadderStudios/MoonMind",
        defaultTaskRuntime: "codex_cli",
        defaultTaskModel: "gpt-5.4",
        defaultTaskEffort: "medium",
        defaultPublishMode: "pr",
        defaultProposeTasks: false,
        defaultTaskModelByRuntime: {
          codex_cli: "gpt-5.4",
        },
        defaultTaskEffortByRuntime: {
          codex_cli: "medium",
        },
        supportedTaskRuntimes: ["codex_cli"],
        providerProfiles: {
          list: "/api/v1/provider-profiles",
        },
        taskTemplateCatalog: {
          enabled: true,
          templateSaveEnabled: true,
          list: "/api/task-step-templates",
          detail: "/api/task-step-templates/{slug}",
          expand: "/api/task-step-templates/{slug}:expand",
          saveFromTask: "/api/task-step-templates/save-from-task",
        },
      },
      features: {
        temporalDashboard: {
          temporalTaskEditing: true,
        },
      },
    },
  },
};

function getStepTypeRadio(step: HTMLElement, label: "Skill" | "Tool" | "Preset") {
  return within(step).getByRole("radio", {
    name: new RegExp(`(?:Step Type\\s+)?${label}$`),
  }) as HTMLInputElement;
}

function selectStepType(step: HTMLElement, label: "Skill" | "Tool" | "Preset") {
  fireEvent.click(getStepTypeRadio(step, label));
}

async function findPrimaryStep(): Promise<HTMLElement> {
  const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
    "section",
  );
  expect(primaryStep).not.toBeNull();
  return primaryStep as HTMLElement;
}

describe("Task Create Step Type controls", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    window.history.pushState({}, "Task Create", "/tasks/new");
    window.sessionStorage.clear();
    window.localStorage.clear();
    vi.mocked(navigateTo).mockReset();
    fetchSpy = vi
      .spyOn(window, "fetch")
      .mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.startsWith("/api/tasks/skills")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: { worker: ["moonspec-orchestrate", "pr-resolver"] },
            }),
          } as Response);
        }
        if (url.startsWith("/api/github/branches")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [{ value: "main", label: "main", source: "github" }],
              defaultBranch: "main",
              error: null,
            }),
          } as Response);
        }
        if (url.startsWith("/api/v1/provider-profiles")) {
          return Promise.resolve({
            ok: true,
            json: async () => [],
          } as Response);
        }
        if (url.startsWith("/api/task-step-templates?scope=personal")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ items: [] }),
          } as Response);
        }
        if (url.startsWith("/api/task-step-templates?scope=global")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                {
                  slug: "preset-a",
                  scope: "global",
                  title: "Preset A",
                  description: "Preset A.",
                  latestVersion: "1.0.0",
                  version: "1.0.0",
                },
                {
                  slug: "preset-b",
                  scope: "global",
                  title: "Preset B",
                  description: "Preset B.",
                  latestVersion: "1.0.0",
                  version: "1.0.0",
                },
              ],
            }),
          } as Response);
        }
        if (url === "/api/executions") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ executionId: "mm:test", status: "queued" }),
          } as Response);
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({}),
        } as Response);
      });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it("offers one Step Type control with Tool Skill and Preset choices", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const step = await findPrimaryStep();
    const stepType = within(step).getByRole("group", { name: "Step Type" });

    expect(
      Array.from(stepType.querySelectorAll("label")).map((option) =>
        option.textContent?.replace("Step Type ", "").trim(),
      ),
    ).toEqual(["Skill", "Tool", "Preset"]);
    expect(getStepTypeRadio(step, "Skill").checked).toBe(true);
    expect(getStepTypeRadio(step, "Tool")).toBeTruthy();
    expect(getStepTypeRadio(step, "Preset")).toBeTruthy();
    expect(within(step).getByLabelText(/Skill \(optional\)/)).toBeTruthy();
  });

  it("presents source-consistent helper copy without internal umbrella labels", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const step = await findPrimaryStep();
    const skillRadio = getStepTypeRadio(step, "Skill");
    const toolRadio = getStepTypeRadio(step, "Tool");
    const presetRadio = getStepTypeRadio(step, "Preset");

    expect(skillRadio.closest(".queue-step-type-option")?.getAttribute("title")).toBe(
      "Skill asks an agent to perform work using reusable behavior.",
    );
    expect(toolRadio.closest(".queue-step-type-option")?.getAttribute("title")).toBe(
      "Tool runs a typed integration or system operation directly.",
    );
    expect(presetRadio.closest(".queue-step-type-option")?.getAttribute("title")).toBe(
      "Preset inserts a reusable set of configured steps.",
    );
    expect(within(step).queryByText(/Capability/)).toBeNull();
    expect(within(step).queryByText(/Activity/)).toBeNull();
    expect(within(step).queryByText(/Invocation/)).toBeNull();
    expect(within(step).queryByText(/Command/)).toBeNull();
    expect(within(step).queryByText(/Script/)).toBeNull();
  });

  it("switches type-specific configuration while preserving instructions", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const step = await findPrimaryStep();
    const instructions = within(step).getByLabelText(
      "Instructions",
    ) as HTMLTextAreaElement;

    fireEvent.change(instructions, {
      target: { value: "Keep this Step Type instruction." },
    });
    selectStepType(step, "Tool");

    expect(instructions.value).toBe("Keep this Step Type instruction.");
    expect(within(step).getByLabelText("Tool")).toBeTruthy();
    expect(within(step).queryByLabelText(/Skill \(optional\)/)).toBeNull();
    expect(within(step).queryByLabelText("Preset")).toBeNull();

    selectStepType(step, "Preset");

    expect(instructions.value).toBe("Keep this Step Type instruction.");
    expect(within(step).getByLabelText("Preset")).toBeTruthy();
    expect(within(step).getByRole("button", { name: "Preview" })).toBeTruthy();
    expect(within(step).getByRole("button", { name: "Apply preview" })).toBeTruthy();
    expect(within(step).queryByLabelText(/Skill \(optional\)/)).toBeNull();
    expect(within(step).queryByLabelText("Tool")).toBeNull();
  });

  it("keeps Preset selections scoped to each step", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.click(await screen.findByRole("button", { name: "Add Step" }));
    const primaryStep = await findPrimaryStep();
    const secondStep = (await screen.findByText("Step 2")).closest(
      "section",
    ) as HTMLElement;

    selectStepType(primaryStep, "Preset");
    selectStepType(secondStep, "Preset");
    const firstPreset = within(primaryStep).getByLabelText(
      "Preset",
    ) as HTMLSelectElement;
    const secondPreset = within(secondStep).getByLabelText(
      "Preset",
    ) as HTMLSelectElement;

    await waitFor(() => {
      expect(firstPreset.options.length).toBeGreaterThan(2);
    });
    const firstValue = firstPreset.options[1]?.value || "";
    const secondValue = firstPreset.options[2]?.value || "";
    expect(firstValue).not.toBe("");
    expect(secondValue).not.toBe("");

    fireEvent.change(firstPreset, { target: { value: firstValue } });
    fireEvent.change(secondPreset, { target: { value: secondValue } });

    expect(firstPreset.value).toBe(firstValue);
    expect(secondPreset.value).toBe(secondValue);
  });

  it("preserves hidden Skill fields but blocks Tool submission without a selected Tool", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const step = await findPrimaryStep();

    fireEvent.click(screen.getByLabelText("Show advanced step options"));
    fireEvent.change(within(step).getByLabelText(/Skill \(optional\)/), {
      target: { value: "custom-skill" },
    });
    fireEvent.change(
      within(step).getByLabelText("Step 1 Skill Args (optional JSON object)"),
      { target: { value: '{"hidden":true}' } },
    );
    fireEvent.change(
      within(step).getByLabelText(
        /Step 1 Skill Required Capabilities \(optional CSV\)/,
      ),
      { target: { value: "docker, qdrant" } },
    );
    selectStepType(step, "Tool");
    expect(within(step).queryByLabelText(/Skill \(optional\)/)).toBeNull();
    selectStepType(step, "Skill");
    expect(
      (within(step).getByLabelText(/Skill \(optional\)/) as HTMLInputElement)
        .value,
    ).toBe("custom-skill");
    expect(
      (
        within(step).getByLabelText(
          "Step 1 Skill Args (optional JSON object)",
        ) as HTMLTextAreaElement
      ).value,
    ).toBe('{"hidden":true}');
    selectStepType(step, "Tool");
    fireEvent.change(screen.getByLabelText("Instructions"), {
      target: { value: "Run a tool Step Type submission." },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(
      await screen.findByText("Select a Tool before submitting a Tool step."),
    ).toBeTruthy();
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url) === "/api/executions"),
    ).toBe(false);
  });
});
