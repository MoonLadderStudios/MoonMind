import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useNavigate, type NavigateFunction } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BootPayload } from "../boot/parseBootPayload";
import { navigateTo } from "../lib/navigation";
import { renderWithClient } from "../utils/test-utils";
import { WorkflowStartPage } from "./workflow-start";

vi.mock("../lib/navigation", () => ({ navigateTo: vi.fn() }));

const payload: BootPayload = {
  page: "workflow-start",
  apiBase: "/api",
  initialData: {
    dashboardConfig: {
      sources: { github: { branches: "/api/github/branches?repository={repository}" } },
      system: {
        defaultRepository: "MoonLadderStudios/MoonMind",
        defaultAgentRuntime: "codex_cli",
        defaultTaskModel: "gpt-5.4",
        defaultTaskEffort: "medium",
        defaultPublishMode: "pr",
        defaultTaskModelByRuntime: { codex_cli: "gpt-5.4" },
        defaultTaskEffortByRuntime: { codex_cli: "medium" },
        supportedAgentRuntimes: ["codex_cli"],
        providerProfiles: { list: "/api/v1/provider-profiles" },
      },
      features: { temporalDashboard: { temporalTaskEditing: true } },
    },
  },
};

function recordedExecution(workflowId: string) {
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
        instructions: `Instructions for ${workflowId}.`,
        runtime: { mode: "codex_cli" },
        git: { startingBranch: "main" },
        publish: { mode: "pr" },
      },
    },
    actions: { canUpdateInputs: true, canRerun: true, canEditForRerun: true },
  };
}

function response(body: unknown): Response {
  return { ok: true, json: async () => body } as Response;
}

const modes = [
  { label: "edit", parameter: "editExecutionId", button: "Save Changes", update: "UpdateInputs" },
  { label: "rerun", parameter: "rerunExecutionId", button: "Start New Run", update: "RequestRerun" },
];

describe("Edit and rerun submission route ownership", () => {
  let updates: Array<{ body: { updateName: string }; finish: (result: Response) => void; fail: (error: Error) => void }>;

  beforeEach(() => {
    window.history.replaceState({}, "Workflow", "/workflows/new");
    window.sessionStorage.clear();
    window.localStorage.clear();
    vi.mocked(navigateTo).mockReset();
    updates = [];
    vi.spyOn(window, "fetch").mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/workflows/skills")) return Promise.resolve(response({ items: { worker: [] } }));
      if (url.startsWith("/api/github/branches")) {
        return Promise.resolve(response({ items: [{ value: "main", label: "main", source: "github" }], defaultBranch: "main", error: null }));
      }
      if (url.startsWith("/api/v1/provider-profiles")) {
        return Promise.resolve(response([{ profile_id: "profile:codex-default", runtime_id: "codex_cli", account_label: "Codex Default", is_default: true }]));
      }
      const detail = url.match(/^\/api\/executions\/([^/?]+)\?source=temporal$/);
      if (detail?.[1]) return Promise.resolve(response(recordedExecution(decodeURIComponent(detail[1]))));
      if (/^\/api\/executions\/[^/?]+\/update$/.test(url)) {
        return new Promise<Response>((finish, fail) => {
          updates.push({ body: JSON.parse(String(init?.body)), finish, fail });
        });
      }
      return Promise.resolve(response({}));
    });
  });

  afterEach(() => vi.restoreAllMocks());

  function updateAt(index: number) {
    const update = updates[index];
    if (!update) throw new Error(`Update request ${index + 1} is missing.`);
    return update;
  }

  function renderMode(parameter: string) {
    let navigate!: NavigateFunction;
    function CaptureNavigation() { navigate = useNavigate(); return null; }
    const initial = `/workflows/new?${parameter}=mm%3Afirst`;
    renderWithClient(
      <MemoryRouter initialEntries={[initial]}>
        <CaptureNavigation />
        <WorkflowStartPage payload={payload} />
      </MemoryRouter>,
    );
    return { navigate: (to: string | number) => typeof to === "number" ? navigate(to) : navigate(to), initial };
  }

  async function submitLoaded(button: string, workflowId: string, count: number) {
    await waitFor(() => expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe(`Instructions for ${workflowId}.`));
    fireEvent.click(screen.getByRole("button", { name: button }));
    await waitFor(() => expect(updates).toHaveLength(count));
  }

  it.each(modes)("ignores a late $label success without unlocking the newer request", async ({ parameter, button, update }) => {
    const { navigate } = renderMode(parameter);
    await submitLoaded(button, "mm:first", 1);
    expect(updateAt(0).body.updateName).toBe(update);
    await act(async () => navigate(`/workflows/new?${parameter}=mm%3Asecond`));
    await submitLoaded(button, "mm:second", 2);

    await act(async () => updateAt(0).finish(response({ accepted: true, applied: "continue_as_new", execution: { workflowId: "mm:old-result" } })));
    expect(navigateTo).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem("moonmind.temporalTaskEditing.notice")).toBeNull();
    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe("Instructions for mm:second.");
    expect(screen.getByRole("button", { name: button }).getAttribute("aria-disabled")).toBe("true");

    await act(async () => updateAt(1).finish(response({ accepted: true, execution: { workflowId: "mm:new-result" } })));
    expect(navigateTo).toHaveBeenCalledExactlyOnceWith("/workflows/mm%3Anew-result?source=temporal");
  });

  it.each(modes)("ignores a late $label failure without replacing the newer draft status", async ({ parameter, button }) => {
    const { navigate } = renderMode(parameter);
    await submitLoaded(button, "mm:first", 1);
    await act(async () => navigate(`/workflows/new?${parameter}=mm%3Asecond`));
    await submitLoaded(button, "mm:second", 2);

    await act(async () => updateAt(0).fail(new Error("Failure from the old route.")));
    expect(screen.queryByText("Failure from the old route.")).toBeNull();
    expect(navigateTo).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: button }).getAttribute("aria-disabled")).toBe("true");

    await act(async () => updateAt(1).fail(new Error("Current request failed.")));
    expect(await screen.findByText("Current request failed.")).toBeTruthy();
    expect(screen.getByRole("button", { name: button }).getAttribute("aria-disabled")).toBe("false");
  });

  it.each(modes)("does not send a stale $label update after input artifact preparation", async ({ parameter, button }) => {
    const original = vi.mocked(window.fetch).getMockImplementation()!;
    let finishArtifact!: (result: Response) => void;
    vi.mocked(window.fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/artifacts" && init?.method === "POST") {
        return new Promise<Response>((resolve) => { finishArtifact = resolve; });
      }
      return original(input, init);
    });
    const { navigate } = renderMode(parameter);
    await waitFor(() => expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe("Instructions for mm:first."));
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Large edited input. ".repeat(10000) } });
    fireEvent.click(screen.getByRole("button", { name: button }));
    await waitFor(() => expect(finishArtifact).toBeDefined());
    await act(async () => navigate(`/workflows/new?${parameter}=mm%3Asecond`));
    await waitFor(() => expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe("Instructions for mm:second."));

    await act(async () => finishArtifact(response({ artifact_ref: { artifact_id: "old-input" } })));
    expect(updates).toHaveLength(0);
    expect(navigateTo).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: button }).getAttribute("aria-disabled")).toBe("false");
  });

  it.each(modes)("does not revive a stale $label request after returning to its URL", async ({ parameter, button }) => {
    const { navigate } = renderMode(parameter);
    await submitLoaded(button, "mm:first", 1);
    await act(async () => navigate("/workflows/new"));
    await act(async () => navigate(-1));
    await waitFor(() => expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe("Instructions for mm:first."));

    await act(async () => updateAt(0).finish(response({ accepted: true, execution: { workflowId: "mm:old-result" } })));
    expect(navigateTo).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem("moonmind.temporalTaskEditing.notice")).toBeNull();
    expect(screen.getByRole("button", { name: button }).getAttribute("aria-disabled")).toBe("false");
  });
});
