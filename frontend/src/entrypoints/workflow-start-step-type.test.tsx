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
import { WorkflowStartPage } from "./workflow-start";

vi.mock("../lib/navigation", () => ({
  navigateTo: vi.fn(),
}));

const mockPayload: BootPayload = {
  page: "workflow-start",
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
        defaultAgentRuntime: "codex_cli",
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
        supportedAgentRuntimes: ["codex_cli"],
        providerProfiles: {
          list: "/api/v1/provider-profiles",
        },
        presetCatalog: {
          enabled: true,
          templateSaveEnabled: true,
          list: "/api/presets",
          detail: "/api/presets/{slug}",
          expand: "/api/presets/{slug}:expand",
          saveFromWorkflow: "/api/presets/save-from-workflow",
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
    name: label,
  }) as HTMLInputElement;
}

function selectStepType(step: HTMLElement, label: "Skill" | "Tool" | "Preset") {
  const radio = getStepTypeRadio(step, label);
  fireEvent.click(radio);
}

describe("Task Create Step Type authoring", () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, "Task Create", "/workflows/new");
    window.sessionStorage.clear();
    window.localStorage.clear();
    fetchSpy = vi
      .spyOn(window, "fetch")
      .mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.startsWith("/api/workflows/skills")) {
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
        if (url === "/mcp/tools") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              tools: [
                {
                  name: "security.pentest.run",
                  description: "Run an authorized PentestGPT workload.",
                  inputSchema: {
                    type: "object",
                    required: [
                      "target",
                      "scope_artifact_ref",
                      "operation_mode",
                      "runner_profile_id",
                    ],
                    properties: {
                      target: {
                        type: "string",
                        title: "Target",
                        description:
                          "Approved target URL, host, CIDR, FQDN, or application.",
                      },
                      scope_artifact_ref: {
                        type: "string",
                        title: "Approved scope artifact",
                        description:
                          "ArtifactRef for the approved pentest scope document.",
                      },
                      operation_mode: {
                        type: "string",
                        title: "Operation mode",
                        enum: ["recon_only", "validate_hypothesis"],
                        default: "recon_only",
                      },
                      runner_profile_id: {
                        type: "string",
                        title: "Runner profile",
                        enum: ["pentestgpt-claude-oauth"],
                        default: "pentestgpt-claude-oauth",
                      },
                      objective: {
                        type: "string",
                        title: "Objective",
                      },
                      time_budget_minutes: {
                        type: "integer",
                        title: "Time budget minutes",
                        minimum: 1,
                        maximum: 120,
                        default: 60,
                      },
                      evidence_level: {
                        type: "string",
                        title: "Evidence level",
                        enum: ["minimal", "standard"],
                        default: "standard",
                      },
                    },
                    additionalProperties: false,
                  },
                },
                {
                  name: "example.raw_tool",
                  description: "Raw tool.",
                },
              ],
            }),
          } as Response);
        }
        if (url === "/api/artifacts" && init?.method === "POST") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              artifact_ref: { artifact_id: "art_scope_123" },
              upload: {
                mode: "single_put",
                upload_url: "/api/artifacts/art_scope_123/content",
                required_headers: {},
              },
            }),
          } as Response);
        }
        if (url === "/api/artifacts/art_scope_123/content") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ artifact_id: "art_scope_123" }),
          } as Response);
        }
        if (url === "/api/artifacts/art_scope_123/complete") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ artifact_id: "art_scope_123" }),
          } as Response);
        }
        if (url === "/api/executions") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ workflowId: "wf-123" }),
          } as Response);
        }
        if (url.startsWith("/api/presets?scope=global")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                {
                  slug: "jira-orchestrate",
                  scope: "global",
                  title: "Jira Orchestrate",
                  description: "Implement a Jira issue.",
                },
              ],
            }),
          } as Response);
        }
        if (url.startsWith("/api/presets?scope=personal")) {
          return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
        }
        if (url.startsWith("/api/presets/jira-orchestrate?scope=global")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              slug: "jira-orchestrate",
              scope: "global",
              title: "Jira Orchestrate",
              description: "Implement a Jira issue.",
              inputs: [
                {
                  name: "feature_request",
                  label: "Feature Request",
                  type: "text",
                  required: true,
                },
              ],
              inputSchema: {
                type: "object",
                required: ["feature_request"],
                properties: {
                  feature_request: {
                    type: "string",
                    title: "Feature Request",
                  },
                },
              },
            }),
          } as Response);
        }
        if (url.startsWith("/api/presets/jira-orchestrate:expand?scope=global")) {
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
    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    const primaryStep = (await screen.findByText("Step 1")).closest(
      "section",
    ) as HTMLElement;

    const stepType = within(primaryStep).getByRole("group", { name: "Step Type" });
    expect(
      Array.from(stepType.querySelectorAll("label")).map((option) =>
        option.textContent?.trim(),
      ),
    ).toEqual(["Skill", "Tool", "Preset"]);

    fireEvent.click(screen.getByLabelText("Advanced mode"));
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
      (within(primaryStep).getByLabelText("Step 1 Instructions") as HTMLTextAreaElement)
        .value,
    ).toBe("Keep these shared instructions.");
    expect(within(primaryStep).getByLabelText("Tool ID")).toBeTruthy();
    expect(within(primaryStep).queryByLabelText(/Skill \(optional\)/)).toBeNull();

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

  it("clears Preset configuration after changing Step Type without showing the discard warning", async () => {
    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    const primaryStep = (await screen.findByText("Step 1")).closest(
      "section",
    ) as HTMLElement;
    const instructions = within(primaryStep).getByLabelText(
      "Instructions",
    ) as HTMLTextAreaElement;

    fireEvent.change(instructions, {
      target: { value: "Keep these shared instructions." },
    });
    selectStepType(primaryStep, "Preset");

    const presetSelect = within(primaryStep).getByLabelText(
      "Preset Template",
    ) as HTMLSelectElement;
    await waitFor(() => {
      expect(presetSelect.options.length).toBeGreaterThan(1);
    });
    fireEvent.change(presetSelect, {
      target: { value: "global::::jira-orchestrate" },
    });

    selectStepType(primaryStep, "Tool");

    expect(
      (within(primaryStep).getByLabelText("Step 1 Instructions") as HTMLTextAreaElement)
        .value,
    ).toBe("Keep these shared instructions.");
    expect(
      screen.queryByText(
        "Preset configuration discarded after changing Step Type. Shared instructions were preserved.",
      ),
    ).toBeNull();

    selectStepType(primaryStep, "Preset");
    expect(
      (within(primaryStep).getByLabelText("Preset Template") as HTMLSelectElement)
        .value,
    ).toBe("");
  });

  it("expands a preset step in place and pushes following steps down", async () => {
    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    fireEvent.click(await screen.findByRole("button", { name: "Add Step" }));
    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));

    const firstStep = (await screen.findByText("Step 1")).closest(
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
      within(secondStep).getByLabelText("Instructions"),
      {
        target: { value: "the selected Jira issue" },
      },
    );

    const presetSelect = within(secondStep).getByLabelText(
      "Preset Template",
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
      await screen.findByText(/Applied preset 'Jira Orchestrate' \(2 steps\)\./),
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

  it("expands a preset using the latest preset instructions", async () => {
    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    const step = (await screen.findByText("Step 1")).closest(
      "section",
    ) as HTMLElement;
    selectStepType(step, "Preset");
    const presetSelect = within(step).getByLabelText("Preset Template") as HTMLSelectElement;
    await waitFor(() => {
      expect(presetSelect.options.length).toBeGreaterThan(1);
    });
    fireEvent.change(presetSelect, {
      target: { value: "global::::jira-orchestrate" },
    });
    const instructions = within(step).getByLabelText(
      "Instructions",
    );
    fireEvent.change(instructions, { target: { value: "old issue" } });

    fireEvent.change(instructions, { target: { value: "new issue" } });
    fireEvent.click(within(step).getByRole("button", { name: "Expand" }));

    expect(await screen.findByDisplayValue("Read new issue.")).toBeTruthy();
    expect(screen.getByDisplayValue("Implement new issue.")).toBeTruthy();
    expect(screen.queryByDisplayValue("Read old issue.")).toBeNull();

    const expandCalls = fetchSpy.mock.calls.filter(([url]) =>
      String(url).startsWith(
        "/api/presets/jira-orchestrate:expand?scope=global",
      ),
    );
    expect(expandCalls).toHaveLength(1);
    const body = JSON.parse(String(expandCalls[0]?.[1]?.body || "{}"));
    expect(body.inputs.feature_request).toBe("new issue");
  });

  it("ignores async preset expansion results after the preset step changes", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    let resolveExpand: ((response: Response) => void) | null = null;
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (
        url.startsWith(
          "/api/presets/jira-orchestrate:expand?scope=global",
        )
      ) {
        return new Promise<Response>((resolve) => {
          resolveExpand = resolve;
        });
      }
      return defaultFetch?.(input, init) as ReturnType<typeof window.fetch>;
    });

    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    const step = (await screen.findByText("Step 1")).closest(
      "section",
    ) as HTMLElement;
    selectStepType(step, "Preset");
    const presetSelect = within(step).getByLabelText("Preset Template") as HTMLSelectElement;
    await waitFor(() => {
      expect(presetSelect.options.length).toBeGreaterThan(1);
    });
    fireEvent.change(presetSelect, {
      target: { value: "global::::jira-orchestrate" },
    });
    const instructions = within(step).getByLabelText(
      "Instructions",
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

  it("renders PentestGPT schema fields and blocks missing required inputs", async () => {
    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    const step = (await screen.findByText("Step 1")).closest(
      "section",
    ) as HTMLElement;
    selectStepType(step, "Tool");
    fireEvent.click(
      await screen.findByRole("button", { name: "security.pentest.run" }),
    );

    const targetFields = await screen.findAllByLabelText(/^Target/);
    expect(targetFields[0]).toBeTruthy();
    expect(screen.getByLabelText(/^Operation mode/)).toBeTruthy();
    expect(
      (screen.getByLabelText(/^Operation mode/) as HTMLSelectElement).value,
    ).toBe("recon_only");
    expect(
      (screen.getByLabelText(/^Runner profile/) as HTMLSelectElement).value,
    ).toBe("pentestgpt-claude-oauth");
    expect(screen.getByText("Approved Scope")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findAllByText("Target is required.")).toHaveLength(2);
  });

  it("generates and attaches a Pentest scope artifact before canonical tool submit", async () => {
    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    const step = (await screen.findByText("Step 1")).closest(
      "section",
    ) as HTMLElement;
    selectStepType(step, "Tool");
    fireEvent.click(
      await screen.findByRole("button", { name: "security.pentest.run" }),
    );
    const [targetField] = await screen.findAllByLabelText(/^Target/);
    expect(targetField).toBeTruthy();
    fireEvent.change(targetField!, {
      target: { value: "https://lab-app.internal.example" },
    });
    fireEvent.click(
      screen.getByLabelText(
        "I confirm I am authorized to test this target within the selected scope.",
      ),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Generate and attach scope",
      }),
    );

    await screen.findByText("Approved scope attached: art_scope_123");

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      const createCall = fetchSpy.mock.calls.find(
        ([url]) => String(url) === "/api/executions",
      );
      expect(createCall).toBeTruthy();
      const body = JSON.parse(String(createCall?.[1]?.body || "{}"));
      const tool =
        body.payload.task.steps[0].tool as Record<string, Record<string, unknown>>;
      expect(tool.id).toBe("security.pentest.run");
      expect(tool.inputs).toMatchObject({
        target: "https://lab-app.internal.example",
        scope_artifact_ref: "art_scope_123",
        operation_mode: "recon_only",
        runner_profile_id: "pentestgpt-claude-oauth",
        time_budget_minutes: 60,
        evidence_level: "standard",
      });
      expect(tool.inputs).not.toHaveProperty("approved_scope");
    });

    const artifactCreateCall = fetchSpy.mock.calls.find(
      ([url, init]) => String(url) === "/api/artifacts" && init?.method === "POST",
    );
    const artifactBody = JSON.parse(
      String(artifactCreateCall?.[1]?.body || "{}"),
    );
    expect(artifactBody.retention_class).toBe("pinned");
    expect(artifactBody.metadata.artifact_type).toBe("approved_pentest_scope");

    const artifactContentCall = fetchSpy.mock.calls.find(
      ([url]) => String(url) === "/api/artifacts/art_scope_123/content",
    );
    const generatedScope = JSON.parse(
      String(artifactContentCall?.[1]?.body || "{}"),
    );
    expect(generatedScope.required_network_attachment_type).toBeNull();
    expect(generatedScope).not.toHaveProperty("authorized_principals");
    expect(generatedScope.targets).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ value: "https://lab-app.internal.example" }),
        expect.objectContaining({ value: "lab-app.internal.example" }),
      ]),
    );
  });
});
