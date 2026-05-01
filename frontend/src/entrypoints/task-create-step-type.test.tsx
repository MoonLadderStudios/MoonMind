import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
      .mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
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
        if (url.startsWith("/api/task-step-templates/jira-orchestrate?scope=global")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              slug: "jira-orchestrate",
              scope: "global",
              title: "Jira Orchestrate",
              description: "Implement a Jira issue.",
              latestVersion: "1.0.0",
              version: "1.0.0",
              inputs: [
                {
                  name: "feature_request",
                  label: "Feature Request",
                  type: "text",
                  required: true,
                },
              ],
            }),
          } as Response);
        }
        if (url.startsWith("/api/task-step-templates/jira-orchestrate:expand?scope=global")) {
          const body = JSON.parse(String(init?.body || "{}")) as {
            inputs?: { feature_request?: string };
          };
          const featureRequest = String(
            body.inputs?.feature_request || "the selected Jira issue",
          );
          return Promise.resolve({
            ok: true,
            json: async () => ({
              steps: [
                {
                  id: "tpl:jira-orchestrate:1.0.0:01",
                  title: "Read Jira issue",
                  instructions: `Read ${featureRequest}.`,
                  skill: { id: "jira-implement" },
                },
                {
                  id: "tpl:jira-orchestrate:1.0.0:02",
                  title: "Implement Jira issue",
                  instructions: `Implement ${featureRequest}.`,
                  skill: { id: "moonspec-orchestrate" },
                },
              ],
              appliedTemplate: {
                slug: "jira-orchestrate",
                version: "1.0.0",
              },
              warnings: [],
            }),
          } as Response);
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

  it("expands a preset step in place and pushes following steps down", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.click(await screen.findByRole("button", { name: "Add Step" }));
    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));

    const firstStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    ) as HTMLElement;
    const secondStep = (await screen.findByText("Step 2")).closest(
      "section",
    ) as HTMLElement;
    const thirdStep = (await screen.findByText("Step 3")).closest(
      "section",
    ) as HTMLElement;

    fireEvent.change(within(firstStep).getByLabelText("Instructions"), {
      target: { value: "Keep first manual skill." },
    });
    fireEvent.change(within(thirdStep).getByLabelText("Instructions"), {
      target: { value: "Keep trailing skill." },
    });
    selectStepType(secondStep, "Preset");
    fireEvent.change(
      within(secondStep).getByLabelText("Feature Request / Initial Instructions"),
      {
        target: { value: "the selected Jira issue" },
      },
    );

    const presetSelect = within(secondStep).getByLabelText(
      "Preset",
    ) as HTMLSelectElement;
    await waitFor(() => {
      expect(presetSelect.options.length).toBeGreaterThan(1);
    });
    fireEvent.change(presetSelect, {
      target: { value: "global::::jira-orchestrate" },
    });

    fireEvent.click(within(secondStep).getByRole("button", { name: "Expand" }));

    await screen.findByDisplayValue("Read the selected Jira issue.");
    expect(screen.getByDisplayValue("Implement the selected Jira issue.")).toBeTruthy();
    expect(screen.getByDisplayValue("Keep first manual skill.")).toBeTruthy();
    expect(screen.getByDisplayValue("Keep trailing skill.")).toBeTruthy();
    expect(
      await screen.findByText("Applied preset 'Jira Orchestrate' (2 steps)."),
    ).toBeTruthy();

    const renderedSteps = Array.from(
      document.querySelectorAll<HTMLElement>(".queue-step-section"),
    );
    expect(renderedSteps).toHaveLength(4);
    const [renderedFirst, renderedSecond, renderedThird, renderedFourth] =
      renderedSteps;
    if (!renderedFirst || !renderedSecond || !renderedThird || !renderedFourth) {
      throw new Error("Expected four rendered steps after preset expansion.");
    }
    expect(
      (within(renderedFirst).getByLabelText("Instructions") as HTMLTextAreaElement)
        .value,
    ).toBe("Keep first manual skill.");
    expect(
      (within(renderedSecond).getByLabelText("Instructions") as HTMLTextAreaElement)
        .value,
    ).toBe("Read the selected Jira issue.");
    expect(
      (within(renderedThird).getByLabelText("Instructions") as HTMLTextAreaElement)
        .value,
    ).toBe("Implement the selected Jira issue.");
    expect(
      (within(renderedFourth).getByLabelText("Instructions") as HTMLTextAreaElement)
        .value,
    ).toBe("Keep trailing skill.");
  });

  it("regenerates a preset preview after preset instructions change", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const step = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    ) as HTMLElement;
    selectStepType(step, "Preset");
    const presetSelect = within(step).getByLabelText("Preset") as HTMLSelectElement;
    await waitFor(() => {
      expect(presetSelect.options.length).toBeGreaterThan(1);
    });
    fireEvent.change(presetSelect, {
      target: { value: "global::::jira-orchestrate" },
    });
    const instructions = within(step).getByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.change(instructions, { target: { value: "old issue" } });

    fireEvent.click(within(step).getByRole("button", { name: "Preview" }));
    await waitFor(() => {
      const previewCall = fetchSpy.mock.calls.find(([url]) =>
        String(url).startsWith(
          "/api/task-step-templates/jira-orchestrate:expand?scope=global",
        ),
      );
      expect(previewCall).toBeTruthy();
      const body = JSON.parse(String(previewCall?.[1]?.body || "{}"));
      expect(body.inputs.feature_request).toBe("old issue");
    });

    fireEvent.change(instructions, { target: { value: "new issue" } });
    fireEvent.click(within(step).getByRole("button", { name: "Expand" }));

    expect(await screen.findByDisplayValue("Read new issue.")).toBeTruthy();
    expect(screen.getByDisplayValue("Implement new issue.")).toBeTruthy();
    expect(screen.queryByDisplayValue("Read old issue.")).toBeNull();

    const expandCalls = fetchSpy.mock.calls.filter(([url]) =>
      String(url).startsWith(
        "/api/task-step-templates/jira-orchestrate:expand?scope=global",
      ),
    );
    expect(expandCalls).toHaveLength(2);
  });

  it("ignores async preset expansion results after the preset step changes", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    let resolveExpand: ((response: Response) => void) | null = null;
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (
        url.startsWith(
          "/api/task-step-templates/jira-orchestrate:expand?scope=global",
        )
      ) {
        return new Promise<Response>((resolve) => {
          resolveExpand = resolve;
        });
      }
      return defaultFetch?.(input, init) as ReturnType<typeof window.fetch>;
    });

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const step = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    ) as HTMLElement;
    selectStepType(step, "Preset");
    const presetSelect = within(step).getByLabelText("Preset") as HTMLSelectElement;
    await waitFor(() => {
      expect(presetSelect.options.length).toBeGreaterThan(1);
    });
    fireEvent.change(presetSelect, {
      target: { value: "global::::jira-orchestrate" },
    });
    const instructions = within(step).getByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.change(instructions, { target: { value: "old issue" } });

    fireEvent.click(within(step).getByRole("button", { name: "Expand" }));
    await waitFor(() => {
      expect(resolveExpand).not.toBeNull();
      expect(presetSelect.disabled).toBe(true);
    });
    fireEvent.change(instructions, { target: { value: "new issue" } });

    const resolve = resolveExpand as ((response: Response) => void) | null;
    if (!resolve) {
      throw new Error("Preset expansion request did not start.");
    }
    resolve({
      ok: true,
      json: async () => ({
        steps: [
          {
            id: "tpl:jira-orchestrate:1.0.0:01",
            title: "Read Jira issue",
            instructions: "Read old issue.",
            skill: { id: "jira-implement" },
          },
        ],
        appliedTemplate: {
          slug: "jira-orchestrate",
          version: "1.0.0",
        },
        warnings: [],
      }),
    } as Response);

    await waitFor(() => {
      expect(presetSelect.disabled).toBe(false);
    });
    expect(screen.queryByDisplayValue("Read old issue.")).toBeNull();
    expect(
      (instructions as HTMLTextAreaElement).value,
    ).toBe("new issue");
  });
});
