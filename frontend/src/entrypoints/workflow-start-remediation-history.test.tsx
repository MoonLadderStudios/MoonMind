import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BootPayload } from "../boot/parseBootPayload";
import { createSubmissionScope } from "../lib/createSubmissionIntent";
import { navigateTo } from "../lib/navigation";
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
