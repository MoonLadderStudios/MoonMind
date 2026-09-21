import { fireEvent, screen, waitFor } from "@testing-library/react";
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
import {
  BRANCH_TEXT_FIRST_LIMITS,
  WorkflowStartPage,
  branchRecentHistoryKey,
  configuredBranchResolveUrl,
  readRecentBranches,
  writeRecentBranch,
} from "./workflow-start";

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
          branchResolve:
            "/api/github/branches/resolve?repository={repository}&branch={branch}",
          branchMetadata:
            "/api/github/branches/metadata?repository={repository}",
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
          enabled: false,
          templateSaveEnabled: false,
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

// Canary payload: branch enumeration must never run, so any request to the
// legacy list route is recorded and asserted as zero in the tests below.
function branchListPayload() {
  return {
    items: [{ value: "main", label: "main", source: "github" }],
    defaultBranch: "main",
    hasMore: false,
    error: null,
  };
}

function resolvePayload(branch: string) {
  if (branch === "main" || branch === "feature/old-branch") {
    return { found: true, branch, defaultBranch: "main", error: null, inconclusive: false };
  }
  if (branch === "missing-nope") {
    return { found: false, branch: null, defaultBranch: "main", error: null, inconclusive: false };
  }
  return { found: false, branch: null, defaultBranch: "main", error: null, inconclusive: true };
}

describe("MoonLadderStudios/MoonMind#4054 text-first branch picker", () => {
  let fetchSpy: MockInstance;
  let branchRequestUrls: string[];
  let resolveRequestUrls: string[];
  let metadataRequestUrls: string[];

  beforeEach(() => {
    window.history.pushState({}, "Task Create", "/workflows/new");
    window.sessionStorage.clear();
    window.localStorage.clear();
    branchRequestUrls = [];
    resolveRequestUrls = [];
    metadataRequestUrls = [];
    fetchSpy = vi
      .spyOn(window, "fetch")
      .mockImplementation((input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        if (url.startsWith("/api/workflows/skills")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ items: { worker: ["moonspec-orchestrate"] } }),
          } as Response);
        }
        if (url.startsWith("/api/github/branches/resolve")) {
          resolveRequestUrls.push(url);
          const parsed = new URL(url, "http://localhost");
          const branch = String(parsed.searchParams.get("branch") || "");
          return Promise.resolve({
            ok: true,
            json: async () => resolvePayload(branch),
          } as Response);
        }
        if (url.startsWith("/api/github/branches/metadata")) {
          metadataRequestUrls.push(url);
          return Promise.resolve({
            ok: true,
            json: async () => ({ defaultBranch: "main", error: null }),
          } as Response);
        }
        if (url.startsWith("/api/github/branches")) {
          branchRequestUrls.push(url);
          return Promise.resolve({
            ok: true,
            json: async () => branchListPayload(),
          } as Response);
        }
        if (url.startsWith("/api/v1/provider-profiles")) {
          return Promise.resolve({ ok: true, json: async () => [] } as Response);
        }
        if (url.startsWith("/api/presets")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ items: [] }),
          } as Response);
        }
        return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
      });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("opens the form with metadata only and never enumerates branches", async () => {
    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    await screen.findByLabelText("Branch", { selector: "input" });
    await waitFor(
      () => {
        expect(metadataRequestUrls.length).toBeGreaterThan(0);
      },
      { timeout: 5000 },
    );
    // Opening Create (or changing repository) fetches only default-branch
    // metadata. There is no broader branch enumeration in the page.
    expect(branchRequestUrls.length).toBe(0);
  });

  it("preserves pasted text with at most one resolve and zero enumeration fetches", async () => {
    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    const branchInput = (await screen.findByLabelText("Branch", {
      selector: "input",
    })) as HTMLInputElement;
    fireEvent.change(branchInput, { target: { value: "feature/old-branch" } });

    // Authored text is authoritative immediately, before any lookup finishes.
    expect(branchInput.value).toBe("feature/old-branch");
    expect(
      screen.queryByText(/not in the latest list for this repository/),
    ).toBeNull();

    // One settled paste schedules at most one exact lookup and never branch
    // enumeration alongside it.
    await waitFor(
      () => {
        expect(resolveRequestUrls.length).toBe(1);
      },
      { timeout: 5000 },
    );
    expect(branchRequestUrls.length).toBe(0);
    expect(branchInput.value).toBe("feature/old-branch");
    expect(branchInput.disabled).toBe(false);
    expect(
      screen.queryByText(/not in the latest list for this repository/),
    ).toBeNull();
  });

  it("stays responsive while branch endpoints hang and keeps the latest text", async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (
        url.startsWith("/api/github/branches/resolve") ||
        url.startsWith("/api/github/branches/metadata") ||
        (url.startsWith("/api/github/branches") && !url.startsWith("/api/workflows"))
      ) {
        return new Promise(() => {});
      }
      if (url.startsWith("/api/workflows/skills")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: { worker: ["moonspec-orchestrate"] } }),
        } as Response);
      }
      if (url.startsWith("/api/v1/provider-profiles")) {
        return Promise.resolve({ ok: true, json: async () => [] } as Response);
      }
      if (url.startsWith("/api/presets")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [] }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });

    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    const branchInput = (await screen.findByLabelText("Branch", {
      selector: "input",
    })) as HTMLInputElement;
    // Paste, replace selected text, and type again while lookups hang: every
    // edit must appear synchronously without waiting for the network.
    fireEvent.change(branchInput, { target: { value: "feature/pasted-name" } });
    expect(branchInput.value).toBe("feature/pasted-name");
    fireEvent.change(branchInput, { target: { value: "feature/replaced" } });
    expect(branchInput.value).toBe("feature/replaced");
    fireEvent.change(branchInput, { target: { value: "feature/replaced-2" } });
    expect(branchInput.value).toBe("feature/replaced-2");
    expect(branchInput.disabled).toBe(false);
  });

  it("offers local default and recent suggestions with zero enumeration and no search control", async () => {
    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    const branchInput = (await screen.findByLabelText("Branch", {
      selector: "input",
    })) as HTMLInputElement;
    fireEvent.change(branchInput, { target: { value: "feature" } });
    // Local typing alone never fetches suggestions and there is no broader
    // discovery button in the floating bar.
    expect(branchInput.value).toBe("feature");
    expect(
      screen.queryByRole("button", { name: /Search other branches/i }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: /Hide search/i }),
    ).toBeNull();

    await waitFor(
      () => {
        expect(metadataRequestUrls.length).toBeGreaterThan(0);
      },
      { timeout: 5000 },
    );
    expect(branchRequestUrls.length).toBe(0);
    const options = document.querySelectorAll(
      '#queue-branch-options option',
    );
    expect(options.length).toBeLessThanOrEqual(
      BRANCH_TEXT_FIRST_LIMITS.suggestionLimit,
    );
  });

  it("keeps manual input editable and intact when the branch list endpoint fails", async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/github/branches/metadata")) {
        metadataRequestUrls.push(url);
        return Promise.resolve({
          ok: true,
          json: async () => ({ defaultBranch: "main", error: null }),
        } as Response);
      }
      if (
        url.startsWith("/api/github/branches/resolve") ||
        url.startsWith("/api/workflows/skills") ||
        url.startsWith("/api/presets")
      ) {
        return Promise.resolve({
          ok: true,
          json: async () => ({}),
        } as Response);
      }
      if (url.startsWith("/api/v1/provider-profiles")) {
        return Promise.resolve({
          ok: true,
          json: async () => [],
        } as Response);
      }
      if (url.startsWith("/api/github/branches")) {
        branchRequestUrls.push(url);
        return Promise.resolve({
          ok: false,
          status: 500,
          text: async () => "upstream blew up",
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });

    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    const branchInput = (await screen.findByLabelText("Branch", {
      selector: "input",
    })) as HTMLInputElement;
    fireEvent.change(branchInput, { target: { value: "feature/typed-during-outage" } });

    // Typing never triggers branch enumeration, so a failing list endpoint
    // leaves the authored value intact without a suggestion error.
    expect(branchInput.value).toBe("feature/typed-during-outage");
    expect(
      screen.queryByText(/Branch suggestions are unavailable/),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: /Search other branches/i }),
    ).toBeNull();
    expect(branchRequestUrls.length).toBe(0);
    // The authored value stays intact and the control stays enabled.
    expect(branchInput.value).toBe("feature/typed-during-outage");
    expect(branchInput.disabled).toBe(false);
    expect(
      screen.queryByText(/not in the latest list for this repository/),
    ).toBeNull();
  });

  it("reports a definitively absent branch as information without blocking authoring", async () => {
    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    const branchInput = (await screen.findByLabelText("Branch", {
      selector: "input",
    })) as HTMLInputElement;
    fireEvent.change(branchInput, { target: { value: "missing-nope" } });

    await waitFor(
      () => {
        expect(
          screen.getByText(/No branch named "missing-nope" was found/),
        ).toBeTruthy();
      },
      { timeout: 5000 },
    );
    expect(branchInput.value).toBe("missing-nope");
    expect(branchInput.disabled).toBe(false);
    // A full-name paste needs exact resolution only, never enumeration.
    expect(branchRequestUrls.length).toBe(0);
  });

  it("submits the live branch draft when Start is pressed within the settle debounce", async () => {
    // P1: typing then immediately submitting must use the live draft ref, not
    // the render-time settled value. Clearing then submitting must not reuse
    // the prior settled branch.
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/executions" && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ workflowId: "mm:live-branch" }),
        } as Response);
      }
      if (url.startsWith("/api/workflows/skills")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: { worker: ["moonspec-orchestrate"] } }),
        } as Response);
      }
      if (url.startsWith("/api/github/branches/resolve")) {
        resolveRequestUrls.push(url);
        const parsed = new URL(url, "http://localhost");
        const branch = String(parsed.searchParams.get("branch") || "");
        return Promise.resolve({
          ok: true,
          json: async () => resolvePayload(branch),
        } as Response);
      }
      if (url.startsWith("/api/github/branches/metadata")) {
        metadataRequestUrls.push(url);
        return Promise.resolve({
          ok: true,
          json: async () => ({ defaultBranch: "main", error: null }),
        } as Response);
      }
      if (url.startsWith("/api/github/branches")) {
        branchRequestUrls.push(url);
        return Promise.resolve({
          ok: true,
          json: async () => branchListPayload(),
        } as Response);
      }
      if (url.startsWith("/api/v1/provider-profiles")) {
        return Promise.resolve({ ok: true, json: async () => [] } as Response);
      }
      if (url.startsWith("/api/presets")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [] }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });

    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    const branchInput = (await screen.findByLabelText("Branch", {
      selector: "input",
    })) as HTMLInputElement;
    const instructions = (await screen.findByLabelText("Instructions")) as HTMLTextAreaElement;
    fireEvent.change(instructions, { target: { value: "Verify live branch submission." } });
    // Type a new branch and submit immediately, before the 600ms settled
    // commit fires. The request must carry the typed value.
    fireEvent.change(branchInput, { target: { value: "feature/live-draft-branch" } });
    fireEvent.click(screen.getByRole("button", { name: "Start Workflow" }));

    await waitFor(
      () => {
        const call = fetchSpy.mock.calls.find(
          ([url, init]) =>
            String(url) === "/api/executions" && (init as RequestInit)?.method === "POST",
        );
        expect(call).toBeTruthy();
      },
      { timeout: 5000 },
    );
    const createCall = fetchSpy.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/executions" && (init as RequestInit)?.method === "POST",
    );
    const request = JSON.parse(String((createCall?.[1] as RequestInit)?.body));
    expect(request.payload.repository.branch.name).toBe("feature/live-draft-branch");
  });
});

describe("branch picker helpers", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("encodes slash-containing branch names in resolve urls", () => {
    const url = configuredBranchResolveUrl(
      "/api/github/branches/resolve?repository={repository}&branch={branch}",
      "Octo/Repo",
      "feature/foo",
    );
    expect(url).toContain(`branch=${encodeURIComponent("feature/foo")}`);
  });

  it("keeps user-scoped recent history small, deduplicated, and case-preserving", () => {
    writeRecentBranch("Octo/Repo", "Feature/Foo");
    writeRecentBranch("Octo/Repo", "main");
    writeRecentBranch("Octo/Repo", "Feature/Foo");
    expect(readRecentBranches("Octo/Repo")).toEqual(["Feature/Foo", "main"]);
    expect(readRecentBranches("octo/repo")).toEqual(["Feature/Foo", "main"]);
    expect(readRecentBranches("Other/Repo")).toEqual([]);
    expect(branchRecentHistoryKey("Octo/Repo")).toBe(
      branchRecentHistoryKey("octo/repo"),
    );
  });

  it("bounds recent history growth", () => {
    for (let index = 0; index < 30; index += 1) {
      writeRecentBranch("Octo/Repo", `branch-${index}`);
    }
    expect(readRecentBranches("Octo/Repo")).toHaveLength(
      BRANCH_TEXT_FIRST_LIMITS.recentHistoryMax,
    );
  });
});
