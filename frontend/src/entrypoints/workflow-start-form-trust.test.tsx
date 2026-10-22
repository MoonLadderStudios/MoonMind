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

function payloadWith({ presetSave = true, attachments = false } = {}): BootPayload {
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
          attachmentPolicy: {
            enabled: attachments, maxCount: 4, maxBytes: 1024 * 1024,
            totalBytes: 2 * 1024 * 1024, allowedContentTypes: ["image/png"],
          },
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


  it("replays a lost Create acknowledgment and changes keys for new intent", async () => {
    const original = fetchSpy.getMockImplementation()!;
    const keys: string[] = [];
    const accepted = new Set<string>();
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/executions" && init?.method === "POST") {
        const key = JSON.parse(String(init.body)).payload.task.idempotencyKey;
        keys.push(key);
        accepted.add(key || `unkeyed-${keys.length}`);
        throw new Error(`Lost response ${keys.length}`);
      }
      return original(input, init);
    });
    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
    const instructions = await screen.findByLabelText("Instructions");
    fireEvent.change(instructions, { target: { value: "First intent." } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText("Lost response 1");
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText("Lost response 2");
    expect(keys[0]).toEqual(expect.any(String));
    expect(keys[0]).not.toBe("");
    expect(keys[1]).toBe(keys[0]);
    expect(accepted.size).toBe(1);
    fireEvent.change(instructions, { target: { value: "Second intent." } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText("Lost response 3");
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("does not let a late Create acknowledgment navigate away from an edited draft", async () => {
    const original = fetchSpy.getMockImplementation()!;
    let finish!: (response: Response) => void;
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/executions" && init?.method === "POST") {
        return new Promise<Response>((resolve) => { finish = resolve; });
      }
      return original(input, init);
    });
    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
    const instructions = await screen.findByLabelText("Instructions");
    fireEvent.change(instructions, { target: { value: "Submitted intent." } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await waitFor(() => expect(finish).toBeDefined());
    fireEvent.change(instructions, { target: { value: "Keep this newer draft." } });
    await act(async () => finish({ ok: true, json: async () => ({ workflowId: "mm:old-intent" }) } as Response));
    expect(navigateTo).not.toHaveBeenCalled();
    expect((instructions as HTMLTextAreaElement).value).toBe("Keep this newer draft.");
    expect(screen.getByRole("link", { name: "Open accepted result" }).getAttribute("href")).toBe("/workflows/mm%3Aold-intent?source=temporal");
    expect(screen.getByText("Earlier submission was accepted. Your current draft has been kept.").getAttribute("role")).toBe("status");
  });

  it.each(["lost acknowledgment", "auth_required", "origin_forbidden"] as const)("preserves an accepted Create after input artifact link %s", async (failure) => {
    const original = fetchSpy.getMockImplementation()!;
    let creates = 0;
    let artifacts = 0;
    let links = 0;
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/executions" && init?.method === "POST") {
        creates += 1;
        return { ok: true, json: async () => ({ workflowId: "mm:accepted", runId: "run-1", namespace: "default", task_instructions: "PRIVATE_INSTRUCTIONS", input_parameters: { private: "PRIVATE_PARAMETERS", token: "PRIVATE_SECRET" } }) } as Response;
      }
      if (url === "/api/artifacts" && init?.method === "POST") {
        artifacts += 1;
        return { ok: true, json: async () => ({ artifact_ref: { artifact_id: "large-input" }, upload: { upload_url: "/api/artifacts/large-input/content" } }) } as Response;
      }
      if (url === "/api/artifacts/large-input" && !init?.method) {
        return { ok: true, json: async () => ({ links: [{ namespace: "default", workflow_id: "mm:accepted", run_id: "run-1", link_type: "input.instructions", label: "Submitted Workflow Input" }] }) } as Response;
      }
      if (url === "/api/artifacts/large-input/links") {
        links += 1;
        if (links === 1) {
          if (failure === "lost acknowledgment") throw new Error("Link unavailable");
          return new Response(JSON.stringify({ detail: { code: failure, message: "Link unavailable" } }), {
            status: failure === "auth_required" ? 401 : 403,
          });
        }
        return { ok: true } as Response;
      }
      return original(input, init);
    });
    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Large input. ".repeat(10000) } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText(/Workflow was started.*Link unavailable/);
    expect(screen.getByRole("link", { name: "Open accepted result" }).getAttribute("href")).toBe("/workflows/mm%3Aaccepted?source=temporal");
    expect(navigateTo).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/workflows/mm%3Aaccepted?source=temporal"));
    expect(JSON.stringify(window.sessionStorage)).not.toMatch(/PRIVATE_INSTRUCTIONS|PRIVATE_PARAMETERS|PRIVATE_SECRET|task_instructions|input_parameters/);
    expect(creates).toBe(1);
    expect(artifacts).toBe(1);
    expect(links).toBe(failure === "lost acknowledgment" ? 1 : 2);
  });

  it("retains the originally admitted deferred time on a lost-ack retry", async () => {
    const original = fetchSpy.getMockImplementation()!;
    const bodies: Array<{ payload: { task: { idempotencyKey: string }; schedule: { scheduledFor: string } } }> = [];
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/executions" && init?.method === "POST") {
        bodies.push(JSON.parse(String(init.body)));
        throw new Error(`Lost deferred response ${bodies.length}`);
      }
      return original(input, init);
    });
    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Do this later." } });
    fireEvent.change(screen.getByLabelText("Schedule Mode"), { target: { value: "deferred_minutes" } });
    fireEvent.change(screen.getByLabelText("Minutes from now"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText("Lost deferred response 1");
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText("Lost deferred response 2");
    expect(bodies[1]?.payload.task.idempotencyKey).toBe(bodies[0]?.payload.task.idempotencyKey);
    expect(bodies[1]?.payload.schedule.scheduledFor).toBe(bodies[0]?.payload.schedule.scheduledFor);
  });

  it.each(["unavailable", "misconfigured"])("retries recurring creation after an operator-admission %s rejection", async (code) => {
    const original = fetchSpy.getMockImplementation()!;
    let creates = 0;
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/executions" && init?.method === "POST") {
        creates += 1;
        return creates === 1
          ? { ok: false, status: 503, text: async () => JSON.stringify({ detail: { code, message: "Operator ingress needs repair." } }) } as Response
          : { ok: true, json: async () => ({ definitionId: "schedule-after-repair" }) } as Response;
      }
      return original(input, init);
    });
    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Do this daily." } });
    fireEvent.change(screen.getByLabelText("Schedule Mode"), { target: { value: "recurring" } });
    fireEvent.change(screen.getByLabelText("Cron Expression"), { target: { value: "0 8 * * *" } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText(/Operator ingress needs repair/);
    expect(screen.queryByText(/Schedule creation outcome is unknown/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await waitFor(() => expect(creates).toBe(2));
    await waitFor(() => expect(navigateTo).toHaveBeenCalledExactlyOnceWith("/schedules/schedule-after-repair"));
  });

  it("does not blindly retry an uncertain recurring definition without a replay contract", async () => {
    const original = fetchSpy.getMockImplementation()!;
    let creates = 0;
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/executions" && init?.method === "POST") {
        creates += 1;
        throw new Error("Acknowledgment lost");
      }
      return original(input, init);
    });
    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Do this daily." } });
    fireEvent.change(screen.getByLabelText("Schedule Mode"), { target: { value: "recurring" } });
    fireEvent.change(screen.getByLabelText("Cron Expression"), { target: { value: "0 8 * * *" } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText(/Schedule creation outcome is unknown/);
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText(/Schedule creation outcome is unknown/);
    expect(creates).toBe(1);
  });

  it("keeps a newer route when the first Create settles late", async () => {
    const original = fetchSpy.getMockImplementation()!;
    let finish!: (response: Response) => void;
    let navigate!: NavigateFunction;
    function CaptureNavigation() { navigate = useNavigate(); return null; }
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/executions" && init?.method === "POST") {
        return new Promise<Response>((resolve) => { finish = resolve; });
      }
      return original(input, init);
    });
    renderWithClient(<MemoryRouter initialEntries={["/workflows/new"]}><CaptureNavigation /><WorkflowStartPage payload={payloadWith()} /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "First draft." } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await waitFor(() => expect(finish).toBeDefined());
    await act(async () => navigate("/workflows/new"));
    await act(async () => finish({ ok: true, json: async () => ({ workflowId: "mm:old-draft" }) } as Response));
    expect(navigateTo).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: "Open accepted result" })).toBeNull();
  });

  it("reconciles a lost acknowledgment after the absolute scheduled time passes", async () => {
    const original = fetchSpy.getMockImplementation()!;
    const keys: string[] = [];
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/executions" && init?.method === "POST") {
        keys.push(JSON.parse(String(init.body)).payload.task.idempotencyKey);
        if (keys.length === 1) throw new Error("Lost scheduled response");
        return { ok: true, json: async () => ({ workflowId: "mm:scheduled" }) } as Response;
      }
      return original(input, init);
    });
    vi.useFakeTimers({ toFake: ["Date"] });
    try {
      vi.setSystemTime(new Date("2030-01-01T08:00:00"));
      renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
      fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Do this at nine." } });
      fireEvent.change(screen.getByLabelText("Schedule Mode"), { target: { value: "once" } });
      fireEvent.change(screen.getByLabelText("Scheduled For"), { target: { value: "2030-01-01T09:00" } });
      fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
      await screen.findByText("Lost scheduled response");
      vi.setSystemTime(new Date("2030-01-01T10:00:00"));
      fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
      await waitFor(() => expect(keys).toHaveLength(2));
      expect(keys[1]).toBe(keys[0]);
    } finally { vi.useRealTimers(); }
  });

  it("reuses uploaded attachment refs when the Create acknowledgment is lost", async () => {
    const original = fetchSpy.getMockImplementation()!;
    let artifacts = 0;
    const bodies: Array<{ payload: { task: { idempotencyKey: string; inputAttachments?: unknown[]; steps?: unknown[] } } }> = [];
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/artifacts" && init?.method === "POST") {
        artifacts += 1;
        return { ok: true, json: async () => ({ artifact_ref: { artifact_id: `attachment-${artifacts}` } }) } as Response;
      }
      if (url === "/api/executions" && init?.method === "POST") {
        bodies.push(JSON.parse(String(init.body)));
        throw new Error(`Lost attachment response ${bodies.length}`);
      }
      return original(input, init);
    });
    const first = renderWithClient(<WorkflowStartPage payload={payloadWith({ attachments: true })} />);
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Use the attached design." } });
    const picker = document.querySelector('input[type="file"]')!;
    fireEvent.change(picker, { target: { files: [new File(["image"], "design.png", { type: "image/png" })] } });
    await screen.findByText("design.png");
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText("Lost attachment response 1");
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText("Lost attachment response 2");
    expect(artifacts).toBe(1);
    expect(bodies[1]).toEqual(bodies[0]);
    first.unmount();
    renderWithClient(<WorkflowStartPage payload={payloadWith({ attachments: true })} />);
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Use the attached design." } });
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(["image"], "design.png", { type: "image/png" })] } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText("Lost attachment response 3");
    expect(artifacts).toBe(1);
    expect(bodies[2]).toEqual(bodies[0]);
  });

  it.each(["Step 1 image file picker", "Step 2 image file picker"])("coalesces identical files into one valid reference for %s across lost-ack retries", async (pickerLabel) => {
    const original = fetchSpy.getMockImplementation()!;
    let artifacts = 0;
    const bodies: unknown[] = [];
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/artifacts" && init?.method === "POST") {
        const id = `duplicate-content-${++artifacts}`;
        await new Promise((resolve) => setTimeout(resolve, 20));
        return { ok: true, json: async () => ({ artifact_ref: { artifact_id: id } }) } as Response;
      }
      if (String(input) === "/api/executions" && init?.method === "POST") {
        bodies.push(JSON.parse(String(init.body)));
        throw new Error(`Lost duplicate-content response ${bodies.length}`);
      }
      return original(input, init);
    });
    renderWithClient(<WorkflowStartPage payload={payloadWith({ attachments: true })} />);
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Use both selected files." } });
    if (pickerLabel.startsWith("Step 2")) {
      fireEvent.click(screen.getByRole("button", { name: "Add Step" }));
      fireEvent.change(within(stepSection(2)).getByLabelText("Instructions"), { target: { value: "Use the files in this step." } });
    }
    fireEvent.change(screen.getByLabelText(pickerLabel), { target: { files: [100, 200].map((lastModified) => new File(["same content"], "same.png", { type: "image/png", lastModified })) } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    const task = (bodies[0] as { payload: { task: { inputAttachments?: Array<{ artifactId: string }>; steps?: Array<{ inputAttachments?: Array<{ artifactId: string }> }> } } }).payload.task;
    const refs = [...(task.inputAttachments || []), ...(task.steps || []).flatMap((step) => step.inputAttachments || [])];
    // _validate_and_collect_task_input_attachments rejects repeated artifact IDs
    // globally, even when both declarations belong to the same target.
    expect(new Set(refs.map((ref) => ref.artifactId)).size).toBe(refs.length);
    expect(refs).toHaveLength(1);
    await screen.findByText("Lost duplicate-content response 1");
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText("Lost duplicate-content response 2");
    expect(bodies[1]).toEqual(bodies[0]);
    expect(artifacts).toBe(1);
  });

  it("keeps identical uploaded contents attached to each selected step with distinct artifact IDs", async () => {
    const original = fetchSpy.getMockImplementation()!;
    let artifacts = 0;
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/artifacts" && init?.method === "POST") {
        return { ok: true, json: async () => ({ artifact_ref: { artifact_id: `scoped-${++artifacts}` } }) } as Response;
      }
      return original(input, init);
    });
    renderWithClient(<WorkflowStartPage payload={payloadWith({ attachments: true })} />);
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Use this image in the first step." } });
    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));
    fireEvent.change(within(stepSection(2)).getByLabelText("Instructions"), { target: { value: "Use this image in the second step." } });
    for (const number of [1, 2]) {
      fireEvent.change(screen.getByLabelText(`Step ${number} image file picker`), {
        target: { files: [new File(["same content"], "same.png", { type: "image/png", lastModified: 100 })] },
      });
    }
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await waitFor(() => expect(createRequests()).toHaveLength(1));
    const task = (createRequests()[0]!.payload as { task: { steps: Array<{ inputAttachments: Array<{ artifactId: string }> }> } }).task;
    expect(task.steps.map((step) => step.inputAttachments)).toEqual([
      [expect.objectContaining({ artifactId: "scoped-1", filename: "same.png" })],
      [expect.objectContaining({ artifactId: "scoped-2", filename: "same.png" })],
    ]);
    expect(artifacts).toBe(2);
  });

  it.each(["objective", "primary", "additional"])("deduplicates persisted and uploaded refs within the %s target", async (target) => {
    const attachment = { artifactId: "persisted-attachment", filename: "same.png", contentType: "image/png", sizeBytes: 12 };
    const record = recordedExecution("mm:attachment-dedup");
    const workflow = (record.inputParameters as { workflow: Record<string, unknown> }).workflow;
    workflow.steps = [
      { instructions: "First recorded step.", ...(target === "primary" ? { inputAttachments: [attachment, attachment] } : {}) },
      { instructions: "Second recorded step.", ...(target === "additional" ? { inputAttachments: [attachment, attachment] } : {}) },
    ];
    if (target === "objective") workflow.inputAttachments = [attachment, attachment];
    executions["mm:attachment-dedup"] = record;
    const original = fetchSpy.getMockImplementation()!;
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/artifacts" && init?.method === "POST") {
        return { ok: true, json: async () => ({ artifact_ref: { artifact_id: attachment.artifactId } }) } as Response;
      }
      return original(input, init);
    });
    window.history.pushState({}, "Workflow Edit", "/workflows/new?editExecutionId=mm%3Aattachment-dedup");
    renderWithClient(<WorkflowStartPage payload={payloadWith({ attachments: true })} />);
    await screen.findByDisplayValue("Second recorded step.");
    if (target !== "objective") {
      fireEvent.change(screen.getByLabelText(`Step ${target === "primary" ? 1 : 2} image file picker`), {
        target: { files: [new File(["same content"], "same.png", { type: "image/png" })] },
      });
    }
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    await waitFor(() => expect(updates).toHaveLength(1));
    const parameters = updates[0]!.body.parametersPatch as { task?: Record<string, unknown>; workflow?: Record<string, unknown> };
    const submitted = parameters.workflow || parameters.task!;
    const refs = target === "objective" ? submitted.inputAttachments :
      (submitted.steps as Array<Record<string, unknown>>)[target === "primary" ? 0 : 1]!.inputAttachments;
    expect(refs).toEqual([attachment]);
  });

  it.each(["objective and step", "two steps"])("rejects a persisted artifact assigned to %s without dropping either association", async (targets) => {
    const attachment = { artifactId: "shared-attachment", filename: "shared.png", contentType: "image/png", sizeBytes: 12 };
    const record = recordedExecution("mm:attachment-conflict");
    const workflow = (record.inputParameters as { workflow: Record<string, unknown> }).workflow;
    workflow.steps = [
      { instructions: "First recorded step.", inputAttachments: [attachment] },
      { instructions: "Second recorded step.", ...(targets === "two steps" ? { inputAttachments: [attachment] } : {}) },
    ];
    if (targets === "objective and step") workflow.inputAttachments = [attachment];
    executions["mm:attachment-conflict"] = record;
    window.history.pushState({}, "Workflow Edit", "/workflows/new?editExecutionId=mm%3Aattachment-conflict");
    renderWithClient(<WorkflowStartPage payload={payloadWith({ attachments: true })} />);
    await screen.findByDisplayValue("Second recorded step.");
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    await screen.findByText(/shared.png is assigned to both/);
    expect(updates).toHaveLength(0);
    expect(createRequests()).toHaveLength(0);
    expect(screen.getAllByRole("button", { name: /Remove .*attachment shared.png/ })).toHaveLength(2);
  });

  it("rejects a fresh past schedule without uploading selected attachments", async () => {
    const original = fetchSpy.getMockImplementation()!;
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/artifacts" && init?.method === "POST") return { ok: true, json: async () => ({ artifact_ref: { artifact_id: "past-orphan" } }) } as Response;
      return original(input, init);
    });
    renderWithClient(<WorkflowStartPage payload={payloadWith({ attachments: true })} />);
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Past request." } });
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(["image"], "design.png", { type: "image/png" })] } });
    fireEvent.change(screen.getByLabelText("Schedule Mode"), { target: { value: "once" } });
    fireEvent.change(screen.getByLabelText("Scheduled For"), { target: { value: "2000-01-01T09:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findAllByText(/Scheduled time must be a valid future date/);
    expect(fetchSpy.mock.calls.filter(([url, init]) => String(url) === "/api/artifacts" && init?.method === "POST")).toHaveLength(0);
  });

  it.each([[503, "temporal_submit_disabled"], [503, "unknown_gateway_error"], [422, "invalid_recurring_workflow"], [401, "auth_required"], [403, "inactive"]] as const)("handles recurring %i %s according to admission certainty", async (status, code) => {
    const original = fetchSpy.getMockImplementation()!;
    let creates = 0;
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/executions" && init?.method === "POST") {
        creates += 1;
        if (creates === 1) return { ok: false, status, text: async () => JSON.stringify({ detail: { code, message: code === "invalid_recurring_workflow" ? "Failed to create schedule: timed out waiting for acknowledgment" : "Submission temporarily unavailable" } }) } as Response;
        return { ok: true, json: async () => ({ definitionId: "schedule-retried" }) } as Response;
      }
      return original(input, init);
    });
    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Do this daily." } });
    fireEvent.change(screen.getByLabelText("Schedule Mode"), { target: { value: "recurring" } });
    fireEvent.change(screen.getByLabelText("Cron Expression"), { target: { value: "0 8 * * *" } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText(/Submission temporarily unavailable|Failed to create schedule/);
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    if (code === "temporal_submit_disabled" || code === "auth_required" || code === "inactive") {
      await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/schedules/schedule-retried"));
      expect(creates).toBe(2);
    } else {
      await screen.findByText(/Schedule creation outcome is unknown/);
      expect(creates).toBe(1);
    }
  });

  it("reconstructs uploaded attachment refs after reload but separates changed contents", async () => {
    const original = fetchSpy.getMockImplementation()!;
    let artifacts = 0;
    let creates = 0;
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/artifacts" && init?.method === "POST") {
        artifacts += 1;
        return { ok: true, json: async () => ({ artifact_ref: { artifact_id: `reload-attachment-${artifacts}` } }) } as Response;
      }
      if (String(input) === "/api/executions" && init?.method === "POST") {
        creates += 1;
        return { ok: true, json: async () => ({ workflowId: `mm:accepted-${creates}` }) } as Response;
      }
      return original(input, init);
    });
    vi.mocked(navigateTo).mockImplementationOnce(() => { throw new Error("Navigation interrupted"); });
    async function reconstruct(content: string) {
      const view = renderWithClient(<WorkflowStartPage payload={payloadWith({ attachments: true })} />);
      fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Reconstructed draft." } });
      fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File([content], "design.png", { type: "image/png", lastModified: 100 })] } });
      fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
      return view;
    }
    const first = await reconstruct("PRIVATE_FILE_ALPHA");
    await screen.findByText(/Workflow was started.*Navigation interrupted/);
    first.unmount();
    const second = await reconstruct("PRIVATE_FILE_ALPHA");
    await waitFor(() => expect(navigateTo).toHaveBeenCalledTimes(2));
    expect(artifacts).toBe(1);
    expect(creates).toBe(1);
    second.unmount();
    await reconstruct("PRIVATE_FILE_BRAVO");
    await waitFor(() => expect(navigateTo).toHaveBeenCalledTimes(3));
    expect(artifacts).toBe(2);
    expect(creates).toBe(2);
    expect(JSON.stringify(window.sessionStorage)).not.toMatch(/PRIVATE_FILE|Reconstructed draft/);
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

    it("preserves Create retry identity when Back is declined", async () => {
      const original = fetchSpy.getMockImplementation()!;
      const keys: string[] = [];
      fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input) === "/api/executions" && init?.method === "POST") {
          keys.push(JSON.parse(String(init.body)).payload.task.idempotencyKey);
          throw new Error(`Lost history response ${keys.length}`);
        }
        return original(input, init);
      });
      window.history.pushState({ key: "previous-entry" }, "", "/workflows");
      window.history.pushState({ key: "create-entry" }, "", "/workflows/new");
      renderWithClient(<BrowserRouter><WorkflowStartPage payload={payloadWith()} /></BrowserRouter>);
      fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Keep this submitted draft." } });
      fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
      await screen.findByText("Lost history response 1");
      const originalState = window.history.state;
      act(() => window.history.back());
      await waitFor(() => expect(confirmSpy).toHaveBeenCalled());
      await waitFor(() => expect(window.location.pathname).toBe("/workflows/new"));
      expect(window.history.state).toEqual(originalState);
      fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
      await screen.findByText("Lost history response 2");
      expect(keys[1]).toBe(keys[0]);
    });

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

  it("preserves recurring uncertainty through late oversized preparation and reload", async () => {
    const original = fetchSpy.getMockImplementation()!;
    let finishOld!: (value: Response) => void;
    let artifactCreates = 0;
    let creates = 0;
    const artifactResponse = (id: string) => ({ ok: true, json: async () => ({
      artifact_ref: { artifact_id: id }, upload: { upload_url: `/api/artifacts/${id}/content` },
    }) } as Response);
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/artifacts" && init?.method === "POST") {
        artifactCreates += 1;
        if (artifactCreates === 1) return new Promise<Response>((resolve) => { finishOld = resolve; });
        return artifactResponse("canonical-input");
      }
      if (url === "/api/executions" && init?.method === "POST") {
        creates += 1;
        throw new Error("Lost recurring acknowledgment");
      }
      return original(input, init);
    });
    const submitSameDraft = async () => {
      fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Large recurring input. ".repeat(10000) } });
      fireEvent.change(screen.getByLabelText("Schedule Mode"), { target: { value: "recurring" } });
      fireEvent.change(screen.getByLabelText("Cron Expression"), { target: { value: "0 8 * * *" } });
      fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    };
    const old = renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
    await submitSameDraft();
    await waitFor(() => expect(finishOld).toBeDefined());
    old.unmount();
    const current = renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
    await submitSameDraft();
    await screen.findByText(/Schedule creation outcome is unknown/);
    expect(creates).toBe(1);
    await act(async () => finishOld(artifactResponse("late-orphan")));
    await waitFor(() => expect(fetchSpy.mock.calls.some(([url]) => String(url) === "/api/artifacts/late-orphan/complete")).toBe(true));
    current.unmount();
    renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
    await submitSameDraft();
    await screen.findByText(/Schedule creation outcome is unknown/);
    expect(creates).toBe(1);
    expect(artifactCreates).toBe(2);
    expect(navigateTo).not.toHaveBeenCalled();
  });


  it.each([[5, 503, "temporal_submit_disabled"], [15, 503, "temporal_submit_disabled"], [15, 422, "schedule_in_past"]] as const)("refreshes a definitively rejected relative delay after %i minutes (%i %s)", async (elapsed, status, code) => {
    vi.useFakeTimers({ toFake: ["Date"] });
    try {
      vi.setSystemTime(new Date("2030-01-01T09:00:00Z"));
      const original = fetchSpy.getMockImplementation()!;
      const bodies: Array<{ payload: { task: { idempotencyKey: string }; schedule: { scheduledFor: string } } }> = [];
      fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input) === "/api/executions" && init?.method === "POST") {
          bodies.push(JSON.parse(String(init.body)));
          if (bodies.length === 1) return { ok: false, status, text: async () => JSON.stringify({ detail: { code, message: "Submission disabled" } }) } as Response;
          return { ok: true, json: async () => ({ workflowId: "mm:delayed" }) } as Response;
        }
        return original(input, init);
      });
      renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
      fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Do this later." } });
      fireEvent.change(screen.getByLabelText("Schedule Mode"), { target: { value: "deferred_minutes" } });
      fireEvent.change(screen.getByLabelText("Minutes from now"), { target: { value: "10" } });
      fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
      await screen.findByText("Submission disabled");
      vi.setSystemTime(new Date(Date.parse("2030-01-01T09:00:00Z") + elapsed * 60000));
      fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
      await waitFor(() => expect(navigateTo).toHaveBeenCalled());
      expect(bodies[1]?.payload.task.idempotencyKey).toBe(bodies[0]?.payload.task.idempotencyKey);
      expect(bodies[1]?.payload.schedule.scheduledFor).toBe(new Date(Date.now() + 10 * 60000).toISOString());
    } finally { vi.useRealTimers(); }
  });

  it.each(["recurring", "deferred_minutes"])("records an owned late rejection after leaving a %s draft", async (mode) => {
    vi.useFakeTimers({ toFake: ["Date"] });
    try {
      vi.setSystemTime(new Date("2030-01-01T09:00:00Z"));
      const original = fetchSpy.getMockImplementation()!;
      let finish!: (response: Response) => void;
      let creates = 0;
      let retryBody: { payload: { schedule?: { scheduledFor?: string } } } | undefined;
      fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input) === "/api/executions" && init?.method === "POST") {
          creates += 1;
          if (creates === 1) return new Promise<Response>((resolve) => { finish = resolve; });
          retryBody = JSON.parse(String(init.body));
          return { ok: true, json: async () => ({ workflowId: "mm:retried", definitionId: mode === "recurring" ? "schedule-retried" : undefined }) } as Response;
        }
        return original(input, init);
      });
      const submit = async () => {
        fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Do this later." } });
        fireEvent.change(screen.getByLabelText("Schedule Mode"), { target: { value: mode } });
        fireEvent.change(screen.getByLabelText(mode === "recurring" ? "Cron Expression" : "Minutes from now"), { target: { value: mode === "recurring" ? "0 8 * * *" : "10" } });
        fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
      };
      const old = renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
      await submit();
      await waitFor(() => expect(finish).toBeDefined());
      old.unmount();
      await act(async () => finish({ ok: false, status: 503, text: async () => JSON.stringify({ detail: { code: "temporal_submit_disabled", message: "Submission disabled" } }) } as Response));
      expect(navigateTo).not.toHaveBeenCalled();
      vi.setSystemTime(new Date("2030-01-01T09:15:00Z"));
      renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
      await submit();
      await waitFor(() => expect(navigateTo).toHaveBeenCalled());
      expect(creates).toBe(2);
      if (mode === "deferred_minutes") expect(retryBody?.payload.schedule?.scheduledFor).toBe("2030-01-01T09:25:00.000Z");
    } finally { vi.useRealTimers(); }
  });


  it.each(["invalid_execution_request", "unknown_gateway_error"])("retains unknown original timing through an auth-rejected replay after %s", async (code) => {
    vi.useFakeTimers({ toFake: ["Date"] });
    try {
      vi.setSystemTime(new Date("2030-01-01T09:00:00Z"));
      const original = fetchSpy.getMockImplementation()!;
      const bodies: Array<{ payload: { task: { idempotencyKey: string }; schedule: { scheduledFor: string } } }> = [];
      fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input) === "/api/executions" && init?.method === "POST") {
          bodies.push(JSON.parse(String(init.body)));
          if (bodies.length === 1) return { ok: false, status: code === "invalid_execution_request" ? 422 : 503, text: async () => JSON.stringify({ detail: { code, message: "Original admission unconfirmed" } }) } as Response;
          if (bodies.length === 2) return { ok: false, status: 401, text: async () => JSON.stringify({ detail: { code: "auth_required", message: "Sign in again" } }) } as Response;
          return { ok: true, json: async () => ({ workflowId: "mm:original" }) } as Response;
        }
        return original(input, init);
      });
      renderWithClient(<WorkflowStartPage payload={payloadWith()} />);
      fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Do this later." } });
      fireEvent.change(screen.getByLabelText("Schedule Mode"), { target: { value: "deferred_minutes" } });
      fireEvent.change(screen.getByLabelText("Minutes from now"), { target: { value: "10" } });
      fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
      await screen.findByText("Original admission unconfirmed");
      vi.setSystemTime(new Date("2030-01-01T09:15:00Z"));
      fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
      await screen.findByText("Sign in again");
      vi.setSystemTime(new Date("2030-01-01T09:30:00Z"));
      fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
      await waitFor(() => expect(navigateTo).toHaveBeenCalled());
      expect(bodies).toHaveLength(3);
      expect(new Set(bodies.map((body) => body.payload.task.idempotencyKey)).size).toBe(1);
      expect(new Set(bodies.map((body) => body.payload.schedule.scheduledFor))).toEqual(new Set(["2030-01-01T09:10:00.000Z"]));
    } finally { vi.useRealTimers(); }
  });

});
