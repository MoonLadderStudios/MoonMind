import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { BootPayload } from "../boot/parseBootPayload";
import { navigateTo } from "../lib/navigation";
import {
  buildTemporalSubmissionDraftFromExecution,
  resolveTaskSubmitPageMode,
} from "../lib/temporalTaskEditing";
import { renderWithClient } from "../utils/test-utils";
import {
  ARTIFACT_COMPLETE_RETRY_DELAYS_MS,
  resolveObjectiveInstructions,
  TaskCreatePage,
} from "./task-create";

vi.mock("../lib/navigation", () => ({
  navigateTo: vi.fn(),
}));

const mockPayload: BootPayload = {
  page: "task-create",
  apiBase: "/api",
  initialData: {
    dashboardConfig: {
      sources: {
        temporal: {
          create: "/api/executions",
          artifactCreate: "/api/artifacts",
        },
      },
      system: {
        defaultRepository: "MoonLadderStudios/MoonMind",
        defaultTaskRuntime: "codex_cli",
        defaultTaskModel: "gpt-5.4",
        defaultTaskEffort: "medium",
        defaultPublishMode: "pr",
        defaultProposeTasks: false,
        defaultTaskModelByRuntime: {
          codex_cli: "gpt-5.4",
          gemini_cli: "gemini-2.5-pro",
          claude_code: "claude-3.7-sonnet",
        },
        defaultTaskEffortByRuntime: {
          codex_cli: "medium",
          gemini_cli: "high",
          claude_code: "low",
        },
        supportedTaskRuntimes: ["codex_cli", "gemini_cli", "claude_code"],
        providerProfiles: {
          list: "/api/v1/provider-profiles",
        },
        taskTemplateCatalog: {
          enabled: true,
          templateSaveEnabled: true,
          list: "/api/task-step-templates",
          detail: "/api/task-step-templates/{slug}",
          expand: "/api/task-step-templates/{slug}:expand",
          saveFromTask: "/api/task-step-templates/save-from-task",
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

function withJiraIntegration(payload: BootPayload = mockPayload): BootPayload {
  const initialData = payload.initialData as {
    dashboardConfig: {
      sources?: Record<string, unknown>;
      system?: Record<string, unknown>;
    };
  };
  return {
    ...payload,
    initialData: {
      ...initialData,
      dashboardConfig: {
        ...initialData.dashboardConfig,
        sources: {
          ...initialData.dashboardConfig.sources,
          jira: {
            connections: "/api/jira/connections/verify",
            projects: "/api/jira/projects",
            boards: "/api/jira/projects/{projectKey}/boards",
            columns: "/api/jira/boards/{boardId}/columns",
            issues: "/api/jira/boards/{boardId}/issues",
            issue: "/api/jira/issues/{issueKey}",
          },
        },
        system: {
          ...initialData.dashboardConfig.system,
          jiraIntegration: {
            enabled: true,
            defaultProjectKey: "ENG",
            defaultBoardId: "42",
            rememberLastBoardInSession: true,
          },
        },
      },
    },
  };
}

describe("Task Create Entrypoint", () => {
  let fetchSpy: MockInstance;
  let executionResponseOverride: Response | null;

  beforeEach(() => {
    window.history.pushState({}, "Task Create", "/tasks/new");
    vi.mocked(navigateTo).mockReset();
    executionResponseOverride = null;
    fetchSpy = vi
      .spyOn(window, "fetch")
      .mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.startsWith("/api/tasks/skills")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: { worker: ["speckit-orchestrate", "pr-resolver"] },
            }),
          } as Response);
        }
        if (url.startsWith("/api/task-step-templates?scope=personal")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [],
            }),
          } as Response);
        }
        if (url.startsWith("/api/task-step-templates?scope=global")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                {
                  slug: "speckit-demo",
                  scope: "global",
                  title: "Spec Kit Demo",
                  description: "Seed a two-step planning flow.",
                  latestVersion: "1.2.3",
                  version: "1.2.3",
                },
                {
                  slug: "objective-demo",
                  scope: "global",
                  title: "Objective Request Demo",
                  description:
                    "Use template inputs to derive the task objective.",
                  latestVersion: "2.0.0",
                  version: "2.0.0",
                },
              ],
            }),
          } as Response);
        }
        if (
          url.startsWith("/api/task-step-templates/speckit-demo?scope=global")
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              slug: "speckit-demo",
              scope: "global",
              title: "Spec Kit Demo",
              description: "Seed a two-step planning flow.",
              latestVersion: "1.2.3",
              version: "1.2.3",
              inputs: [
                {
                  name: "feature_name",
                  label: "Feature Name",
                  type: "text",
                  required: true,
                },
              ],
            }),
          } as Response);
        }
        if (
          url.startsWith("/api/task-step-templates/objective-demo?scope=global")
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              slug: "objective-demo",
              scope: "global",
              title: "Objective Request Demo",
              description: "Use template inputs to derive the task objective.",
              latestVersion: "2.0.0",
              version: "2.0.0",
              inputs: [
                {
                  name: "request",
                  label: "Request",
                  type: "text",
                  required: true,
                },
              ],
            }),
          } as Response);
        }
        if (
          url.startsWith(
            "/api/task-step-templates/speckit-demo:expand?scope=global",
          )
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              steps: [
                {
                  id: "tpl:speckit-demo:1.2.3:01",
                  title: "Clarify spec",
                  instructions: "Clarify the {{ inputs.feature_name }} scope.",
                  skill: {
                    id: "speckit-clarify",
                    args: { feature: "Task Create" },
                  },
                },
                {
                  id: "tpl:speckit-demo:1.2.3:02",
                  title: "Plan implementation",
                  instructions: "Write a plan for the task builder recovery.",
                },
              ],
              appliedTemplate: {
                slug: "speckit-demo",
                version: "1.2.3",
              },
              warnings: [],
            }),
          } as Response);
        }
        if (
          url.startsWith(
            "/api/task-step-templates/objective-demo:expand?scope=global",
          )
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              steps: [
                {
                  id: "tpl:objective-demo:2.0.0:01",
                  title: "Clarify request",
                  instructions: "",
                  skill: {
                    id: "speckit-clarify",
                    args: { mode: "objective" },
                  },
                },
                {
                  id: "tpl:objective-demo:2.0.0:02",
                  title: "Review objective",
                  instructions: "Review the resulting task objective.",
                },
              ],
              appliedTemplate: {
                slug: "objective-demo",
                version: "2.0.0",
              },
              warnings: [],
            }),
          } as Response);
        }
        if (url.startsWith("/api/v1/provider-profiles")) {
          const runtimeId = new URL(`http://localhost${url}`).searchParams.get(
            "runtime_id",
          );
          const items =
            runtimeId === "gemini_cli"
              ? [
                  {
                    profile_id: "profile:gemini-default",
                    account_label: "Gemini Default",
                    is_default: true,
                  },
                ]
              : runtimeId === "claude_code"
                ? [
                    {
                      profile_id: "profile:claude-default",
                      account_label: "Claude Default",
                      is_default: true,
                    },
                  ]
                : [
                    {
                      profile_id: "profile:codex-default",
                      account_label: "Codex Default",
                      is_default: true,
                    },
                    {
                      profile_id: "profile:codex-secondary",
                      account_label: "Codex Secondary",
                      is_default: false,
                    },
                  ];
          return Promise.resolve({
            ok: true,
            json: async () => items,
          } as Response);
        }
        if (url.startsWith("/api/executions?source=temporal&pageSize=50&workflowType=MoonMind.Run&entry=run")) {
          const depItems = Array.from({ length: 12 }, (_, i) => ({
            taskId: `mm:dep-${i + 1}`,
            workflowType: "MoonMind.Run",
            entry: "run",
            title: i === 0 ? "Build shared schema" : `Dependency task ${i + 1}`,
            state: i % 3 === 0 ? "completed" : "executing",
          }));
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: depItems,
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aedit-123?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:edit-123",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "gemini_cli",
              profileId: "profile:gemini-default",
              model: "gemini-2.5-pro",
              effort: "high",
              repository: "MoonLadderStudios/MoonMind",
              startingBranch: "main",
              targetBranch: "task-editing-phase-2",
              publishMode: "branch",
              targetSkill: "speckit-implement",
              inputParameters: {
                targetRuntime: "gemini_cli",
                task: {
                  instructions: "Rebuild the Temporal task draft.",
                  runtime: {
                    mode: "gemini_cli",
                    model: "gemini-2.5-pro",
                    effort: "high",
                    profileId: "profile:gemini-default",
                  },
                  git: {
                    startingBranch: "main",
                    targetBranch: "task-editing-phase-2",
                  },
                  publish: { mode: "branch" },
                  tool: { type: "skill", name: "speckit-implement" },
                  appliedStepTemplates: [
                    {
                      slug: "speckit-demo",
                      version: "1.2.3",
                      inputs: { feature_name: "Task Editing" },
                      stepIds: ["tpl:speckit-demo:1.2.3:01"],
                      appliedAt: "2026-04-12T00:00:00Z",
                      capabilities: ["git"],
                    },
                  ],
                },
              },
              actions: {
                canUpdateInputs: true,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Arerun-123?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:rerun-123",
              workflowType: "MoonMind.Run",
              state: "completed",
              targetRuntime: "codex_cli",
              profileId: "profile:codex-secondary",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              publishMode: "pr",
              inputArtifactRef: "historical-input",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                    profileId: "profile:codex-secondary",
                  },
                  publish: { mode: "pr" },
                  tool: { type: "skill", name: "speckit-orchestrate" },
                },
              },
              actions: {
                canUpdateInputs: false,
                canRerun: true,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aunsupported?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:unsupported",
              workflowType: "MoonMind.ManifestIngest",
              state: "completed",
              inputParameters: {},
              actions: {
                canUpdateInputs: false,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Ano-edit?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:no-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
              inputParameters: {
                task: { instructions: "Existing task instructions." },
              },
              actions: {
                canUpdateInputs: false,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Ano-rerun?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:no-rerun",
              workflowType: "MoonMind.Run",
              state: "completed",
              inputParameters: {
                task: { instructions: "Terminal task instructions." },
              },
              actions: {
                canUpdateInputs: false,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Amissing-artifact?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:missing-artifact",
              workflowType: "MoonMind.Run",
              state: "completed",
              inputArtifactRef: "missing-input",
              inputParameters: { task: {} },
              actions: {
                canUpdateInputs: false,
                canRerun: true,
              },
            }),
          } as Response);
        }
        if (
          url === "/api/executions/mm%3Amalformed-artifact?source=temporal"
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:malformed-artifact",
              workflowType: "MoonMind.Run",
              state: "completed",
              inputArtifactRef: "malformed-input",
              inputParameters: { task: {} },
              actions: {
                canUpdateInputs: false,
                canRerun: true,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aincomplete?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:incomplete",
              workflowType: "MoonMind.Run",
              state: "executing",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                  },
                },
              },
              actions: {
                canUpdateInputs: true,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (
          url ===
          "/gateway/api/executions/mm%3Acustom-endpoints?view=detail&source=temporal"
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:custom-endpoints",
              workflowType: "MoonMind.Run",
              state: "completed",
              targetRuntime: "codex_cli",
              profileId: "profile:codex-secondary",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              publishMode: "pr",
              inputArtifactRef: "custom-input",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                    profileId: "profile:codex-secondary",
                  },
                },
              },
              actions: {
                canUpdateInputs: false,
                canRerun: true,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions") {
          if (executionResponseOverride) {
            return Promise.resolve(executionResponseOverride);
          }
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:workflow-123",
              runId: "run-123",
              namespace: "moonmind",
              redirectPath: "/tasks/mm:workflow-123?source=temporal",
            }),
          } as Response);
        }
        if (url === "/api/task-step-templates/save-from-task") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              slug: "saved-preset",
              scope: "personal",
              title: "Saved preset",
              latestVersion: "1.0.0",
            }),
          } as Response);
        }
        if (url === "/api/artifacts") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              artifact_ref: {
                artifact_id: "art-001",
              },
              upload: {
                mode: "single",
                upload_url: "/api/artifacts/art-001/content",
                expires_at: "2026-04-02T00:00:00Z",
                max_size_bytes: 100000,
                required_headers: {},
              },
            }),
          } as Response);
        }
        if (url === "/api/artifacts/art-001/content") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ artifact_id: "art-001" }),
          } as Response);
        }
        if (url === "/api/artifacts/art-001/complete") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ artifact_id: "art-001" }),
          } as Response);
        }
        if (url === "/api/artifacts/art-001/links") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ artifact_id: "art-001" }),
          } as Response);
        }
        if (url === "/api/artifacts/historical-input/download") {
          return Promise.resolve({
            ok: true,
            text: async () =>
              JSON.stringify({
                repository: "MoonLadderStudios/MoonMind",
                task: {
                  instructions: "Rerun from artifact-backed instructions.",
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                    profileId: "profile:codex-secondary",
                  },
                  publish: { mode: "pr" },
                  tool: { type: "skill", name: "speckit-orchestrate" },
                },
              }),
          } as Response);
        }
        if (url === "/api/artifacts/missing-input/download") {
          return Promise.resolve({
            ok: false,
            status: 404,
            statusText: "Not Found",
            text: async () => "",
          } as Response);
        }
        if (url === "/api/artifacts/malformed-input/download") {
          return Promise.resolve({
            ok: true,
            text: async () => "not-json",
          } as Response);
        }
        if (url === "/gateway/api/artifacts/custom-input/raw") {
          return Promise.resolve({
            ok: true,
            text: async () =>
              JSON.stringify({
                task: {
                  instructions: "Loaded through configured artifact route.",
                  skill: { id: "speckit-orchestrate" },
                },
              }),
          } as Response);
        }
        if (url === "/api/jira/projects") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                { key: "OPS", name: "Operations" },
                { key: "ENG", name: "Engineering" },
              ],
            }),
          } as Response);
        }
        if (url === "/api/jira/projects/ENG/boards") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                { id: "7", name: "Backlog", projectKey: "ENG" },
                { id: "42", name: "Delivery", projectKey: "ENG" },
              ],
            }),
          } as Response);
        }
        if (url === "/api/jira/boards/42/columns") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              board: { id: "42", name: "Delivery", projectKey: "ENG" },
              columns: [
                { id: "todo", name: "To Do", count: 1 },
                { id: "doing", name: "Doing", count: 1 },
              ],
            }),
          } as Response);
        }
        if (url === "/api/jira/boards/42/issues") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              boardId: "42",
              columns: [
                { id: "todo", name: "To Do" },
                { id: "doing", name: "Doing" },
              ],
              itemsByColumn: {
                todo: [
                  {
                    issueKey: "ENG-101",
                    summary: "Plan queue controls",
                    issueType: "Story",
                    statusName: "Selected",
                    assignee: "Ada",
                    updatedAt: "2026-04-10T19:30:00Z",
                  },
                ],
                doing: [
                  {
                    issueKey: "ENG-202",
                    summary: "Build browser shell",
                    issueType: "Story",
                    statusName: "In Progress",
                    assignee: "Grace",
                    updatedAt: "2026-04-11T19:30:00Z",
                  },
                ],
              },
            }),
          } as Response);
        }
        if (url === "/api/jira/issues/ENG-202") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              issueKey: "ENG-202",
              url: "https://jira.example.test/browse/ENG-202",
              summary: "Build browser shell",
              issueType: "Story",
              column: { id: "doing", name: "Doing" },
              status: { id: "3", name: "In Progress" },
              descriptionText: "Let operators browse Jira stories.",
              acceptanceCriteriaText:
                "Given a board, users can select a story preview.",
              recommendedImports: {
                presetInstructions:
                  "ENG-202: Build browser shell\n\nLet operators browse Jira stories.",
                stepInstructions:
                  "Complete Jira story ENG-202: Build browser shell",
              },
            }),
          } as Response);
        }
        return Promise.resolve({
          ok: false,
          status: 404,
          statusText: `Unhandled fetch for ${url} ${String(init?.method || "GET")}`,
          text: async () => "Unhandled fetch",
        } as Response);
      });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("resolves task submit mode with rerun taking precedence over edit", () => {
    expect(resolveTaskSubmitPageMode("")).toEqual({
      mode: "create",
      executionId: null,
    });
    expect(resolveTaskSubmitPageMode("?editExecutionId=mm%3Aedit")).toEqual({
      mode: "edit",
      executionId: "mm:edit",
    });
    expect(
      resolveTaskSubmitPageMode(
        "?editExecutionId=mm%3Aedit&rerunExecutionId=mm%3Arerun",
      ),
    ).toEqual({
      mode: "rerun",
      executionId: "mm:rerun",
    });
  });

  it("does not load an execution detail draft in create mode", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(await screen.findByRole("heading", { name: "Create Task" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create" })).toBeTruthy();
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/tasks/skills",
        expect.objectContaining({
          headers: { Accept: "application/json" },
        }),
      );
    });
    expect(
      fetchSpy.mock.calls.some(([url]) =>
        /^\/api\/executions\/[^?]+\?source=temporal$/.test(String(url)),
      ),
    ).toBe(false);
  });

  it("reconstructs a create-form draft from Temporal execution fields", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:edit-123",
      workflowType: "MoonMind.Run",
      targetRuntime: "gemini_cli",
      profileId: "profile:gemini-default",
      model: "gemini-2.5-pro",
      effort: "high",
      repository: "MoonLadderStudios/MoonMind",
      startingBranch: "main",
      targetBranch: "task-editing-phase-2",
      publishMode: "branch",
      targetSkill: "speckit-implement",
      inputParameters: {
        task: {
          instructions: "Rebuild the Temporal task draft.",
          appliedStepTemplates: [
            {
              slug: "speckit-demo",
              version: "1.2.3",
              inputs: { feature_name: "Task Editing" },
              stepIds: ["tpl:speckit-demo:1.2.3:01"],
              appliedAt: "2026-04-12T00:00:00Z",
              capabilities: ["git"],
            },
          ],
        },
      },
    });

    expect(draft).toMatchObject({
      runtime: "gemini_cli",
      providerProfile: "profile:gemini-default",
      model: "gemini-2.5-pro",
      effort: "high",
      repository: "MoonLadderStudios/MoonMind",
      startingBranch: "main",
      targetBranch: "task-editing-phase-2",
      publishMode: "branch",
      taskInstructions: "Rebuild the Temporal task draft.",
      primarySkill: "speckit-implement",
    });
    expect(draft.appliedTemplates).toEqual([
      expect.objectContaining({
        slug: "speckit-demo",
        version: "1.2.3",
        inputs: { feature_name: "Task Editing" },
      }),
    ]);
  });

  it("reconstructs a draft from an artifact-backed execution contract", () => {
    const draft = buildTemporalSubmissionDraftFromExecution(
      {
        workflowId: "mm:rerun-123",
        workflowType: "MoonMind.Run",
        inputArtifactRef: "historical-input",
        inputParameters: {
          targetRuntime: "codex_cli",
          task: {
            runtime: {
              mode: "codex_cli",
              model: "gpt-5.4",
              effort: "medium",
              profileId: "profile:codex-secondary",
            },
          },
        },
        actions: {
          canUpdateInputs: false,
          canRerun: true,
        },
      },
      {
        repository: "MoonLadderStudios/MoonMind",
        task: {
          instructions: "Rerun from immutable artifact input.",
          git: {
            startingBranch: "main",
            targetBranch: "rerun-target",
          },
          publish: { mode: "pr" },
          skill: { id: "speckit-orchestrate" },
        },
      },
    );

    expect(draft).toMatchObject({
      runtime: "codex_cli",
      providerProfile: "profile:codex-secondary",
      model: "gpt-5.4",
      effort: "medium",
      repository: "MoonLadderStudios/MoonMind",
      startingBranch: "main",
      targetBranch: "rerun-target",
      publishMode: "pr",
      taskInstructions: "Rerun from immutable artifact input.",
      primarySkill: "speckit-orchestrate",
    });
  });

  it("includes step-level instructions when reconstructing a draft", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:step-instructions",
      workflowType: "MoonMind.Run",
      inputParameters: {
        task: {
          instructions: "Top-level objective.",
          steps: [
            { instructions: "First step instructions." },
            { instructions: "Second step instructions." },
          ],
        },
      },
    });

    expect(draft.taskInstructions).toBe(
      [
        "Top-level objective.",
        "First step instructions.",
        "Second step instructions.",
      ].join("\n\n"),
    );
  });

  it("uses null for optional draft fields that cannot be reconstructed", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:minimal",
      workflowType: "MoonMind.Run",
      inputParameters: {
        task: {
          instructions: "Only instructions are available.",
        },
      },
    });

    expect(draft).toMatchObject({
      runtime: null,
      providerProfile: null,
      model: null,
      effort: null,
      repository: null,
      startingBranch: null,
      targetBranch: null,
      publishMode: null,
      primarySkill: null,
      taskInstructions: "Only instructions are available.",
    });
  });

  it("fails draft reconstruction when instructions are missing", () => {
    expect(() =>
      buildTemporalSubmissionDraftFromExecution({
        workflowId: "mm:incomplete",
        workflowType: "MoonMind.Run",
        inputParameters: {
          targetRuntime: "codex_cli",
          task: {
            runtime: {
              mode: "codex_cli",
              model: "gpt-5.4",
              effort: "medium",
            },
          },
        },
      }),
    ).toThrow("Task instructions could not be reconstructed from this execution.");
  });

  it("loads edit mode from an active Temporal execution and prefills the shared form", async () => {
    window.history.pushState(
      {},
      "Task Edit",
      "/tasks/new?editExecutionId=mm%3Aedit-123",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(await screen.findByRole("heading", { name: "Edit Task" })).toBeTruthy();
    await waitFor(() => {
      expect(
        (screen.getByLabelText("Instructions") as HTMLTextAreaElement).value,
      ).toBe("Rebuild the Temporal task draft.");
      expect((screen.getByLabelText("Runtime") as HTMLSelectElement).value).toBe(
        "gemini_cli",
      );
      expect(
        (screen.getByLabelText("Provider profile") as HTMLSelectElement).value,
      ).toBe("profile:gemini-default");
      expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe(
        "gemini-2.5-pro",
      );
      expect((screen.getByLabelText("Effort") as HTMLInputElement).value).toBe(
        "high",
      );
      expect(
        (screen.getByLabelText(/GitHub Repo/) as HTMLInputElement).value,
      ).toBe("MoonLadderStudios/MoonMind");
      expect(
        (screen.getByLabelText("Starting Branch (optional)") as HTMLInputElement)
          .value,
      ).toBe("main");
      expect(
        (screen.getByLabelText("Target Branch (optional)") as HTMLInputElement)
          .value,
      ).toBe("task-editing-phase-2");
      expect(
        (screen.getByLabelText("Publish Mode") as HTMLSelectElement).value,
      ).toBe("branch");
      expect(
        (screen.getByLabelText(/Skill \(optional\)/) as HTMLInputElement).value,
      ).toBe("speckit-implement");
    });
    expect(screen.queryByText("Schedule (optional)")).toBeNull();
    expect(screen.getByRole("button", { name: "Save Changes" })).toBeTruthy();
  });

  it("loads rerun mode instructions from an input artifact when inline instructions are absent", async () => {
    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Arerun-123",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(await screen.findByRole("heading", { name: "Rerun Task" })).toBeTruthy();
    await waitFor(() => {
      expect(
        (screen.getByLabelText("Instructions") as HTMLTextAreaElement).value,
      ).toBe("Rerun from artifact-backed instructions.");
      expect(
        (screen.getByLabelText("Provider profile") as HTMLSelectElement).value,
      ).toBe("profile:codex-secondary");
    });
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/artifacts/historical-input/download",
      expect.objectContaining({
        headers: { Accept: "application/json" },
      }),
    );
    expect(screen.queryByText("Schedule (optional)")).toBeNull();
    expect(screen.getByRole("button", { name: "Rerun Task" })).toBeTruthy();
  });

  it("loads draft inputs through configured detail and artifact download routes", async () => {
    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Acustom-endpoints",
    );
    const customPayload = JSON.parse(JSON.stringify(mockPayload)) as BootPayload;
    (
      customPayload.initialData as {
        dashboardConfig: {
          sources: {
            temporal: {
              detail: string;
              artifactDownload: string;
            };
          };
        };
      }
    ).dashboardConfig.sources.temporal = {
      ...(
        customPayload.initialData as {
          dashboardConfig: { sources: { temporal: Record<string, string> } };
        }
      ).dashboardConfig.sources.temporal,
      detail: "/gateway/api/executions/{workflowId}?view=detail",
      artifactDownload: "/gateway/api/artifacts/{artifactId}/raw",
    };

    renderWithClient(<TaskCreatePage payload={customPayload} />);

    expect(await screen.findByRole("heading", { name: "Rerun Task" })).toBeTruthy();
    await waitFor(() => {
      expect(
        (screen.getByLabelText("Instructions") as HTMLTextAreaElement).value,
      ).toBe("Loaded through configured artifact route.");
    });
    expect(fetchSpy).toHaveBeenCalledWith(
      "/gateway/api/executions/mm%3Acustom-endpoints?view=detail&source=temporal",
      expect.objectContaining({
        headers: { Accept: "application/json" },
      }),
    );
    expect(fetchSpy).toHaveBeenCalledWith(
      "/gateway/api/artifacts/custom-input/raw",
      expect.objectContaining({
        headers: { Accept: "application/json" },
      }),
    );
  });

  it("shows a feature-disabled error without loading execution detail", async () => {
    window.history.pushState(
      {},
      "Task Edit",
      "/tasks/new?editExecutionId=mm%3Aedit-123",
    );
    const disabledPayload = JSON.parse(JSON.stringify(mockPayload)) as BootPayload;
    (
      disabledPayload.initialData as {
        dashboardConfig: {
          features: { temporalDashboard: { temporalTaskEditing: boolean } };
        };
      }
    ).dashboardConfig.features.temporalDashboard.temporalTaskEditing = false;

    renderWithClient(<TaskCreatePage payload={disabledPayload} />);

    expect(
      await screen.findByText("Temporal task editing is not enabled."),
    ).toBeTruthy();
    expect(
      fetchSpy.mock.calls.some(
        ([url]) => String(url) === "/api/executions/mm%3Aedit-123?source=temporal",
      ),
    ).toBe(false);
    expect(
      (screen.getByRole("button", { name: "Save Changes" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("shows an explicit error for unsupported Temporal workflow types", async () => {
    window.history.pushState(
      {},
      "Task Edit",
      "/tasks/new?editExecutionId=mm%3Aunsupported",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(
      await screen.findByText(
        "This execution cannot be edited here because only MoonMind.Run is supported.",
      ),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Save Changes" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("shows an explicit error when edit capability is missing", async () => {
    window.history.pushState(
      {},
      "Task Edit",
      "/tasks/new?editExecutionId=mm%3Ano-edit",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(
      await screen.findByText(
        "This execution does not currently allow editing its inputs.",
      ),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Save Changes" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("shows an explicit error when rerun capability is missing", async () => {
    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Ano-rerun",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(
      await screen.findByText("This execution does not currently allow rerun."),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Rerun Task" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("shows an explicit error when the input artifact cannot be read", async () => {
    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Amissing-artifact",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(
      await screen.findByText(
        "Task instructions could not be loaded from the input artifact.",
      ),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Rerun Task" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("shows explicit errors for malformed artifacts and incomplete drafts", async () => {
    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Amalformed-artifact",
    );

    const { unmount } = renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(
      await screen.findByText("Task input artifact did not contain valid JSON."),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Rerun Task" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    unmount();
    window.history.pushState(
      {},
      "Task Edit",
      "/tasks/new?editExecutionId=mm%3Aincomplete",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(
      await screen.findByText(
        "Task instructions could not be reconstructed from this execution.",
      ),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Save Changes" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("submits the queue-shaped Temporal task payload and redirects on success", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    );
    expect(primaryStep).not.toBeNull();
    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Run end-to-end regression flow." },
    });
    fireEvent.change(screen.getByLabelText(/GitHub Repo/), {
      target: { value: "MoonLadderStudios/MoonMind" },
    });
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "speckit-orchestrate" },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({
          method: "POST",
        }),
      );
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request).toMatchObject({
      type: "task",
      priority: 0,
      maxAttempts: 3,
      payload: {
        repository: "MoonLadderStudios/MoonMind",
        targetRuntime: "codex_cli",
        task: {
          instructions: "Run end-to-end regression flow.",
          tool: {
            type: "skill",
            name: "speckit-orchestrate",
            version: "1.0",
          },
          runtime: {
            mode: "codex_cli",
            model: "gpt-5.4",
            effort: "medium",
          },
          publish: {
            mode: "pr",
          },
          proposeTasks: false,
        },
      },
    });
    expect(request.payload.requiredCapabilities).toEqual([
      "codex_cli",
      "git",
      "gh",
    ]);
    await waitFor(() => {
      expect(navigateTo).toHaveBeenCalledWith(
        "/tasks/mm:workflow-123?source=temporal",
      );
    });
  });

  it("submits selected task dependencies from the picker", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Run the dependent stage." },
    });
    fireEvent.change(screen.getByLabelText("Existing run"), {
      target: { value: "mm:dep-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add dependency" }));

    await waitFor(() => {
      expect(screen.getByText(/Build shared schema/)).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.task.dependsOn).toEqual(["mm:dep-1"]);
  });

  it("defaults publish mode to none when selecting pr-resolver skills", async () => {
    type MockInitialData = {
      dashboardConfig: {
        system: {
          defaultPublishMode: string;
        };
      };
    };

    const payload: BootPayload = {
      ...mockPayload,
      initialData: {
        ...(mockPayload.initialData as MockInitialData),
        dashboardConfig: {
          ...(mockPayload.initialData as MockInitialData).dashboardConfig,
          system: {
            ...(mockPayload.initialData as MockInitialData).dashboardConfig
              .system,
            defaultPublishMode: "pr",
          },
        },
      },
    };

    renderWithClient(<TaskCreatePage payload={payload} />);

    const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    );
    expect(primaryStep).not.toBeNull();

    const publishSelect = screen.getByLabelText(
      "Publish Mode",
    ) as HTMLSelectElement;
    expect(publishSelect.value).toBe(
      (payload.initialData as MockInitialData).dashboardConfig.system
        .defaultPublishMode,
    );
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "pr-resolver" },
      },
    );
    await waitFor(() => {
      expect(
        (screen.getByLabelText("Publish Mode") as HTMLSelectElement).value,
      ).toBe("none");
    });

    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "batch-pr-resolver" },
      },
    );
    await waitFor(() => {
      expect(
        (screen.getByLabelText("Publish Mode") as HTMLSelectElement).value,
      ).toBe("none");
    });
  });

  it("submits publish mode none when the selected primary skill is pr-resolver", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    );
    expect(primaryStep).not.toBeNull();
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "pr-resolver" },
      },
    );
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText("Instructions"),
      {
        target: { value: "Resolve the current branch PR." },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.task.publish).toMatchObject({
      mode: "none",
    });
  });

  it("renders the restored legacy create-task controls", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(await screen.findByPlaceholderText("owner/repo")).not.toBeNull();
    expect(await screen.findByLabelText("Provider profile")).not.toBeNull();
    expect(
      await screen.findByLabelText("Feature Request / Initial Instructions"),
    ).not.toBeNull();
    expect(
      await screen.findByPlaceholderText(
        "auto-generated unless starting branch is non-default",
      ),
    ).not.toBeNull();
    expect(await screen.findByDisplayValue("3")).not.toBeNull();
    expect(screen.getByText("Task Presets (optional)")).not.toBeNull();
    expect(screen.getByText("Schedule (optional)")).not.toBeNull();
  });

  it("updates provider-profile options when the selected runtime changes", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const providerSelect = await screen.findByLabelText("Provider profile");
    await waitFor(() => {
      const labels = Array.from(
        (providerSelect as HTMLSelectElement).options,
      ).map((option) => option.text);
      expect(labels).toEqual([
        "Codex Default (Default)",
        "Codex Secondary",
      ]);
      expect((providerSelect as HTMLSelectElement).value).toBe(
        "profile:codex-default",
      );
    });

    fireEvent.change(screen.getByLabelText("Runtime"), {
      target: { value: "gemini_cli" },
    });

    await waitFor(() => {
      const labels = Array.from(
        (providerSelect as HTMLSelectElement).options,
      ).map((option) => option.text);
      expect(labels).toEqual(["Gemini Default (Default)"]);
      expect((providerSelect as HTMLSelectElement).value).toBe(
        "profile:gemini-default",
      );
    });
  });

  it("uploads oversized task input as a JSON artifact before submitting the execution", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Large instructions ".repeat(1000) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/artifacts",
        expect.objectContaining({ method: "POST" }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/artifacts/art-001/content",
        expect.objectContaining({ method: "PUT" }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/artifacts/art-001/complete",
        expect.objectContaining({ method: "POST" }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/artifacts/art-001/links",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.inputArtifactRef).toBe("art-001");
    expect(request.payload.task.instructions).toBeUndefined();

    const uploadCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/artifacts/art-001/content")
      .at(-1);
    const uploadHeaders = new Headers(uploadCall?.[1]?.headers);
    expect(uploadHeaders.get("content-type")).toBe(
      "application/json; charset=utf-8",
    );
    expect(JSON.parse(String(uploadCall?.[1]?.body))).toMatchObject({
      repository: "MoonLadderStudios/MoonMind",
      task: {
        instructions: expect.stringContaining(
          "Large instructions Large instructions",
        ),
      },
    });
  });

  it("uploads task input to the returned single-put URL and finalizes the artifact before task creation", async () => {
    const providerProfileItems = [
      {
        profile_id: "profile:codex-default",
        account_label: "Codex Default",
        is_default: true,
      },
      {
        profile_id: "profile:codex-secondary",
        account_label: "Codex Secondary",
        is_default: false,
      },
    ];

    fetchSpy.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/v1/provider-profiles")) {
          return Promise.resolve({
            ok: true,
            json: async () => providerProfileItems,
          } as Response);
        }
        if (url.startsWith("/api/executions?source=temporal&pageSize=50&workflowType=MoonMind.Run&entry=run")) {
          const depItems = Array.from({ length: 12 }, (_, i) => ({
            taskId: `mm:dep-${i + 1}`,
            workflowType: "MoonMind.Run",
            entry: "run",
            title: i === 0 ? "Build shared schema" : `Dependency task ${i + 1}`,
            state: i % 3 === 0 ? "completed" : "executing",
          }));
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: depItems,
            }),
          } as Response);
        }
        if (url === "/api/executions") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:workflow-123",
              runId: "run-123",
              namespace: "moonmind",
              redirectPath: "/tasks/mm:workflow-123?source=temporal",
            }),
          } as Response);
        }
        if (url === "/api/artifacts") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              artifact_ref: {
                artifact_id: "art-001",
              },
              upload: {
                mode: "single_put",
                upload_url:
                  "http://localhost:9000/moonmind-temporal-artifacts/demo-presigned-upload",
                expires_at: "2026-04-02T00:00:00Z",
                max_size_bytes: 100000,
                required_headers: {
                  "content-type": "text/plain",
                  "x-amz-server-side-encryption": "AES256",
                },
              },
            }),
          } as Response);
        }
        if (
          url ===
          "http://localhost:9000/moonmind-temporal-artifacts/demo-presigned-upload"
        ) {
          const headers = new Headers(init?.headers);
          expect(headers.get("content-type")).toBe("text/plain");
          expect(headers.get("x-amz-server-side-encryption")).toBe("AES256");
          return Promise.resolve({
            ok: true,
            text: async () => "",
          } as Response);
        }
        if (url === "/api/artifacts/art-001/complete") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ artifact_id: "art-001" }),
          } as Response);
        }
        if (url === "/api/artifacts/art-001/links") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ artifact_id: "art-001" }),
          } as Response);
        }
        return Promise.resolve({
          ok: false,
          status: 404,
          statusText: `Unhandled fetch for ${url} ${String(init?.method || "GET")}`,
          text: async () => "Unhandled fetch",
        } as Response);
      },
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Large instructions ".repeat(1000) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "http://localhost:9000/moonmind-temporal-artifacts/demo-presigned-upload",
        expect.objectContaining({ method: "PUT" }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/artifacts/art-001/complete",
        expect.objectContaining({ method: "POST" }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const presignedUploadIndex = fetchSpy.mock.calls.findIndex(
      ([url]) =>
        String(url) ===
        "http://localhost:9000/moonmind-temporal-artifacts/demo-presigned-upload",
    );
    const completeIndex = fetchSpy.mock.calls.findIndex(
      ([url]) => String(url) === "/api/artifacts/art-001/complete",
    );
    const executionIndex = fetchSpy.mock.calls.findIndex(
      ([url]) => String(url) === "/api/executions",
    );
    expect(presignedUploadIndex).toBeGreaterThanOrEqual(0);
    expect(completeIndex).toBeGreaterThan(presignedUploadIndex);
    expect(executionIndex).toBeGreaterThan(completeIndex);
  });

  it(
    "retries transient artifact completion conflicts before creating the task",
    async () => {
      let completeAttempts = 0;
      const originalRetryDelays = [...ARTIFACT_COMPLETE_RETRY_DELAYS_MS];

      try {
        ARTIFACT_COMPLETE_RETRY_DELAYS_MS.splice(
          0,
          ARTIFACT_COMPLETE_RETRY_DELAYS_MS.length,
          1,
          1,
          1,
          1,
          1,
        );
        fetchSpy.mockImplementation(
          (input: RequestInfo | URL, init?: RequestInit) => {
            const url = String(input);
            if (url.includes("/api/v1/provider-profiles")) {
              return Promise.resolve({
                ok: true,
                json: async () => [],
              } as Response);
            }
            if (url === "/api/artifacts") {
              return Promise.resolve({
                ok: true,
                json: async () => ({
                  artifact_ref: {
                    artifact_id: "art-001",
                  },
                  upload: {
                    mode: "single_put",
                    upload_url: "/api/artifacts/art-001/content",
                    expires_at: "2026-04-02T00:00:00Z",
                    max_size_bytes: 100000,
                    required_headers: {},
                  },
                }),
              } as Response);
            }
            if (url === "/api/artifacts/art-001/content") {
              return Promise.resolve({
                ok: true,
                text: async () => "",
              } as Response);
            }
            if (url === "/api/artifacts/art-001/complete") {
              completeAttempts += 1;
              if (completeAttempts < 5) {
                return Promise.resolve({
                  ok: false,
                  status: 409,
                  text: async () =>
                    JSON.stringify({
                      detail: {
                        code: "artifact_state_error",
                        message: "artifact upload is not complete",
                      },
                    }),
                } as Response);
              }
              return Promise.resolve({
                ok: true,
                json: async () => ({ artifact_id: "art-001" }),
              } as Response);
            }
            if (url === "/api/artifacts/art-001/links") {
              return Promise.resolve({
                ok: true,
                json: async () => ({ artifact_id: "art-001" }),
              } as Response);
            }
            if (url.startsWith("/api/executions?source=temporal&pageSize=50&workflowType=MoonMind.Run&entry=run")) {
              const depItems = Array.from({ length: 12 }, (_, i) => ({
                taskId: `mm:dep-${i + 1}`,
                workflowType: "MoonMind.Run",
                entry: "run",
                title: i === 0 ? "Build shared schema" : `Dependency task ${i + 1}`,
                state: i % 3 === 0 ? "completed" : "executing",
              }));
              return Promise.resolve({
                ok: true,
                json: async () => ({
                  items: depItems,
                }),
              } as Response);
            }
            if (url === "/api/executions") {
              return Promise.resolve({
                ok: true,
                json: async () => ({
                  workflowId: "mm:workflow-123",
                  runId: "run-123",
                  namespace: "moonmind",
                  redirectPath: "/tasks/mm:workflow-123?source=temporal",
                }),
              } as Response);
            }
            return Promise.resolve({
              ok: false,
              status: 404,
              statusText: `Unhandled fetch for ${url} ${String(init?.method || "GET")}`,
              text: async () => "Unhandled fetch",
            } as Response);
          },
        );

        renderWithClient(<TaskCreatePage payload={mockPayload} />);

        fireEvent.change(await screen.findByLabelText("Instructions"), {
          target: { value: "Large instructions ".repeat(1000) },
        });
        fireEvent.click(screen.getByRole("button", { name: "Create" }));

        await waitFor(
          () => {
            expect(completeAttempts).toBe(5);
            expect(fetchSpy).toHaveBeenCalledWith(
              "/api/executions",
              expect.objectContaining({ method: "POST" }),
            );
          },
          { timeout: 9000 },
        );
      } finally {
        ARTIFACT_COMPLETE_RETRY_DELAYS_MS.splice(
          0,
          ARTIFACT_COMPLETE_RETRY_DELAYS_MS.length,
          ...originalRetryDelays,
        );
      }
    },
    10000,
  );

  it("uploads oversized step instructions as a JSON artifact and strips inline step text", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Primary objective" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));

    const stepTextarea = await screen.findByPlaceholderText(
      "Step-specific instructions (leave blank to continue from the task objective).",
    );
    fireEvent.change(stepTextarea, {
      target: { value: "Long step instructions ".repeat(1000) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/artifacts",
        expect.objectContaining({ method: "POST" }),
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.inputArtifactRef).toBe("art-001");
    expect(request.payload.task.instructions).toBe("Primary objective");
    expect(request.payload.task.steps).toEqual([
      {
        instructions: "Primary objective",
      },
      {},
    ]);

    const uploadCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/artifacts/art-001/content")
      .at(-1);
    expect(JSON.parse(String(uploadCall?.[1]?.body))).toMatchObject({
      repository: "MoonLadderStudios/MoonMind",
      task: {
        instructions: "Primary objective",
        steps: expect.arrayContaining([
          expect.objectContaining({
            instructions: "Primary objective",
          }),
          expect.objectContaining({
            instructions: expect.stringContaining(
              "Long step instructions Long step instructions",
            ),
          }),
        ]),
      },
    });
  });

  it("applies a preset into task steps and submits them", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const presetSelect = await screen.findByLabelText("Preset");
    await waitFor(() => {
      expect(
        Array.from((presetSelect as HTMLSelectElement).options).some(
          (option) => option.text === "Spec Kit Demo (Global)",
        ),
      ).toBe(true);
    });

    fireEvent.change(presetSelect, {
      target: { value: "global::::speckit-demo" },
    });

    fireEvent.change(
      screen.getByLabelText("Feature Request / Initial Instructions"),
      {
        target: { value: "Task Create" },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: "Apply" }));

    await screen.findByDisplayValue(
      "Clarify the {{ inputs.feature_name }} scope.",
    );
    await screen.findByDisplayValue(
      "Write a plan for the task builder recovery.",
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({
          method: "POST",
        }),
      );
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.task.instructions).toBe('Task Create');
    // Address: Copilot r3034495957 — assert skills and derived title in preset-submit.
    // The first template step has an explicit skill (speckit-clarify), so effectiveSkillId
    // stays as that explicit skill rather than the template slug.
    expect(request.payload.task.skills).toEqual(['speckit-clarify']);
    expect(request.payload.task.title).toBe('Task Create');
    expect(request.payload.task.tool.name).toBe('speckit-clarify');
    expect(request.payload.task.skill.id).toBe('speckit-clarify');
    expect(request.payload.task.steps).toEqual([
      {
        id: "tpl:speckit-demo:1.2.3:01",
        title: "Clarify spec",
        instructions: "Clarify the {{ inputs.feature_name }} scope.",
        tool: {
          type: "skill",
          name: "speckit-clarify",
          version: "1.0",
          inputs: { feature: "Task Create" },
        },
        skill: {
          id: "speckit-clarify",
          args: { feature: "Task Create" },
        },
      },
      {
        id: "tpl:speckit-demo:1.2.3:02",
        title: "Plan implementation",
        instructions: "Write a plan for the task builder recovery.",
      },
    ]);
    expect(request.payload.task.appliedStepTemplates).toEqual([
      expect.objectContaining({
        slug: "speckit-demo",
        version: "1.2.3",
      }),
    ]);
  });

  it("derives the task objective from feature-request template input aliases", () => {
    expect(
      resolveObjectiveInstructions("", "", [
        {
          slug: "objective-demo",
          version: "2.0.0",
          appliedAt: "2026-04-03T00:00:00Z",
          inputs: {
            request: "Restore the legacy Create Task objective handling.",
          },
          stepIds: [],
          capabilities: [],
        },
      ]),
    ).toBe("Restore the legacy Create Task objective handling.");
  });

  it("surfaces plain-text execution errors without reading the response body twice", async () => {
    executionResponseOverride = new Response("Plaintext execution failure.", {
      status: 400,
      statusText: "Bad Request",
      headers: {
        "Content-Type": "text/plain",
      },
    });

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Run end-to-end regression flow." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(screen.getByText("Plaintext execution failure.")).not.toBeNull();
    });
  });

  it("blocks preset saves when step skill args are invalid JSON", async () => {
    const promptSpy = vi
      .spyOn(window, "prompt")
      .mockImplementationOnce(() => "Saved preset title")
      .mockImplementationOnce(() => "Saved preset description");

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    );
    expect(primaryStep).not.toBeNull();

    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText("Instructions"),
      {
        target: { value: "Capture the current draft as a preset." },
      },
    );
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "pr-resolver" },
      },
    );
    await waitFor(() => {
      expect(
        within(primaryStep as HTMLElement).getByLabelText(
          /Skill Args \(optional JSON object\)/,
        ),
      ).not.toBeNull();
    });
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(
        /Skill Args \(optional JSON object\)/,
      ),
      {
        target: { value: '{"broken":' },
      },
    );

    fireEvent.click(
      screen.getByRole("button", { name: /Save Current Steps as Preset/ }),
    );

    await waitFor(() => {
      expect(
        screen.getByText("Step 1 Skill Args must be valid JSON object text."),
      ).not.toBeNull();
    });
    expect(
      fetchSpy.mock.calls.some(
        ([url]) => String(url) === "/api/task-step-templates/save-from-task",
      ),
    ).toBe(false);

    promptSpy.mockRestore();
  });

  // -----------------------------------------------------------------------
  // Dependency picker hardening tests
  // -----------------------------------------------------------------------

  it("prevents adding the same dependency twice", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Dependent stage." },
    });

    // Add mm:dep-1 first time.
    fireEvent.change(screen.getByLabelText("Existing run"), {
      target: { value: "mm:dep-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add dependency" }));

    await waitFor(() => {
      expect(screen.getByText(/Build shared schema/)).toBeTruthy();
    });

    // After adding, mm:dep-1 should be removed from the dropdown options
    // (filtered out by availableDependencyOptions).
    const select = screen.getByLabelText("Existing run") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).not.toContain("mm:dep-1");

    // The list should have exactly ONE entry.
    const list = document.getElementById("queue-dependency-list");
    expect(list).toBeTruthy();
    expect(within(list as HTMLElement).getAllByRole("listitem")).toHaveLength(1);

    // Add a second dependency to verify the list grows.
    fireEvent.change(screen.getByLabelText("Existing run"), {
      target: { value: "mm:dep-2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add dependency" }));

    await waitFor(() => {
      expect(within(list as HTMLElement).getAllByRole("listitem")).toHaveLength(2);
    });
  });

  it("enforces the 10-item dependency limit", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Dependent stage." },
    });

    // Add 10 dependencies.
    for (let i = 1; i <= 10; i += 1) {
      fireEvent.change(screen.getByLabelText("Existing run"), {
        target: { value: `mm:dep-${i}` },
      });
      fireEvent.click(
        screen.getByRole("button", { name: "Add dependency" }),
      );
    }

    // Wait for all 10 to appear.
    await waitFor(() => {
      const list = document.getElementById("queue-dependency-list");
      expect(list).toBeTruthy();
      expect(within(list as HTMLElement).getAllByRole("listitem")).toHaveLength(10);
    });

    // Try to add an 11th.
    fireEvent.change(screen.getByLabelText("Existing run"), {
      target: { value: "mm:dep-11" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add dependency" }));

    // Still 10 items.
    const list = document.getElementById("queue-dependency-list");
    expect(list).toBeTruthy();
    expect(within(list as HTMLElement).getAllByRole("listitem")).toHaveLength(10);

    // Limit-exceeded message should be visible.
    expect(
      screen.getByText(/at most 10 direct dependencies/),
    ).toBeTruthy();
  });

  it("shows validation message when adding dependency without selection", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Dependent stage." },
    });

    // Click Add without selecting a run.
    fireEvent.click(screen.getByRole("button", { name: "Add dependency" }));

    await waitFor(() => {
      expect(
        screen.getByText(/Choose a prerequisite run before adding/),
      ).toBeTruthy();
    });
  });

  it("hides Jira browser controls when the runtime config does not enable Jira", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(await screen.findByText("Step 1 (Primary)")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /Browse Jira story/ }),
    ).toBeNull();
  });

  it("opens the Jira browser from the preset target", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    expect(
      await screen.findByRole("dialog", { name: "Browse Jira story" }),
    ).toBeTruthy();
    expect(screen.getByText("Target: Feature Request / Initial Instructions"))
      .toBeTruthy();
  });

  it("opens the Jira browser from a step target", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for Step 1 instructions",
      }),
    );

    expect(
      await screen.findByRole("dialog", { name: "Browse Jira story" }),
    ).toBeTruthy();
    expect(screen.getByText("Target: Step 1 Instructions")).toBeTruthy();
  });

  it("loads board columns in order and switches visible Jira issues by column", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "To Do 1" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "Doing 1" })).toBeTruthy();
    });

    expect(screen.getByText("ENG-101")).toBeTruthy();
    expect(screen.queryByText("ENG-202")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Doing 1" }));

    expect(await screen.findByText("ENG-202")).toBeTruthy();
    expect(screen.queryByText("ENG-101")).toBeNull();
  });

  it("keeps the Jira browser disabled when endpoint templates are incomplete", async () => {
    const payload = withJiraIntegration();
    const initialData = payload.initialData as {
      dashboardConfig: {
        sources?: Record<string, unknown>;
      };
    };
    renderWithClient(
      <TaskCreatePage
        payload={{
          ...payload,
          initialData: {
            ...initialData,
            dashboardConfig: {
              ...initialData.dashboardConfig,
              sources: {
                ...initialData.dashboardConfig.sources,
                jira: {
                  projects: "/api/jira/projects",
                },
              },
            },
          },
        }}
      />,
    );

    expect(await screen.findByText("Step 1 (Primary)")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /Browse Jira story/ }),
    ).toBeNull();
  });

  it("does not restore Jira project or board defaults after a manual clear", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    const projectSelect = await screen.findByLabelText("Project");
    await waitFor(() => {
      expect((projectSelect as HTMLSelectElement).value).toBe("ENG");
    });
    fireEvent.change(projectSelect, { target: { value: "" } });

    await waitFor(() => {
      expect((projectSelect as HTMLSelectElement).value).toBe("");
    });
    expect(
      (screen.getByLabelText("Board") as HTMLSelectElement).value,
    ).toBe("");

    fireEvent.change(projectSelect, { target: { value: "ENG" } });
    const boardSelect = screen.getByLabelText("Board");
    await waitFor(() => {
      expect((boardSelect as HTMLSelectElement).value).toBe("42");
    });
    fireEvent.change(boardSelect, { target: { value: "" } });

    await waitFor(() => {
      expect((boardSelect as HTMLSelectElement).value).toBe("");
    });
  });

  it("loads Jira issue preview when an issue is selected", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));

    expect(await screen.findByText("Build browser shell")).toBeTruthy();
    expect(
      await screen.findByText("Let operators browse Jira stories."),
    ).toBeTruthy();
    expect(
      screen.getByText("Given a board, users can select a story preview."),
    ).toBeTruthy();
  });

  it("does not import Jira preview text into draft fields in this phase", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const stepInstructions = await screen.findByLabelText("Instructions");
    const presetInstructions = screen.getByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.change(stepInstructions, {
      target: { value: "Keep existing step instructions." },
    });
    fireEvent.change(presetInstructions, {
      target: { value: "Keep existing preset instructions." },
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));

    expect(await screen.findByText("Build browser shell")).toBeTruthy();
    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing step instructions.",
    );
    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing preset instructions.",
    );
  });

  it("shows Jira browser failures locally", async () => {
    fetchSpy.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/jira/projects") {
          return Promise.resolve({
            ok: false,
            status: 503,
            statusText: "Service Unavailable",
            text: async () => "Jira unavailable",
          } as Response);
        }
        if (url.startsWith("/api/tasks/skills")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ items: { worker: [] } }),
          } as Response);
        }
        if (url.startsWith("/api/task-step-templates")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ items: [] }),
          } as Response);
        }
        return Promise.resolve({
          ok: false,
          status: 404,
          statusText: `Unhandled fetch for ${url} ${String(init?.method || "GET")}`,
          text: async () => "Unhandled fetch",
        } as Response);
      },
    );
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );

    expect(await screen.findByText("Failed to load Jira projects.")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Browse Jira story" }))
      .toBeTruthy();
    expect(screen.getByLabelText("Instructions")).toBeTruthy();
  });

  it("keeps manual task creation available after Jira browser failure", async () => {
    fetchSpy.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/jira/projects") {
          return Promise.resolve({
            ok: false,
            status: 503,
            statusText: "Service Unavailable",
            text: async () => "Jira unavailable",
          } as Response);
        }
        if (url === "/api/executions") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ workflowId: "mm:workflow-123" }),
          } as Response);
        }
        if (url.startsWith("/api/tasks/skills")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ items: { worker: [] } }),
          } as Response);
        }
        if (url.startsWith("/api/task-step-templates")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ items: [] }),
          } as Response);
        }
        return Promise.resolve({
          ok: false,
          status: 404,
          statusText: `Unhandled fetch for ${url} ${String(init?.method || "GET")}`,
          text: async () => "Unhandled fetch",
        } as Response);
      },
    );
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira story for preset instructions",
      }),
    );
    expect(await screen.findByText("Failed to load Jira projects.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Close Jira browser" }));
    fireEvent.change(screen.getByLabelText("Instructions"), {
      target: { value: "Create this task manually." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });
});
