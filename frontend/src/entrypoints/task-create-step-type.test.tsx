import { fireEvent, screen, within } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";

import type { BootPayload } from "../boot/parseBootPayload";
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

describe("Task Create Step Type authoring", () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, "Task Create", "/tasks/new");
    window.sessionStorage.clear();
    window.localStorage.clear();
    fetchSpy = vi
      .spyOn(window, "fetch")
      .mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/api/tasks/skills")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ items: { worker: ["moonspec-orchestrate"] } }),
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
          return Promise.resolve({ ok: true, json: async () => [] } as Response);
        }
        if (url.startsWith("/api/task-step-templates?scope=global")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                {
                  slug: "jira-orchestrate",
                  scope: "global",
                  title: "Jira Orchestrate",
                  description: "Implement a Jira issue.",
                  latestVersion: "1.0.0",
                  version: "1.0.0",
                },
              ],
            }),
          } as Response);
        }
        if (url.startsWith("/api/task-step-templates?scope=personal")) {
          return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
        }
        return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
      });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("shows one Step Type selector and visibly discards incompatible Skill state", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    ) as HTMLElement;

    const stepType = within(primaryStep).getByRole("group", { name: "Step Type" });
    expect(
      Array.from(stepType.querySelectorAll("label")).map((option) =>
        option.textContent?.replace("Step Type ", "").trim(),
      ),
    ).toEqual(["Skill", "Tool", "Preset"]);

    fireEvent.click(screen.getByLabelText("Show advanced step options"));
    fireEvent.change(within(primaryStep).getByLabelText(/Skill \(optional\)/), {
      target: { value: "moonspec-orchestrate" },
    });
    fireEvent.change(
      within(primaryStep).getByLabelText("Step 1 Skill Args (optional JSON object)"),
      { target: { value: '{"issueKey":"MM-568"}' } },
    );
    fireEvent.change(within(primaryStep).getByLabelText("Instructions"), {
      target: { value: "Keep these shared instructions." },
    });

    selectStepType(primaryStep, "Tool");

    expect(
      (within(primaryStep).getByLabelText("Instructions") as HTMLTextAreaElement)
        .value,
    ).toBe("Keep these shared instructions.");
    expect(within(primaryStep).getByLabelText("Tool")).toBeTruthy();
    expect(within(primaryStep).queryByLabelText(/Skill \(optional\)/)).toBeNull();
    expect(
      within(primaryStep).getByText(
        "Skill configuration discarded after changing Step Type. Shared instructions were preserved.",
      ),
    ).toBeTruthy();

    selectStepType(primaryStep, "Skill");

    expect(
      (within(primaryStep).getByLabelText(/Skill \(optional\)/) as HTMLInputElement)
        .value,
    ).toBe("");
    expect(
      (
        within(primaryStep).getByLabelText(
          "Step 1 Skill Args (optional JSON object)",
        ) as HTMLTextAreaElement
      ).value,
    ).toBe("");
    expect(fetchSpy).toHaveBeenCalled();
  });
});
