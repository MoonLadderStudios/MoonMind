import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BootPayload } from "../boot/parseBootPayload";
import { CreateSubmissionIntents, createSubmissionScope } from "../lib/createSubmissionIntent";
import { navigateTo } from "../lib/navigation";
import { buildRemediationCreateDraft, readRemediationCreateDraft, storeRemediationCreateDraft } from "../lib/remediationCreateDraft";
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
        defaultPublishMode: "none",
        supportedAgentRuntimes: ["codex_cli"],
        attachmentPolicy: { enabled: true, maxCount: 4, maxBytes: 1024 * 1024, totalBytes: 2 * 1024 * 1024, allowedContentTypes: ["image/png", "application/pdf"] },
        providerProfiles: { list: "/api/v1/provider-profiles" },
      },
    },
  },
};

function response(body: unknown): Response {
  return { ok: true, json: async () => body } as Response;
}

function renderCreate() {
  return renderWithClient(<BrowserRouter><WorkflowStartPage payload={payload} /></BrowserRouter>);
}

describe("Create history identity after discarding remediation", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
    window.history.replaceState(
      { key: "remediation-entry", idx: 3, usr: { returnTo: "/workflows" }, moonmindDashboard: true },
      "",
      "/workflows/new?intent=remediate&draftId=repair&source=temporal#draft",
    );
    vi.mocked(navigateTo).mockReset();
    vi.spyOn(window, "fetch").mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/workflows/skills")) {
        return Promise.resolve(response({ items: { worker: [] } }));
      }
      if (url.startsWith("/api/v1/provider-profiles")) {
        return Promise.resolve(response([{
          profile_id: "profile:codex-default", runtime_id: "codex_cli",
          account_label: "Codex Default", is_default: true,
        }]));
      }
      if (url.startsWith("/api/executions/mm%3Atarget")) {
        return Promise.resolve(response({ workflowId: "mm:target", runId: "target-run" }));
      }
      return Promise.resolve(response({}));
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it("refuses an unreconstructable authored request before hydrating the form", async () => {
    const draft = buildRemediationCreateDraft({
      workflowId: "mm:target", runId: "target-run", repository: "MoonLadderStudios/MoonMind",
      targetRuntime: "codex_cli", profileId: "profile:codex-default",
    });
    draft.authoring = {
      request: { type: "task", priority: 0, maxAttempts: 3, payload: { targetRuntime: "codex_cli", task: { instructions: "", remediation: draft.remediation } } },
      publishMode: "none", schedule: { mode: "immediate", scheduledFor: "", deferredMinutes: "", cron: "", timezone: "UTC", name: "" },
    };
    const draftId = storeRemediationCreateDraft(draft);
    window.history.replaceState(window.history.state, "", `/workflows/new?intent=remediate&draftId=${draftId}`);
    renderCreate();
    await screen.findByRole("button", { name: "Discard draft reference" });
    expect(screen.queryByRole("button", { name: "Discard remediation draft" })).toBeNull();
    expect((screen.getByLabelText(/^Repository$/) as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe("");
  });

  it.each(["instructions", "dependency", "attachment"])("submits an operator's %s edit after restoring an uncertain request", async (editedField) => {
    const draft = buildRemediationCreateDraft({
      workflowId: "mm:target", runId: "target-run", targetRuntime: "codex_cli", profileId: "profile:codex-default",
    });
    const draftId = storeRemediationCreateDraft(draft);
    window.history.replaceState(window.history.state, "", `/workflows/new?intent=remediate&draftId=${draftId}`);
    const originalFetch = vi.mocked(window.fetch).getMockImplementation()!;
    const requests: Array<{ payload: { task: { instructions: string; dependsOn?: string[]; steps?: Array<{ inputAttachments?: unknown[] }>; idempotencyKey: string } } }> = [];
    vi.mocked(window.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/executions?")) return response({ items: [{ workflowId: "mm:prerequisite", title: "Prerequisite" }] });
      if (url === "/api/artifacts" && init?.method === "POST") {
        return response({ artifact_ref: { artifact_id: "art-edited" }, upload: { upload_url: "/upload/art-edited" } });
      }
      if (url === "/api/executions" && init?.method === "POST") {
        requests.push(JSON.parse(String(init.body)));
        if (requests.length === 1) throw new Error("Create acknowledgment lost.");
        return response({ workflowId: "mm:repair" });
      }
      return originalFetch(input, init);
    });

    const first = renderCreate();
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Original authored repair." } });
    await screen.findByRole("option", { name: "Prerequisite" });
    fireEvent.change(screen.getByLabelText("Prerequisite"), { target: { value: "mm:prerequisite" } });
    fireEvent.change(screen.getByLabelText("Step 1 attachment file picker"), {
      target: { files: [new File(["image"], "repair.png", { type: "image/png" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText("Create acknowledgment lost.");
    first.unmount();

    renderCreate();
    await screen.findByRole("button", { name: "Discard remediation draft" });
    if (editedField === "instructions") {
      fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Revised authored repair." } });
    } else if (editedField === "dependency") {
      fireEvent.click(screen.getByRole("button", { name: "Remove prerequisite mm:prerequisite" }));
      expect(screen.queryByRole("button", { name: "Remove prerequisite mm:prerequisite" })).toBeNull();
    } else {
      fireEvent.click(screen.getByRole("button", { name: "Remove Step 1 attachment repair.png" }));
      expect(screen.queryByText("repair.png")).toBeNull();
    }
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/workflows/mm%3Arepair?source=temporal"));
    expect(requests).toHaveLength(2);
    expect(requests[1]!.payload.task.idempotencyKey).not.toBe(requests[0]!.payload.task.idempotencyKey);
    expect(requests[1]!.payload.task.instructions).toBe(editedField === "instructions" ? "Revised authored repair." : "Original authored repair.");
    expect(requests[1]!.payload.task.dependsOn).toEqual(editedField === "dependency" ? undefined : ["mm:prerequisite"]);
    expect(requests[1]!.payload.task.steps?.[0]?.inputAttachments?.length || 0).toBe(editedField === "attachment" ? 0 : 1);
  });

  it("retains exact submitted metadata when only changing the form's visibility", async () => {
    const draft = buildRemediationCreateDraft({
      workflowId: "mm:target", runId: "target-run", targetRuntime: "codex_cli", profileId: "profile:codex-default",
    });
    draft.instructions = "Retain authored preset metadata.";
    const request = {
      type: "task", priority: 0, maxAttempts: 3,
      payload: { targetRuntime: "codex_cli", publishMode: "none", task: {
        instructions: draft.instructions, remediation: draft.remediation,
        runtime: { mode: "codex_cli", profileId: "profile:codex-default" },
        steps: [{ id: "repair-step", instructions: draft.instructions, skill: { id: "remediate-workflow" } }],
        appliedStepTemplates: [{ slug: "repair-source", inputs: {}, stepIds: ["repair-step"], appliedAt: draft.createdAt,
          capabilities: [], presetDigest: "sha256:retained", composition: { source: "authored" } }],
      } },
    };
    draft.authoring = { request, publishMode: "none", schedule: { mode: "immediate", scheduledFor: "", deferredMinutes: "", cron: "", timezone: "UTC", name: "" } };
    const draftId = storeRemediationCreateDraft(draft);
    window.history.replaceState(window.history.state, "", `/workflows/new?intent=remediate&draftId=${draftId}`);
    const intents = new CreateSubmissionIntents(`${createSubmissionScope()}:/workflows/new:${window.history.state.key}`);
    const intent = intents.forRequest(request);
    intents.beginSubmission(intent, false);
    const originalFetch = vi.mocked(window.fetch).getMockImplementation()!;
    const requests: unknown[] = [];
    vi.mocked(window.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) !== "/api/executions" || init?.method !== "POST") return originalFetch(input, init);
      requests.push(JSON.parse(String(init.body)));
      return response({ workflowId: "mm:repair" });
    });

    renderCreate();
    await screen.findByRole("button", { name: "Discard remediation draft" });
    fireEvent.click(screen.getByLabelText("Advanced mode"));
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/workflows/mm%3Arepair?source=temporal"));
    expect(requests).toEqual([{ ...request, payload: { ...request.payload, task: { ...request.payload.task, idempotencyKey: intent.key } } }]);
  });

  it.each(["create", "artifact link"])("retains uploaded inputs after a lost %s acknowledgment", async (failurePoint) => {
    const draft = buildRemediationCreateDraft({
      workflowId: "mm:target", runId: "target-run", targetRuntime: "codex_cli", profileId: "profile:codex-default",
    });
    const draftId = storeRemediationCreateDraft(draft);
    window.history.replaceState(window.history.state, "", `/workflows/new?intent=remediate&draftId=${draftId}`);
    const originalFetch = vi.mocked(window.fetch).getMockImplementation()!;
    const creates: unknown[] = [];
    const links: unknown[] = [];
    let uploads = 0;
    vi.mocked(window.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/artifacts" && init?.method === "POST") {
        uploads += 1;
        return response({ artifact_ref: { artifact_id: "art-edited" }, upload: { upload_url: "/upload/art-edited" } });
      }
      if (url === "/api/artifacts/art-edited/links" && init?.method === "POST") {
        links.push(JSON.parse(String(init.body)));
        if (failurePoint === "artifact link") throw new Error("Artifact link acknowledgment lost.");
      }
      if (url === "/api/artifacts/art-edited" && !init?.method) return response({ links });
      if (url === "/api/executions" && init?.method === "POST") {
        creates.push(JSON.parse(String(init.body)));
        if (failurePoint === "create" && creates.length === 1) throw new Error("Create acknowledgment lost.");
        return response({ workflowId: "mm:repair", runId: "repair-run", namespace: "default" });
      }
      return originalFetch(input, init);
    });

    const first = renderCreate();
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Repair using this uploaded input." } });
    fireEvent.change(screen.getByLabelText("Step 1 attachment file picker"), {
      target: { files: [new File(["image"], "repair.png", { type: "image/png" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText(failurePoint === "create" ? /Create acknowledgment lost/ : /Artifact link acknowledgment lost/);
    first.unmount();

    renderCreate();
    await screen.findByRole("button", { name: "Discard remediation draft" });
    expect(screen.getByText("repair.png")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/workflows/mm%3Arepair?source=temporal"));
    expect(uploads).toBe(1);
    expect(creates).toHaveLength(failurePoint === "create" ? 2 : 1);
    if (failurePoint === "create") expect(creates[1]).toEqual(creates[0]);
    expect(creates[0]).toMatchObject({ payload: { task: { steps: [{ inputAttachments: [{ artifactId: "art-edited", filename: "repair.png" }] }] } } });
    // Admission owns attachment links. Restored refs need neither a new upload
    // nor another browser bookkeeping POST after the acknowledged admission.
    expect(links).toHaveLength(failurePoint === "create" ? 0 : 1);
    expect(readRemediationCreateDraft(draftId)).toBeNull();
  });

  it("stops before admission when authored remediation cannot be retained", async () => {
    const draft = buildRemediationCreateDraft({
      workflowId: "mm:target", runId: "target-run", targetRuntime: "codex_cli", profileId: "profile:codex-default",
    });
    const draftId = storeRemediationCreateDraft(draft);
    window.history.replaceState(window.history.state, "", `/workflows/new?intent=remediate&draftId=${draftId}`);
    const setItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key === `moonmind.remediation-create-draft.${draftId}`) throw new Error("Storage full");
      return setItem.call(this, key, value);
    });
    renderCreate();
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Keep this repair durable before admission." } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText("The remediation draft could not be saved. Submission was not sent.");
    expect(vi.mocked(window.fetch).mock.calls.filter(([url, init]) => String(url) === "/api/executions" && init?.method === "POST")).toHaveLength(0);
    expect(readRemediationCreateDraft(draftId)).toEqual(draft);
  });

  it.each([null, "immediate", "deferred_minutes"])("retains remediation across remount and admission uncertainty (%s)", async (schedule) => {
    const loseAcknowledgment = schedule !== null;
    const draft = buildRemediationCreateDraft({
      workflowId: "mm:target", runId: "target-run", targetRuntime: "codex_cli",
      profileId: "profile:codex-default",
    });
    const draftId = storeRemediationCreateDraft(draft);
    window.history.replaceState(window.history.state, "", `/workflows/new?intent=remediate&draftId=${draftId}`);
    const originalFetch = vi.mocked(window.fetch).getMockImplementation()!;
    const requests: unknown[] = [];
    vi.mocked(window.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) !== "/api/executions" || init?.method !== "POST") return originalFetch(input, init);
      requests.push(JSON.parse(String(init.body)));
      if (loseAcknowledgment && requests.length === 1) throw new Error("Create acknowledgment lost.");
      return response({ workflowId: "mm:repair" });
    });

    const first = renderCreate();
    await screen.findByRole("button", { name: "Discard remediation draft" });
    const submittedInstructions = loseAcknowledgment ? "Preserve the edited repair before retrying." : draft.instructions;
    if (loseAcknowledgment) {
      fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: submittedInstructions } });
      fireEvent.change(screen.getByLabelText("Authority"), { target: { value: "observe_only" } });
      fireEvent.click(screen.getByLabelText("Diagnostics"));
      fireEvent.change(screen.getByLabelText(/^Repository$/), { target: { value: "MoonLadderStudios/MoonMind" } });
      fireEvent.change(screen.getByLabelText("Branch"), { target: { value: "repair/edited" } });
      fireEvent.change(screen.getByLabelText("Publish Mode"), { target: { value: "branch" } });
      fireEvent.click(screen.getByLabelText("Advanced mode"));
      fireEvent.change(screen.getByLabelText("Workflow Model"), { target: { value: "gpt-6.1-sol" } });
      fireEvent.change(screen.getByLabelText("Priority"), { target: { value: "7" } });
      fireEvent.change(screen.getByLabelText("Max Attempts"), { target: { value: "5" } });
      if (schedule === "deferred_minutes") {
        fireEvent.change(screen.getByLabelText("Schedule Mode"), { target: { value: schedule } });
        fireEvent.change(screen.getByLabelText("Minutes from now"), { target: { value: "5" } });
      }
      fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
      await screen.findByText("Create acknowledgment lost.");
    }
    first.unmount();
    if (schedule === "deferred_minutes") vi.spyOn(Date, "now").mockReturnValue(Date.now() + 10 * 60_000);
    renderCreate();

    await screen.findByRole("button", { name: "Discard remediation draft" });
    expect(screen.queryByText(/This remediation draft is missing/)).toBeNull();
    expect((screen.getByLabelText("Pinned run") as HTMLInputElement).value).toBe("target-run");
    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe(submittedInstructions);
    if (loseAcknowledgment) {
      expect((screen.getByLabelText("Authority") as HTMLSelectElement).value).toBe("observe_only");
      expect((screen.getByLabelText("Diagnostics") as HTMLInputElement).checked).toBe(false);
      expect((screen.getByLabelText(/^Repository$/) as HTMLInputElement).value).toBe("MoonLadderStudios/MoonMind");
      expect((screen.getByLabelText("Branch") as HTMLInputElement).value).toBe("repair/edited");
      expect((screen.getByLabelText("Publish Mode") as HTMLSelectElement).value).toBe("branch");
      expect((screen.getByLabelText("Workflow Model") as HTMLInputElement).value).toBe("gpt-6.1-sol");
      expect((screen.getByLabelText("Priority") as HTMLInputElement).value).toBe("7");
      expect((screen.getByLabelText("Max Attempts") as HTMLInputElement).value).toBe("5");
      if (schedule === "deferred_minutes") {
        expect((screen.getByLabelText("Schedule Mode") as HTMLSelectElement).value).toBe(schedule);
        expect((screen.getByLabelText("Minutes from now") as HTMLInputElement).value).toBe("5");
      }
    }
    expect(requests).toHaveLength(loseAcknowledgment ? 1 : 0);
    expect(readRemediationCreateDraft(draftId)).toMatchObject({ target: draft.target });

    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/workflows/mm%3Arepair?source=temporal"));
    expect(requests).toHaveLength(loseAcknowledgment ? 2 : 1);
    if (loseAcknowledgment) expect(requests[1]).toEqual(requests[0]);
    expect(requests[0]).toMatchObject({ payload: { task: {
      instructions: submittedInstructions,
      remediation: { target: draft.remediation.target, authorityMode: loseAcknowledgment ? "observe_only" : draft.remediation.authorityMode },
    } } });
    expect(readRemediationCreateDraft(draftId)).toBeNull();
  });

  it.each([
    ["imported draft", "Discard remediation draft"],
    ["missing draft reference", "Discard draft reference"],
  ])("replays the same admission after discarding the %s and reloading", async (kind, buttonName) => {
    if (kind === "imported draft") {
      window.sessionStorage.setItem("moonmind.remediation-create-draft.repair", JSON.stringify({
        source: "remediation", schemaVersion: 1, createdAt: new Date().toISOString(),
        target: { workflowId: "mm:target", runId: "target-run" },
        repository: "",
        remediation: {
          target: { workflowId: "mm:target", runId: "target-run" },
          mode: "snapshot", authorityMode: "approval_gated", trigger: { type: "manual" },
        },
      }));
    }
    const originalFetch = vi.mocked(window.fetch).getMockImplementation()!;
    const requests: Array<{ payload: { task: { idempotencyKey: string } } }> = [];
    const admittedKeys = new Set<string>();
    vi.mocked(window.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) !== "/api/executions" || init?.method !== "POST") {
        return originalFetch(input, init);
      }
      const body = JSON.parse(String(init.body));
      requests.push(body);
      admittedKeys.add(body.payload.task.idempotencyKey);
      if (requests.length === 1) throw new Error("Create acknowledgment lost.");
      return response({ workflowId: "mm:accepted" });
    });

    const first = renderCreate();
    const discard = await screen.findByRole("button", { name: buttonName });
    const originalState = window.history.state;
    expect(originalState.moonmindCreateIntent).toEqual(expect.any(String));
    fireEvent.click(discard);
    // Discard only removes the remediation URL parameters, not the browser
    // entry's Create identity, router key/index, or unrelated user state.
    expect.soft(window.history.state).toEqual(originalState);
    expect(window.location.pathname + window.location.search + window.location.hash)
      .toBe("/workflows/new?source=temporal#draft");

    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Ordinary workflow." } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await screen.findByText("Create acknowledgment lost.");
    expect(requests).toHaveLength(1);
    first.unmount();

    // A reload reconstructs the router and Create store from this entry. The
    // operator re-enters the same draft; request bodies must match exactly.
    renderCreate();
    fireEvent.change(await screen.findByLabelText("Instructions"), { target: { value: "Ordinary workflow." } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/workflows/mm%3Aaccepted?source=temporal"));
    expect(requests).toHaveLength(2);
    expect(requests[1]).toEqual(requests[0]);
    expect(admittedKeys.size).toBe(1);
    expect(window.history.state).toEqual(originalState);
  });

  it("preserves same-entry replacement state without copying identity into a fresh Create entry", async () => {
    const { navigateTo: navigate } = await vi.importActual<typeof import("../lib/navigation")>("../lib/navigation");
    const scope = createSubmissionScope();
    const originalState = window.history.state;
    navigate("/workflows/new?source=temporal", { replace: true });
    expect(window.history.state).toEqual(originalState);
    expect(createSubmissionScope()).toBe(scope);

    navigate("/workflows");
    navigate("/workflows/new");
    expect(createSubmissionScope()).not.toBe(scope);

    await act(async () => window.history.go(-2));
    await waitFor(() => expect(window.location.search).toBe("?source=temporal"));
    expect(window.history.state).toEqual(originalState);
    expect(createSubmissionScope()).toBe(scope);
  });
});
