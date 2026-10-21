import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { BrowserRouter, MemoryRouter, useNavigate, type NavigateFunction } from "react-router-dom";
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
import { navigateTo } from "../lib/navigation";
import { requestWorkflowStartRouteChange } from "../lib/workflowStartRouteGuard";
import { renderWithClient } from "../utils/test-utils";
import { WorkflowStartPage } from "./workflow-start";

vi.mock("../lib/navigation", () => ({
  navigateTo: vi.fn(),
}));

function payloadWith({ presetSave = true } = {}): BootPayload {
  return {
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
          defaultTaskModelByRuntime: { codex_cli: "gpt-5.4" },
          defaultTaskEffortByRuntime: { codex_cli: "medium" },
          supportedAgentRuntimes: ["codex_cli"],
          providerProfiles: { list: "/api/v1/provider-profiles" },
          presetCatalog: {
            enabled: true,
            templateSaveEnabled: presetSave,
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
}

function recordedExecution(
  workflowId: string,
  steps: Array<{ instructions: string }> = [],
): Record<string, unknown> {
  return {
    workflowId,
    runId: `${workflowId}-run`,
    workflowType: "MoonMind.UserWorkflow",
    state: "completed",
    targetRuntime: "codex_cli",
    repository: "MoonLadderStudios/MoonMind",
    startingBranch: "main",
    publishMode: "pr",
    inputParameters: {
      targetRuntime: "codex_cli",
      workflow: {
        instructions: "Recorded workflow instructions.",
        runtime: { mode: "codex_cli" },
        git: { startingBranch: "main" },
        publish: { mode: "pr" },
        ...(steps.length > 0 ? { steps } : {}),
      },
    },
    actions: {
      canUpdateInputs: true,
      canRerun: true,
      canEditForRerun: true,
    },
  };
}

function stepSection(stepNumber: number): HTMLElement {
  const section = screen.getByText(`Step ${stepNumber}`).closest("section");
  if (!section) {
    throw new Error(`Step ${stepNumber} section is missing.`);
  }
  return section as HTMLElement;
}

function submissionBar(): HTMLElement {
  return screen.getByRole("group", { name: "Workflow submission controls" });
}

describe("Workflow Create form trust", () => {
  let fetchSpy: MockInstance;
  let executions: Record<string, Record<string, unknown>>;
  let updates: Array<{ url: string; body: Record<string, unknown> }>;

  function createRequests(): Array<Record<string, unknown>> {
    return fetchSpy.mock.calls
      .filter(([url, init]) => String(url) === "/api/executions" && init?.method === "POST")
      .map(([, init]) => JSON.parse(String(init?.body || "{}")) as Record<string, unknown>);
  }

  beforeEach(() => {
    window.history.pushState({}, "Workflow Create", "/workflows/new");
    window.sessionStorage.clear();
    window.localStorage.clear();
    vi.mocked(navigateTo).mockReset();
    executions = {};
    updates = [];
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
          return Promise.resolve({
            ok: true,
            json: async () => [
              {
                profile_id: "profile:codex-default",
                runtime_id: "codex_cli",
                account_label: "Codex Default",
                is_default: true,
              },
            ],
          } as Response);
        }
        if (url === "/mcp/tools") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              tools: [{ name: "example.raw_tool", description: "Raw tool." }],
            }),
          } as Response);
        }
        if (url === "/api/executions" && init?.method === "POST") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ workflowId: "mm:created" }),
          } as Response);
        }
        const detail = url.match(/^\/api\/executions\/([^/?]+)\?source=temporal$/);
        if (detail?.[1]) {
          const execution = executions[decodeURIComponent(detail[1])];
          if (execution) {
            return Promise.resolve({ ok: true, json: async () => execution } as Response);
          }
        }
        const update = url.match(/^\/api\/executions\/([^/?]+)\/update$/);
        if (update?.[1]) {
          updates.push({
            url,
            body: JSON.parse(String(init?.body || "{}")) as Record<string, unknown>,
          });
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "continue_as_new",
              message: "Rerun requested. New execution created.",
              execution: { workflowId: "mm:rerun-created" },
            }),
          } as Response);
        }
        if (url === "/api/presets") {
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
              inputs: [],
            }),
          } as Response);
        }
        if (url.startsWith("/api/presets/jira-orchestrate:expand?scope=global")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              steps: [
                {
                  id: "tpl:jira-orchestrate:1.0.0:01",
                  title: "Read Jira issue",
                  instructions: "Read the Jira issue.",
                  skill: { id: "jira-implement" },
                },
                {
                  id: "tpl:jira-orchestrate:1.0.0:02",
                  title: "Implement Jira issue",
                  instructions: "Implement the Jira issue.",
                  skill: { id: "moonspec-orchestrate" },
                },
              ],
              appliedTemplate: { slug: "jira-orchestrate" },
              warnings: [],
            }),
          } as Response);
        }
        if (url === "/api/presets/save-from-workflow") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ slug: "saved", title: "Saved preset" }),
          } as Response);
        }
        return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
      });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    vi.restoreAllMocks();
  });

  it("starts with auto selected and offers explicit skills from the dropdown", async () => {
    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);

    const skill = await screen.findByRole("combobox", { name: "Step 1 skill" }) as HTMLInputElement;
    expect(skill.value).toBe("auto");
    expect(skill.placeholder).toBe("auto");

    fireEvent.pointerDown(screen.getByRole("button", { name: "Show skill options" }));
    expect(screen.getByRole("option", { name: "auto", selected: true })).toBeTruthy();
    const explicitSkill = await screen.findByRole("option", { name: "moonspec-orchestrate" });
    fireEvent.pointerDown(explicitSkill);
    expect(skill.value).toBe("moonspec-orchestrate");
  });

  it("places Advanced mode at the bottom of the form and reveals optional controls", async () => {
    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);

    const advanced = await screen.findByRole("checkbox", { name: "Advanced mode" });
    expect(within(screen.getByRole("region", { name: "Header" })).queryByRole("checkbox")).toBeNull();
    expect(within(screen.getByRole("region", { name: "Submit" })).getByRole("checkbox", { name: "Advanced mode" })).toBe(advanced);
    expect(screen.queryByLabelText("Priority")).toBeNull();
    fireEvent.click(advanced);
    expect(screen.getByLabelText("Priority")).toBeTruthy();
  });

  it("shows an off-screen validation error in the submit bar and focuses the invalid field", async () => {
    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Run the primary step." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));
    const second = stepSection(2);
    fireEvent.click(within(second).getByRole("radio", { name: "Tool" }));
    fireEvent.change(within(second).getByLabelText("Step 2 Instructions"), {
      target: { value: "Run the tool step." },
    });

    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));

    const message = await within(submissionBar()).findByRole("alert");
    expect(message.textContent).toBe("Select a Tool before submitting Step 2.");
    const toolId = within(second).getByLabelText("Tool ID");
    await waitFor(() => expect(document.activeElement).toBe(toolId));
    expect(toolId.getAttribute("aria-invalid")).toBe("true");
    expect(createRequests()).toHaveLength(0);

    fireEvent.change(toolId, { target: { value: "example.raw_tool" } });
    expect(within(second).getByLabelText("Tool ID").getAttribute("aria-invalid")).not.toBe("true");
  });

  it("follows Create, Edit and Compare URL changes without losing the draft", async () => {
    executions["mm:mode-switch"] = recordedExecution("mm:mode-switch");
    const initial = "/workflows/new?rerunExecutionId=mm%3Amode-switch&mode=edit";
    window.history.pushState({}, "Workflow Edit", initial);
    let navigate: NavigateFunction | null = null;
    function CaptureNavigate() {
      navigate = useNavigate();
      return null;
    }

    renderWithClient(
      <MemoryRouter initialEntries={[initial]}>
        <CaptureNavigate />
        <WorkflowStartPage payload={payloadWith()} />
      </MemoryRouter>,
    );

    const instructions = (await screen.findByLabelText("Instructions")) as HTMLTextAreaElement;
    await waitFor(() => expect(instructions.value).toBe("Recorded workflow instructions."));
    expect(screen.getByRole("heading", { name: "Edit Workflow" })).toBeTruthy();
    fireEvent.change(instructions, { target: { value: "Edited before comparing." } });

    act(() => {
      navigate?.("/workflows/new?rerunExecutionId=mm%3Amode-switch&mode=compare");
    });

    expect(await screen.findByRole("heading", { name: "Compare Workflow" })).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Start Comparison Run" })).toBeTruthy();
    await waitFor(() =>
      expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe(
        "Edited before comparing.",
      ),
    );
  });

  it("keeps every Advanced value after closing it and names each override", async () => {
    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Keep advanced overrides." },
    });
    const advanced = screen.getByLabelText("Advanced mode") as HTMLInputElement;
    fireEvent.click(advanced);
    fireEvent.change(screen.getByLabelText("Priority"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("Max Attempts"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Step 1 Skill Args (optional JSON object)"), {
      target: { value: '{"featureKey":"MM-1"}' },
    });
    fireEvent.click(advanced);

    expect(screen.queryByLabelText("Priority")).toBeNull();
    expect(
      screen.getByText("Overrides: priority 7, max attempts 5, 1 step override"),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));

    await waitFor(() => expect(createRequests()).toHaveLength(1));
    const request = createRequests()[0] as {
      priority: number;
      maxAttempts: number;
      payload: { task: { inputs?: Record<string, unknown> } };
    };
    expect(request.priority).toBe(7);
    expect(request.maxAttempts).toBe(5);
    expect(request.payload.task.inputs).toEqual({ featureKey: "MM-1" });
  });

  it("reveals and focuses an invalid value held by a closed Advanced section", async () => {
    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Reject a hidden invalid value." },
    });
    const advanced = screen.getByLabelText("Advanced mode") as HTMLInputElement;
    fireEvent.click(advanced);
    fireEvent.change(screen.getByLabelText("Max Attempts"), { target: { value: "0" } });
    fireEvent.click(advanced);
    expect(screen.getByText("Overrides: max attempts 0")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));

    const message = await within(submissionBar()).findByRole("alert");
    expect(message.textContent).toBe("Max Attempts must be an integer >= 1.");
    const maxAttempts = await screen.findByLabelText("Max Attempts");
    await waitFor(() => expect(document.activeElement).toBe(maxAttempts));
    expect(maxAttempts.getAttribute("aria-invalid")).toBe("true");
    expect(createRequests()).toHaveLength(0);
  });

  it("navigates to the new run after a successful rerun and cannot resubmit it", async () => {
    executions["mm:rerun-source"] = recordedExecution("mm:rerun-source");
    window.history.pushState({}, "Workflow Rerun", "/workflows/new?rerunExecutionId=mm%3Arerun-source");

    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);

    const instructions = (await screen.findByLabelText("Instructions")) as HTMLTextAreaElement;
    await waitFor(() => expect(instructions.value).toBe("Recorded workflow instructions."));
    fireEvent.change(instructions, { target: { value: "Rerun with changes." } });
    const start = screen.getByRole("button", { name: "Start New Run" });
    fireEvent.click(start);

    await waitFor(() =>
      expect(navigateTo).toHaveBeenCalledWith("/workflows/mm%3Arerun-created?source=temporal"),
    );
    expect(window.sessionStorage.getItem("moonmind.temporalTaskEditing.notice")).toBe(
      "Rerun was requested and the latest execution view is ready.",
    );
    expect(updates).toHaveLength(1);

    fireEvent.click(start);
    await act(async () => {
      await Promise.resolve();
    });
    expect(updates).toHaveLength(1);
    expect(start.getAttribute("aria-disabled")).toBe("true");
  });

  it("saves Tool steps into a preset", async () => {
    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Plan the change." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));
    const second = stepSection(2);
    fireEvent.click(within(second).getByRole("radio", { name: "Tool" }));
    fireEvent.change(within(second).getByLabelText("Step 2 Instructions"), {
      target: { value: "Run the raw tool." },
    });
    fireEvent.change(within(second).getByLabelText("Tool ID"), {
      target: { value: "example.raw_tool" },
    });

    fireEvent.click(
      within(screen.getByLabelText("Preset Management")).getByRole("button", {
        name: "Save preset",
      }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Save preset" });
    fireEvent.change(within(dialog).getByLabelText("Preset Name"), {
      target: { value: "Plan and run" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm save preset" }));

    await waitFor(() =>
      expect(
        fetchSpy.mock.calls.some(([url]) => String(url) === "/api/presets/save-from-workflow"),
      ).toBe(true),
    );
    const saveCall = fetchSpy.mock.calls.find(
      ([url]) => String(url) === "/api/presets/save-from-workflow",
    );
    const body = JSON.parse(String(saveCall?.[1]?.body || "{}")) as {
      steps: Array<Record<string, unknown>>;
    };
    expect(body.steps).toHaveLength(2);
    expect(body.steps[0]).toMatchObject({ instructions: "Plan the change." });
    expect(body.steps[1]).toEqual({
      type: "tool",
      instructions: "Run the raw tool.",
      tool: { id: "example.raw_tool", inputs: {} },
    });
  });

  it("shows preset Expand results when preset saving is off", async () => {
    renderWithClient(<WorkflowStartPage payload={payloadWith({ presetSave: false })} />);

    const step = stepSection(1);
    fireEvent.click(within(step).getByRole("radio", { name: "Preset" }));
    fireEvent.change(within(step).getByLabelText("Instructions"), {
      target: { value: "MM-1" },
    });
    const presetSelect = within(step).getByLabelText("Preset Template") as HTMLSelectElement;
    await waitFor(() => expect(presetSelect.options.length).toBeGreaterThan(1));
    fireEvent.change(presetSelect, { target: { value: "global::::jira-orchestrate" } });
    fireEvent.click(within(step).getByRole("button", { name: "Expand" }));

    expect(
      await screen.findByText(/Applied preset 'Jira Orchestrate' \(2 steps\)\./),
    ).toBeTruthy();
    expect(screen.queryByLabelText("Preset Management")).toBeNull();
  });

  describe("leave-page guard", () => {
    let confirmSpy: MockInstance;

    beforeEach(() => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    });

    async function renderTwoStepEdit() {
      executions["mm:two-steps"] = recordedExecution("mm:two-steps", [
        { instructions: "First recorded step." },
        { instructions: "Second recorded step." },
      ]);
      window.history.pushState({}, "Workflow Edit", "/workflows/new?editExecutionId=mm%3Atwo-steps");
      renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
      await screen.findByDisplayValue("Second recorded step.");
      // Loading the recorded draft is not an operator edit.
      expect(requestWorkflowStartRouteChange("/workflows")).toBe(true);
      expect(confirmSpy).not.toHaveBeenCalled();
    }

    async function renderEditedSecondOfTwoHistoryEntries() {
      executions["mm:history-first"] = recordedExecution("mm:history-first");
      executions["mm:history-second"] = recordedExecution("mm:history-second");
      window.history.pushState({}, "", "/workflows/new?editExecutionId=mm%3Ahistory-first");
      window.history.pushState({}, "", "/workflows/new?editExecutionId=mm%3Ahistory-second");
      renderWithClient(
        <BrowserRouter>
          <WorkflowStartPage payload={payloadWith()} />
        </BrowserRouter>,
      );
      const instructions = (await screen.findByLabelText("Instructions")) as HTMLTextAreaElement;
      await waitFor(() => expect(instructions.value).toBe("Recorded workflow instructions."));
      fireEvent.change(instructions, { target: { value: "Edited the second workflow." } });
    }

    it("keeps an edited draft when the operator declines leaving it through Back", async () => {
      await renderEditedSecondOfTwoHistoryEntries();

      act(() => {
        window.history.back();
      });

      await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
      await waitFor(() =>
        expect(window.location.search).toBe("?editExecutionId=mm%3Ahistory-second"),
      );
      expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe(
        "Edited the second workflow.",
      );
    });

    it("follows Back to the other workflow once the operator confirms", async () => {
      await renderEditedSecondOfTwoHistoryEntries();
      confirmSpy.mockReturnValue(true);

      act(() => {
        window.history.back();
      });

      await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
      await waitFor(() =>
        expect(window.location.search).toBe("?editExecutionId=mm%3Ahistory-first"),
      );
      await waitFor(() =>
        expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe(
          "Recorded workflow instructions.",
        ),
      );
    });

    it("asks before leaving after a step is added", async () => {
      renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
      fireEvent.click(await screen.findByRole("button", { name: "Add Step" }));

      expect(requestWorkflowStartRouteChange("/workflows")).toBe(false);
      expect(confirmSpy).toHaveBeenCalledTimes(1);
    });

    it("asks before leaving after a step is moved", async () => {
      await renderTwoStepEdit();
      fireEvent.click(within(stepSection(2)).getByRole("button", { name: "Move step up" }));

      expect(requestWorkflowStartRouteChange("/workflows")).toBe(false);
      expect(confirmSpy).toHaveBeenCalledTimes(1);
    });

    it("asks before leaving after a step is removed", async () => {
      await renderTwoStepEdit();
      fireEvent.click(within(stepSection(2)).getByRole("button", { name: "Remove step" }));

      expect(requestWorkflowStartRouteChange("/workflows")).toBe(false);
      expect(confirmSpy).toHaveBeenCalledTimes(1);
    });
  });
});
