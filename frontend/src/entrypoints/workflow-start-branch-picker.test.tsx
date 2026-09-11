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
  configuredBranchLookupUrl,
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

// 25 server names on purpose: the picker must cap mounted options at 20 even
// though the fixture advertises more pages via hasMore.
const SERVER_BRANCHES = [
  "main",
  ...Array.from({ length: 24 }, (_, index) => `feature/${String(index).padStart(2, "0")}`),
];

function branchListPayload() {
  return {
    items: SERVER_BRANCHES.map((value) => ({
      value,
      label: value,
      source: "github",
    })),
    defaultBranch: "main",
    hasMore: true,
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

  beforeEach(() => {
    window.history.pushState({}, "Task Create", "/workflows/new");
    window.sessionStorage.clear();
    window.localStorage.clear();
    branchRequestUrls = [];
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
          const parsed = new URL(url, "http://localhost");
          const branch = String(parsed.searchParams.get("branch") || "");
          return Promise.resolve({
            ok: true,
            json: async () => resolvePayload(branch),
          } as Response);
        }
        if (url.startsWith("/api/github/branches/metadata")) {
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

  it("preserves pasted text and never reports a stale-list error for an exact branch outside the first page", async () => {
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

    // The older branch resolves directly even though it is absent from the
    // first bounded suggestion page: no false stale warning appears.
    await waitFor(() => {
      expect(
        screen.queryByText(/not in the latest list for this repository/),
      ).toBeNull();
    });
    await waitFor(
      () => {
        expect(branchRequestUrls.length).toBeGreaterThan(0);
      },
      { timeout: 5000 },
    );
    expect(branchInput.value).toBe("feature/old-branch");
    expect(branchInput.disabled).toBe(false);
  });

  it("caps mounted suggestions at 20 and says the list is partial", async () => {
    renderWithClient(<WorkflowStartPage payload={mockPayload} />);

    const branchInput = (await screen.findByLabelText("Branch", {
      selector: "input",
    })) as HTMLInputElement;
    fireEvent.change(branchInput, { target: { value: "feature" } });

    await waitFor(
      () => {
        expect(
          screen.getByText(/Showing the first 20 suggestions/),
        ).toBeTruthy();
      },
      { timeout: 5000 },
    );
    const options = document.querySelectorAll(
      '#queue-branch-options option',
    );
    expect(options.length).toBeLessThanOrEqual(
      BRANCH_TEXT_FIRST_LIMITS.suggestionLimit,
    );
    // One bounded page per search: the client never drains remaining pages.
    const suggestionCalls = branchRequestUrls.filter((url) =>
      url.startsWith("/api/github/branches?"),
    );
    expect(suggestionCalls.length).toBeGreaterThan(0);
    for (const url of suggestionCalls) {
      const parsed = new URL(url, "http://localhost");
      expect(Number(parsed.searchParams.get("limit"))).toBeLessThanOrEqual(50);
    }
  });

  it("keeps manual input editable and intact while suggestions fail", async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/github/branches/metadata")) {
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

    await waitFor(
      () => {
        expect(
          screen.getByText(/Branch suggestions are unavailable/),
        ).toBeTruthy();
      },
      { timeout: 5000 },
    );
    // Lookup availability is reported separately from input validity: the
    // authored value stays intact and the control stays enabled.
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
  });
});

describe("branch picker helpers", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("builds bounded suggestion urls with encoded queries", () => {
    const url = configuredBranchLookupUrl(
      "/api/github/branches?repository={repository}",
      "Octo/Repo",
      { query: "feature/foo bar", limit: 999 },
    );
    const parsed = new URL(url, "http://localhost");
    expect(parsed.searchParams.get("repository")).toBe("Octo/Repo");
    expect(parsed.searchParams.get("q")).toBe("feature/foo bar");
    expect(parsed.searchParams.get("limit")).toBe("20");
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
