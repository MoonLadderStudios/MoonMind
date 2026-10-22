import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { CreateSubmissionIntents } from "../lib/createSubmissionIntent";
import { linkInputArtifact } from "./workflow-start";

type Link = {
  namespace: string;
  workflow_id: string;
  run_id: string;
  link_type: string;
  label: string;
};

const execution = {
  namespace: "moonmind",
  workflowId: "mm:accepted",
  runId: "run-1",
};
const inputLink: Link = {
  namespace: execution.namespace,
  workflow_id: execution.workflowId,
  run_id: execution.runId,
  link_type: "input.instructions",
  label: "Submitted Workflow Input",
};
const body = { payload: { task: { instructions: "One workflow" } } };

function restoredRetry() {
  const store = new CreateSubmissionIntents("artifact-link-test");
  const intent = store.forRequest(body);
  return { intent, save: () => store.save(intent) };
}

describe("accepted Create artifact link replay", () => {
  let links: Map<string, Link[]>;
  let failedPost: { artifactId: string; committed: boolean } | null;
  let metadataFailure: "http" | "network" | "malformed" | null;
  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.sessionStorage.clear();
    links = new Map();
    failedPost = null;
    metadataFailure = null;
    fetchSpy = vi.spyOn(window, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      const artifactId = decodeURIComponent(url.replace(/^\/api\/artifacts\//, "").replace(/\/links$/, ""));
      const artifactLinks = links.get(artifactId) ?? [];
      if (init?.method === "POST") {
        const failure = failedPost?.artifactId === artifactId ? failedPost : null;
        if (failure) failedPost = null;
        if (!failure || failure.committed) {
          artifactLinks.push(JSON.parse(String(init.body)) as Link);
          links.set(artifactId, artifactLinks);
        }
        if (failure) throw new Error("Link acknowledgment was lost.");
      } else {
        if (metadataFailure === "network") throw new Error("Metadata unavailable.");
        if (metadataFailure === "http") {
          return { ok: false, json: async () => ({ detail: "Metadata unavailable." }) } as Response;
        }
        if (metadataFailure === "malformed") {
          return { ok: true, json: async () => ({}) } as Response;
        }
      }
      return { ok: true, json: async () => ({ links: artifactLinks }) } as Response;
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function postCount(artifactId: string) {
    return fetchSpy.mock.calls.filter(([url, init]) =>
      String(url) === `/api/artifacts/${artifactId}/links` && init?.method === "POST",
    ).length;
  }

  it("does not repeat confirmed links after navigation failure and reload", async () => {
    await linkInputArtifact("input-1", execution, { retry: restoredRetry() });
    await linkInputArtifact("input-1", execution, { retry: restoredRetry() });

    expect(postCount("input-1")).toBe(1);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(links.get("input-1")).toHaveLength(1);
  });

  it("keeps the first link when a later attachment is unconfirmed, then accepts only its visible original link", async () => {
    const retry = restoredRetry();
    await linkInputArtifact("input-1", execution, { retry });
    failedPost = { artifactId: "attachment-1", committed: false };
    await expect(linkInputArtifact("attachment-1", execution, {
      linkType: "input.attachment", label: "photo.png", retry,
    })).rejects.toThrow("Link acknowledgment was lost.");

    const restored = restoredRetry();
    await linkInputArtifact("input-1", execution, { retry: restored });
    await expect(linkInputArtifact("attachment-1", execution, {
      linkType: "input.attachment", label: "photo.png", retry: restored,
    })).rejects.toThrow("The previous artifact link is still unconfirmed.");

    expect(postCount("input-1")).toBe(1);
    expect(postCount("attachment-1")).toBe(1);
    expect(fetchSpy).toHaveBeenCalledWith("/api/artifacts/attachment-1", expect.anything());
    expect(links.get("input-1")).toHaveLength(1);
    expect(links.has("attachment-1")).toBe(false);

    links.set("attachment-1", [{ ...inputLink, link_type: "input.attachment", label: "photo.png" }]);
    await linkInputArtifact("attachment-1", execution, {
      linkType: "input.attachment", label: "photo.png", retry: restoredRetry(),
    });
    expect(postCount("attachment-1")).toBe(1);
    expect(links.get("attachment-1")).toHaveLength(1);
  });

  it("does not race a delayed original commit after an empty reconciliation read", async () => {
    failedPost = { artifactId: "input-1", committed: false };
    await expect(linkInputArtifact("input-1", execution, { retry: restoredRetry() }))
      .rejects.toThrow("Link acknowledgment was lost.");

    await expect(linkInputArtifact("input-1", execution, { retry: restoredRetry() }))
      .rejects.toThrow("The previous artifact link is still unconfirmed.");
    expect(postCount("input-1")).toBe(1);
    expect(links.has("input-1")).toBe(false);

    // The original POST transaction commits only after the first GET returned [].
    links.set("input-1", [...(links.get("input-1") ?? []), { ...inputLink }]);
    await linkInputArtifact("input-1", execution, { retry: restoredRetry() });
    await linkInputArtifact("input-1", execution, { retry: restoredRetry() });

    expect(postCount("input-1")).toBe(1);
    expect(fetchSpy).toHaveBeenCalledTimes(3);
    expect(links.get("input-1")).toHaveLength(1);
  });

  it("reconciles a committed link whose acknowledgment was lost before retrying after reload", async () => {
    failedPost = { artifactId: "input-1", committed: true };
    await expect(linkInputArtifact("input-1", execution, { retry: restoredRetry() }))
      .rejects.toThrow("Link acknowledgment was lost.");

    await linkInputArtifact("input-1", execution, { retry: restoredRetry() });
    await linkInputArtifact("input-1", execution, { retry: restoredRetry() });

    expect(postCount("input-1")).toBe(1);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(links.get("input-1")).toHaveLength(1);
  });

  it.each(["http", "network", "malformed"] as const)(
    "does not repeat an uncertain POST when metadata is %s, and can resume once it is readable",
    async (failure) => {
      failedPost = { artifactId: "input-1", committed: true };
      await expect(linkInputArtifact("input-1", execution, { retry: restoredRetry() }))
        .rejects.toThrow("Link acknowledgment was lost.");
      metadataFailure = failure;
      await expect(linkInputArtifact("input-1", execution, { retry: restoredRetry() })).rejects.toThrow();
      expect(postCount("input-1")).toBe(1);

      metadataFailure = null;
      await linkInputArtifact("input-1", execution, { retry: restoredRetry() });
      expect(postCount("input-1")).toBe(1);
      expect(links.get("input-1")).toHaveLength(1);
    },
  );

  it.each(["namespace", "workflow_id", "run_id", "link_type", "label"] as const)(
    "does not mistake a link with a different %s for the uncertain link",
    async (field) => {
      failedPost = { artifactId: "input-1", committed: true };
      await expect(linkInputArtifact("input-1", execution, { retry: restoredRetry() }))
        .rejects.toThrow("Link acknowledgment was lost.");
      const existing = links.get("input-1")![0]!;
      existing[field] = "unrelated";

      await expect(linkInputArtifact("input-1", execution, { retry: restoredRetry() }))
        .rejects.toThrow("The previous artifact link is still unconfirmed.");
      expect(postCount("input-1")).toBe(1);
      expect(fetchSpy.mock.calls.map(([url]) => String(url))).toEqual([
        "/api/artifacts/input-1/links", "/api/artifacts/input-1",
      ]);
      expect(links.get("input-1")).toHaveLength(1);
    },
  );
});
