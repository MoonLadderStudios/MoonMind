import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BootPayload } from "../boot/parseBootPayload";
import { navigateTo } from "../lib/navigation";
import { requestWorkflowStartRouteChange } from "../lib/workflowStartRouteGuard";
import { renderWithClient } from "../utils/test-utils";
import { WorkflowStartPage } from "./workflow-start";

vi.mock("../lib/navigation", () => ({ navigateTo: vi.fn() }));

const payload: BootPayload = {
  page: "workflow-start",
  apiBase: "/api",
  initialData: {
    dashboardConfig: {
      sources: { temporal: { create: "/api/executions" } },
      system: {
        defaultAgentRuntime: "codex_cli",
        defaultTaskModel: "gpt-5.4",
        defaultTaskEffort: "medium",
        defaultPublishMode: "none",
        supportedAgentRuntimes: ["codex_cli"],
        providerProfiles: { list: "/api/v1/provider-profiles" },
        presetCatalog: {
          enabled: true, list: "/api/presets", detail: "/api/presets/{slug}",
          expand: "/api/presets/{slug}:expand",
        },
        attachmentPolicy: {
          enabled: true, maxCount: 4, maxBytes: 1024 * 1024,
          totalBytes: 2 * 1024 * 1024, allowedContentTypes: ["image/png"],
        },
      },
    },
  },
};

function response(body: unknown): Response {
  return { ok: true, json: async () => body } as Response;
}

function stepSection(): HTMLElement {
  return screen.getByText("Step 1", { selector: "strong" }).closest("section") as HTMLElement;
}

function dropFile(file: File) {
  fireEvent.drop(stepSection(), {
    dataTransfer: {
      types: ["Files"], files: [file],
      items: [{ kind: "file", type: file.type, getAsFile: () => file }],
    },
  });
}

describe("Create authored mutations outside native change events", () => {
  let finishCreate: ((result: Response) => void) | undefined;
  let failCreate: ((error: Error) => void) | undefined;
  let expansions: Array<(result: Response) => void>;

  beforeEach(() => {
    window.history.replaceState({}, "Workflow Create", "/workflows/new");
    window.sessionStorage.clear();
    window.localStorage.clear();
    vi.mocked(navigateTo).mockReset();
    finishCreate = undefined;
    failCreate = undefined;
    expansions = [];
    vi.spyOn(window, "fetch").mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/workflows/skills")) {
        return Promise.resolve(response({ items: { worker: ["moonspec-orchestrate"] } }));
      }
      if (url.startsWith("/api/v1/provider-profiles")) {
        return Promise.resolve(response([{
          profile_id: "profile:codex-default", runtime_id: "codex_cli",
          account_label: "Codex Default", is_default: true,
        }]));
      }
      if (url === "/api/executions" && init?.method === "POST") {
        return new Promise<Response>((resolve, reject) => {
          finishCreate = resolve;
          failCreate = reject;
        });
      }
      if (url === "/api/presets") {
        return Promise.resolve(response({ items: [{ slug: "example", scope: "global", title: "Example" }] }));
      }
      if (url.startsWith("/api/presets/example?")) {
        return Promise.resolve(response({ slug: "example", scope: "global", title: "Example", inputs: [] }));
      }
      if (url.startsWith("/api/presets/example:expand?")) {
        return new Promise<Response>((resolve) => expansions.push(resolve));
      }
      return Promise.resolve(response({}));
    });
  });

  afterEach(() => vi.restoreAllMocks());

  async function submitDraft() {
    renderWithClient(<WorkflowStartPage payload={payload} />);
    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Submitted intent." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await waitFor(() => expect(finishCreate).toBeDefined());
  }

  async function finishOldCreate() {
    await act(async () => finishCreate!(response({ workflowId: "mm:old-intent" })));
  }

  it("keeps a file dropped while Create is pending instead of following the old acknowledgment", async () => {
    await submitDraft();
    dropFile(new File(["new image"], "new-draft.png", { type: "image/png" }));
    expect(within(stepSection()).getByText("new-draft.png")).toBeTruthy();

    await finishOldCreate();

    expect(navigateTo).not.toHaveBeenCalled();
    expect(within(stepSection()).getByText("new-draft.png")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start Workflow" }).getAttribute("aria-disabled")).toBe("false");
  });

  it("does not show an old Create failure over a draft with a newly dropped file", async () => {
    await submitDraft();
    dropFile(new File(["new image"], "new-draft.png", { type: "image/png" }));

    await act(async () => failCreate!(new Error("Old request failed.")));

    expect(screen.queryByText("Old request failed.")).toBeNull();
    expect(within(stepSection()).getByText("new-draft.png")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start Workflow" }).getAttribute("aria-disabled")).toBe("false");
  });

  it("does not admit the old draft when a file is dropped during input artifact preparation", async () => {
    const original = vi.mocked(window.fetch).getMockImplementation()!;
    let finishArtifact: ((result: Response) => void) | undefined;
    vi.mocked(window.fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/artifacts" && init?.method === "POST") {
        return new Promise<Response>((resolve) => { finishArtifact = resolve; });
      }
      return original(input, init);
    });
    renderWithClient(<WorkflowStartPage payload={payload} />);
    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Large submitted input. ".repeat(1000) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await waitFor(() => expect(finishArtifact).toBeDefined());
    dropFile(new File(["new image"], "new-draft.png", { type: "image/png" }));

    await act(async () => finishArtifact!(response({ artifact_ref: { artifact_id: "old-input" } })));

    expect(finishCreate).toBeUndefined();
    expect(navigateTo).not.toHaveBeenCalled();
    expect(within(stepSection()).getByText("new-draft.png")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start Workflow" }).getAttribute("aria-disabled")).toBe("false");
  });

  it.each(["pointer", "keyboard"])("keeps a skill selected by %s while Create is pending", async (method) => {
    await submitDraft();
    const skill = screen.getByRole("combobox", { name: "Step 1 skill" });
    if (method === "pointer") {
      fireEvent.pointerDown(skill);
      fireEvent.pointerDown(screen.getByRole("option", { name: "moonspec-orchestrate" }));
    } else {
      fireEvent.keyDown(skill, { key: "ArrowDown" });
      fireEvent.keyDown(skill, { key: "ArrowDown" });
      fireEvent.keyDown(skill, { key: "Enter" });
    }
    expect((skill as HTMLInputElement).value).toBe("moonspec-orchestrate");

    await finishOldCreate();

    expect(navigateTo).not.toHaveBeenCalled();
    expect((skill as HTMLInputElement).value).toBe("moonspec-orchestrate");
  });

  it("protects a draft whose only authored change was a dropped file", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithClient(<WorkflowStartPage payload={payload} />);
    await screen.findByText("Step 1");
    dropFile(new File(["new image"], "new-draft.png", { type: "image/png" }));

    expect(requestWorkflowStartRouteChange("/workflows")).toBe(false);
    expect(confirm).toHaveBeenCalledOnce();
    expect(within(stepSection()).getByText("new-draft.png")).toBeTruthy();
  });

  it("keeps a manual preset expansion that completes while Create is pending", async () => {
    renderWithClient(<WorkflowStartPage payload={payload} />);
    fireEvent.click(within(stepSection()).getByRole("radio", { name: "Preset" }));
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Expand this intent." } });
    const preset = screen.getByLabelText("Preset Template") as HTMLSelectElement;
    await waitFor(() => expect(preset.options.length).toBeGreaterThan(1));
    fireEvent.change(preset, { target: { value: "global::::example" } });
    fireEvent.click(screen.getByRole("button", { name: "Expand" }));
    await waitFor(() => expect(expansions).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await waitFor(() => expect(expansions).toHaveLength(2));
    await act(async () => expansions[1]!(response({
      steps: [{ instructions: "Submitted expansion." }], warnings: [],
    })));
    await waitFor(() => expect(finishCreate).toBeDefined());

    await act(async () => expansions[0]!(response({
      steps: [{ instructions: "Keep the manual expansion." }], warnings: [],
    })));
    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe("Keep the manual expansion.");
    await finishOldCreate();

    expect(navigateTo).not.toHaveBeenCalled();
    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe("Keep the manual expansion.");
  });

  it("does not invalidate an unchanged draft for a rejected drop", async () => {
    await submitDraft();
    dropFile(new File(["not an image"], "notes.txt", { type: "text/plain" }));
    expect(await screen.findByText("Unsupported file type for notes.txt.")).toBeTruthy();

    await finishOldCreate();

    expect(navigateTo).toHaveBeenCalledExactlyOnceWith("/workflows/mm%3Aold-intent?source=temporal");
  });
});
