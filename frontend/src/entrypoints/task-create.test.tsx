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
  buildTemporalArtifactEditUpdatePayload,
  buildTemporalSubmissionDraftFromExecution,
  resolveTaskSubmitPageMode,
} from "../lib/temporalTaskEditing";
import { renderWithClient } from "../utils/test-utils";
import {
  ARTIFACT_COMPLETE_RETRY_DELAYS_MS,
  preferredTemplate,
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
        github: {
          branches: "/api/github/branches?repository={repository}",
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

function withAttachmentPolicy(payload: BootPayload = mockPayload): BootPayload {
  const initialData = payload.initialData as {
    dashboardConfig: {
      system?: Record<string, unknown>;
    };
  };
  return {
    ...payload,
    initialData: {
      ...initialData,
      dashboardConfig: {
        ...initialData.dashboardConfig,
        system: {
          ...initialData.dashboardConfig.system,
          attachmentPolicy: {
            enabled: true,
            maxCount: 4,
            maxBytes: 1024 * 1024,
            totalBytes: 2 * 1024 * 1024,
            allowedContentTypes: ["image/png", "application/pdf"],
          },
        },
      },
    },
  };
}

function withRepositoryOptions(payload: BootPayload = mockPayload): BootPayload {
  const initialData = payload.initialData as {
    dashboardConfig: {
      system?: Record<string, unknown>;
    };
  };
  return {
    ...payload,
    initialData: {
      ...initialData,
      dashboardConfig: {
        ...initialData.dashboardConfig,
        system: {
          ...initialData.dashboardConfig.system,
          repositoryOptions: {
            items: [
              {
                value: "MoonLadderStudios/MoonMind",
                label: "MoonLadderStudios/MoonMind",
                source: "default",
              },
              {
                value: "Octo/Repo",
                label: "Octo/Repo",
                source: "github",
              },
            ],
            error: null,
          },
        },
      },
    },
  };
}

function withDefaultRepository(
  defaultRepository: string,
  payload: BootPayload = mockPayload,
): BootPayload {
  const initialData = payload.initialData as {
    dashboardConfig: {
      system?: Record<string, unknown>;
    };
  };
  return {
    ...payload,
    initialData: {
      ...initialData,
      dashboardConfig: {
        ...initialData.dashboardConfig,
        system: {
          ...initialData.dashboardConfig.system,
          defaultRepository,
        },
      },
    },
  };
}

function withImageOnlyAttachmentPolicy(
  payload: BootPayload = mockPayload,
): BootPayload {
  const initialData = payload.initialData as {
    dashboardConfig: {
      system?: Record<string, unknown>;
    };
  };
  return {
    ...payload,
    initialData: {
      ...initialData,
      dashboardConfig: {
        ...initialData.dashboardConfig,
        system: {
          ...initialData.dashboardConfig.system,
          attachmentPolicy: {
            enabled: true,
            maxCount: 4,
            maxBytes: 1024 * 1024,
            totalBytes: 2 * 1024 * 1024,
            allowedContentTypes: ["image/png", "image/jpeg", "image/webp"],
          },
        },
      },
    },
  };
}

function withDisabledAttachmentPolicy(
  payload: BootPayload = mockPayload,
): BootPayload {
  const initialData = payload.initialData as {
    dashboardConfig: {
      system?: Record<string, unknown>;
    };
  };
  return {
    ...payload,
    initialData: {
      ...initialData,
      dashboardConfig: {
        ...initialData.dashboardConfig,
        system: {
          ...initialData.dashboardConfig.system,
          attachmentPolicy: {
            enabled: false,
            maxCount: 4,
            maxBytes: 1024 * 1024,
            totalBytes: 2 * 1024 * 1024,
            allowedContentTypes: ["image/png", "image/jpeg", "image/webp"],
          },
        },
      },
    },
  };
}

function withoutDefaultRepository(payload: BootPayload = mockPayload): BootPayload {
  const initialData = payload.initialData as {
    dashboardConfig: {
      system?: Record<string, unknown>;
    };
  };
  return {
    ...payload,
    initialData: {
      ...initialData,
      dashboardConfig: {
        ...initialData.dashboardConfig,
        system: {
          ...initialData.dashboardConfig.system,
          defaultRepository: "",
        },
      },
    },
  };
}

function withoutOptionalAuthoringIntegrations(
  payload: BootPayload = mockPayload,
): BootPayload {
  const initialData = payload.initialData as {
    dashboardConfig: {
      sources?: Record<string, unknown>;
      system?: Record<string, unknown>;
    };
  };
  const { jira: _jiraSources, ...sources } =
    initialData.dashboardConfig.sources || {};
  const {
    jiraIntegration: _jiraIntegration,
    attachmentPolicy: _attachmentPolicy,
    taskTemplateCatalog: _taskTemplateCatalog,
    ...system
  } = initialData.dashboardConfig.system || {};
  return {
    ...payload,
    initialData: {
      ...initialData,
      dashboardConfig: {
        ...initialData.dashboardConfig,
        sources,
        system,
      },
    },
  };
}

function withJiraSessionMemory(
  rememberLastBoardInSession: boolean,
  payload: BootPayload = mockPayload,
): BootPayload {
  const nextPayload = withJiraIntegration(payload);
  const initialData = nextPayload.initialData as {
    dashboardConfig: {
      system?: {
        jiraIntegration?: Record<string, unknown>;
      };
    };
  };
  return {
    ...nextPayload,
    initialData: {
      ...initialData,
      dashboardConfig: {
        ...initialData.dashboardConfig,
        system: {
          ...initialData.dashboardConfig.system,
          jiraIntegration: {
            ...initialData.dashboardConfig.system?.jiraIntegration,
            rememberLastBoardInSession,
          },
        },
      },
    },
  };
}

function collectObjectKeys(value: unknown, keys = new Set<string>()): Set<string> {
  if (!value || typeof value !== "object") {
    return keys;
  }
  if (Array.isArray(value)) {
    value.forEach((item) => collectObjectKeys(item, keys));
    return keys;
  }
  Object.entries(value as Record<string, unknown>).forEach(([key, nested]) => {
    keys.add(key);
    collectObjectKeys(nested, keys);
  });
  return keys;
}

describe("Task Create Entrypoint", () => {
  let fetchSpy: MockInstance;
  let consoleInfoSpy: MockInstance;
  let executionResponseOverride: Response | null;
  let artifactCreateResponseOverride: Response | null;

  function renderForEdit(executionId: string, payload: BootPayload = mockPayload) {
    window.history.pushState(
      {},
      "Task Edit",
      `/tasks/new?editExecutionId=${encodeURIComponent(executionId)}`,
    );
    return renderWithClient(<TaskCreatePage payload={payload} />);
  }

  function latestCreateRequest(): Record<string, unknown> {
    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    return JSON.parse(String(executionCall?.[1]?.body)) as Record<
      string,
      unknown
    >;
  }

  function canonicalCreateSections(): string[] {
    return Array.from(
      document.querySelectorAll<HTMLElement>("[data-canonical-create-section]"),
    ).map((element) => element.dataset.canonicalCreateSection || "");
  }

  function expectAllButtonsHaveTitles(container: ParentNode = document): void {
    const missingTitles = Array.from(
      container.querySelectorAll<HTMLButtonElement>("button"),
    )
      .filter((button) => !button.getAttribute("title")?.trim())
      .map(
        (button) =>
          button.getAttribute("aria-label") || button.textContent?.trim() || "",
      );

    expect(missingTitles).toEqual([]);
  }

  beforeEach(() => {
    window.history.pushState({}, "Task Create", "/tasks/new");
    window.sessionStorage.clear();
    vi.mocked(navigateTo).mockReset();
    consoleInfoSpy = vi.spyOn(console, "info").mockImplementation(() => {});
    executionResponseOverride = null;
    artifactCreateResponseOverride = null;
    fetchSpy = vi
      .spyOn(window, "fetch")
      .mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const path = url.split("?")[0];
        if (url.startsWith("/api/tasks/skills")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: { worker: ["speckit-orchestrate", "pr-resolver"] },
            }),
          } as Response);
        }
        if (url.startsWith("/api/github/branches")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                { value: "main", label: "main", source: "github" },
                {
                  value: "feature/create-page",
                  label: "feature/create-page",
                  source: "github",
                },
              ],
              error: null,
            }),
          } as Response);
        }
        if (url.startsWith("/api/task-step-templates?scope=personal")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                {
                  slug: "personal-demo",
                  scope: "personal",
                  title: "Personal Demo",
                  description: "A user-owned preset.",
                  latestVersion: "1.0.0",
                  version: "1.0.0",
                },
              ],
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
                {
                  slug: "pr-resolver",
                  scope: "global",
                  title: "PR Resolver",
                  description: "Resolve pull request checks and feedback.",
                  latestVersion: "1.0.0",
                  version: "1.0.0",
                },
              ],
            }),
          } as Response);
        }
        if (
          path === "/api/task-step-templates/speckit-demo" &&
          init?.method === "DELETE"
        ) {
          return Promise.resolve({
            ok: true,
            status: 204,
            text: async () => "",
          } as Response);
        }
        if (
          path === "/api/task-step-templates/personal-demo" &&
          init?.method === "DELETE"
        ) {
          return Promise.resolve({
            ok: true,
            status: 204,
            text: async () => "",
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
        if (url.startsWith("/api/task-step-templates/pr-resolver?scope=global")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              slug: "pr-resolver",
              scope: "global",
              title: "PR Resolver",
              description: "Resolve pull request checks and feedback.",
              latestVersion: "1.0.0",
              version: "1.0.0",
              inputs: [],
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
        if (
          url.startsWith(
            "/api/task-step-templates/pr-resolver:expand?scope=global",
          )
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              steps: [
                {
                  id: "tpl:pr-resolver:1.0.0:01",
                  title: "Resolve PR",
                  instructions: "Resolve the current branch PR.",
                },
              ],
              appliedTemplate: {
                slug: "pr-resolver",
                version: "1.0.0",
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
                operatorNote: "Keep this unedited top-level value.",
                startingBranch: "stale-legacy-source",
                targetBranch: "stale-legacy-target",
                task: {
                  instructions: "Rebuild the Temporal task draft.",
                  proposeTasks: true,
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
        if (url === "/api/executions/mm%3Amerge-automation-edit?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:merge-automation-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "codex_cli",
              profileId: "profile:codex-default",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              publishMode: "pr",
              inputParameters: {
                targetRuntime: "codex_cli",
                mergeAutomation: { enabled: true },
                task: {
                  instructions: "Update an existing automated PR task.",
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                    profileId: "profile:codex-default",
                  },
                  publish: { mode: "pr" },
                },
              },
              actions: {
                canUpdateInputs: true,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Amulti-step-edit?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:multi-step-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "codex_cli",
              profileId: "profile:codex-default",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              startingBranch: "main",
              targetBranch: "mm-340-edit-task-all-steps",
              publishMode: "pr",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  instructions: "Investigate why edit shows only step 1.",
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                    profileId: "profile:codex-default",
                  },
                  git: {
                    startingBranch: "main",
                    targetBranch: "mm-340-edit-task-all-steps",
                  },
                  publish: { mode: "pr" },
                  steps: [
                    {
                      id: "step-primary",
                      title: "Investigate",
                      instructions: "Investigate why edit shows only step 1.",
                      skill: {
                        id: "moonspec-orchestrate",
                        args: { mode: "runtime" },
                        requiredCapabilities: ["git"],
                      },
                    },
                    {
                      id: "step-patch",
                      title: "Patch",
                      instructions: "Patch the edit reconstruction path.",
                    },
                    {
                      id: "step-verify",
                      title: "Verify",
                      instructions: "Run the focused task-create tests.",
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
        if (url === "/api/executions/mm%3Aauto-primary-skill?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:auto-primary-skill",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "codex_cli",
              targetSkill: "moonspec-orchestrate",
              inputParameters: {
                task: {
                  instructions: "Preserve the target skill.",
                  steps: [
                    {
                      instructions: "Preserve the target skill.",
                      skill: { id: "auto" },
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
        if (url === "/api/executions/mm%3Aartifact-edit?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:artifact-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
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
                canUpdateInputs: true,
                canRerun: false,
              },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aattachment-edit?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:attachment-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "codex_cli",
              profileId: "profile:codex-secondary",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              inputArtifactRef: "attachment-snapshot",
              taskInputSnapshot: {
                available: true,
                artifactRef: "attachment-snapshot",
                snapshotVersion: 1,
                sourceKind: "create",
                reconstructionMode: "authoritative",
                disabledReasons: {},
                fallbackEvidenceRefs: [],
              },
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
        if (url === "/api/executions/mm%3Astale-edit?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:stale-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
              inputParameters: {
                task: { instructions: "Editable before submit." },
              },
              actions: {
                canUpdateInputs: true,
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
        if (url === "/api/executions/mm%3Astale-rerun?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:stale-rerun",
              workflowType: "MoonMind.Run",
              state: "completed",
              targetRuntime: "codex_cli",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  instructions: "Rerunnable before submit.",
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
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
        if (
          url ===
          "/gateway/api/executions/mm%3Acustom-edit?view=detail&source=temporal"
        ) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:custom-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "codex_cli",
              profileId: "profile:codex-default",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  instructions: "Save through configured endpoint.",
                  runtime: {
                    mode: "codex_cli",
                    model: "gpt-5.4",
                    effort: "medium",
                    profileId: "profile:codex-default",
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
        if (url === "/api/executions/mm%3Aedit-123/update") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "immediate",
              message: "Inputs updated.",
              execution: { workflowId: "mm:edit-123" },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Amerge-automation-edit/update") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "immediate",
              message: "Inputs updated.",
              execution: { workflowId: "mm:merge-automation-edit" },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Amulti-step-edit/update") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "immediate",
              message: "Inputs updated.",
              execution: { workflowId: "mm:multi-step-edit" },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aartifact-edit/update") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "next_safe_point",
              message: "Inputs scheduled.",
              execution: { workflowId: "mm:artifact-edit" },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aattachment-edit/update") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "immediate",
              message: "Inputs updated.",
              execution: { workflowId: "mm:attachment-edit" },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Arerun-123/update") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "continue_as_new",
              message: "Rerun requested. New execution created.",
              execution: { workflowId: "mm:rerun-created" },
              continueAsNewCause: "manual_rerun",
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Astale-rerun/update") {
          return Promise.resolve({
            ok: false,
            status: 422,
            text: async () =>
              JSON.stringify({
                detail: {
                  code: "invalid_update_request",
                  message:
                    "Workflow state changed and rerun is no longer available.",
                },
              }),
          } as Response);
        }
        if (url === "/gateway/api/executions/mm%3Acustom-edit/updates") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "immediate",
              execution: { workflowId: "mm:custom-edit" },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Acontinue-edit?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:continue-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "codex_cli",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  instructions: "Continue-as-new editable input.",
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
        if (url === "/api/executions/mm%3Acontinue-edit/update") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "continue_as_new",
              message: "Inputs accepted for a refreshed run.",
              execution: { workflowId: "mm:continue-edit-next" },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Avalidation-edit?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:validation-edit",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "codex_cli",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  instructions: "Editable input with backend validation.",
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
        if (url === "/api/executions/mm%3Avalidation-edit/update") {
          return Promise.resolve({
            ok: false,
            status: 422,
            text: async () =>
              JSON.stringify({
                detail: {
                  code: "invalid_update_request",
                  message: "Task input validation failed: target branch is invalid.",
                },
              }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Aartifact-failure?source=temporal") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              workflowId: "mm:artifact-failure",
              workflowType: "MoonMind.Run",
              state: "executing",
              targetRuntime: "codex_cli",
              model: "gpt-5.4",
              effort: "medium",
              repository: "MoonLadderStudios/MoonMind",
              inputArtifactRef: "historical-input",
              inputParameters: {
                targetRuntime: "codex_cli",
                task: {
                  instructions: "Artifact-backed editable input.",
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
        if (url === "/api/executions/mm%3Aartifact-failure/update") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              accepted: true,
              applied: "immediate",
              execution: { workflowId: "mm:artifact-failure" },
            }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Ano-edit/update") {
          return Promise.resolve({
            ok: false,
            status: 422,
            text: async () =>
              JSON.stringify({
                detail: {
                  code: "invalid_update_request",
                  message:
                    "Workflow is in a terminal state and no longer accepts updates.",
                },
              }),
          } as Response);
        }
        if (url === "/api/executions/mm%3Astale-edit/update") {
          return Promise.resolve({
            ok: false,
            status: 422,
            text: async () =>
              JSON.stringify({
                detail: {
                  code: "invalid_update_request",
                  message:
                    "Workflow is in a terminal state and no longer accepts updates.",
                },
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
          if (artifactCreateResponseOverride) {
            return Promise.resolve(artifactCreateResponseOverride);
          }
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
        if (url === "/api/artifacts/attachment-snapshot/download") {
          return Promise.resolve({
            ok: true,
            text: async () =>
              JSON.stringify({
                snapshotVersion: 1,
                source: { kind: "create" },
                draft: {
                  taskShape: "multi_step",
                  task: {
                    instructions: "Preserve the existing attachments.",
                    inputAttachments: [
                      {
                        artifactId: "art-objective",
                        filename: "objective.png",
                        contentType: "image/png",
                        sizeBytes: 1234,
                      },
                    ],
                    runtime: {
                      mode: "codex_cli",
                      model: "gpt-5.4",
                      effort: "medium",
                      profileId: "profile:codex-secondary",
                    },
                    steps: [
                      {
                        id: "step-with-attachment",
                        title: "Attached step",
                        instructions: "Preserve the existing attachments.",
                        inputAttachments: [
                          {
                            artifactId: "art-step",
                            filename: "step.webp",
                            contentType: "image/webp",
                            sizeBytes: 4567,
                          },
                        ],
                      },
                    ],
                  },
                },
                attachmentRefs: [
                  {
                    artifactId: "art-objective",
                    filename: "objective.png",
                    contentType: "image/png",
                    sizeBytes: 1234,
                    targetKind: "objective",
                  },
                  {
                    artifactId: "art-step",
                    filename: "step.webp",
                    contentType: "image/webp",
                    sizeBytes: 4567,
                    targetKind: "step",
                    stepId: "step-with-attachment",
                    stepOrdinal: 0,
                  },
                ],
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
                { projectKey: "OPS", name: "Operations" },
                { projectKey: "ENG", name: "Engineering" },
                { projectKey: "MY-PROJ", name: "Hyphenated Project" },
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
        if (url === "/api/jira/projects/MY-PROJ/boards") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                { id: "84", name: "Hyphenated Delivery", projectKey: "MY-PROJ" },
              ],
            }),
          } as Response);
        }
        if (path === "/api/jira/boards/42/columns") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              board: { id: "42", name: "Delivery", projectKey: "ENG" },
              columns: [
                { id: "todo", name: "To Do", count: 0 },
                { id: "doing", name: "Doing", count: 0 },
              ],
            }),
          } as Response);
        }
        if (path === "/api/jira/boards/84/columns") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              board: { id: "84", name: "Hyphenated Delivery", projectKey: "MY-PROJ" },
              columns: [{ id: "selected", name: "Selected", count: 1 }],
            }),
          } as Response);
        }
        if (path === "/api/jira/boards/42/issues") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              boardId: "42",
              columns: [
                { id: "todo", name: "To Do", count: 1 },
                { id: "doing", name: "Doing", count: 1 },
              ],
              itemsByColumn: {
                todo: [
                  {
                    issueKey: "ENG-101",
                    summary: "Plan controls",
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
        if (path === "/api/jira/boards/84/issues") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              boardId: "84",
              columns: [{ id: "selected", name: "Selected" }],
              itemsByColumn: {
                selected: [
                  {
                    issueKey: "MY-PROJ-123",
                    summary: "Handle hyphenated project keys",
                    issueType: "Story",
                    statusName: "Selected",
                    assignee: "Lin",
                    updatedAt: "2026-04-12T19:30:00Z",
                  },
                ],
              },
            }),
          } as Response);
        }
        if (path === "/api/jira/issues/ENG-202") {
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
                  "Complete Jira issue ENG-202: Build browser shell",
              },
            }),
          } as Response);
        }
        if (path === "/api/jira/issues/MY-PROJ-123") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              issueKey: "MY-PROJ-123",
              url: "https://jira.example.test/browse/MY-PROJ-123",
              summary: "Handle hyphenated project keys",
              issueType: "Story",
              column: { id: "selected", name: "Selected" },
              status: { id: "1", name: "Selected" },
              descriptionText: "Keep the full Jira project key.",
              acceptanceCriteriaText:
                "Given a hyphenated project key, reopening keeps the project selected.",
              recommendedImports: {
                presetInstructions:
                  "MY-PROJ-123: Handle hyphenated project keys\n\nKeep the full Jira project key.",
                stepInstructions:
                  "Complete Jira issue MY-PROJ-123: Handle hyphenated project keys",
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

  it("prefers the seeded Jira Orchestrate template over MoonSpec and legacy SpecKit rows", () => {
    const preferred = preferredTemplate([
      {
        key: "global::::jira-orchestrate",
        slug: "jira-orchestrate",
        scope: "global",
        title: "Jira Orchestrate",
        description: "Jira preset.",
        latestVersion: "1.0.0",
        version: "1.0.0",
      },
      {
        key: "global::::speckit-orchestrate",
        slug: "speckit-orchestrate",
        scope: "global",
        title: "SpecKit Orchestrate",
        description: "Legacy preset.",
        latestVersion: "1.0.0",
        version: "1.0.0",
      },
      {
        key: "global::::moonspec-orchestrate",
        slug: "moonspec-orchestrate",
        scope: "global",
        title: "MoonSpec Orchestrate",
        description: "MoonSpec preset.",
        latestVersion: "1.0.0",
        version: "1.0.0",
      },
    ]);

    expect(preferred?.slug).toBe("jira-orchestrate");
    expect(preferred?.scope).toBe("global");
  });

  it("falls back to the seeded MoonSpec orchestrate template when Jira Orchestrate is absent", () => {
    const preferred = preferredTemplate([
      {
        key: "global::::speckit-orchestrate",
        slug: "speckit-orchestrate",
        scope: "global",
        title: "SpecKit Orchestrate",
        description: "Legacy preset.",
        latestVersion: "1.0.0",
        version: "1.0.0",
      },
      {
        key: "global::::moonspec-orchestrate",
        slug: "moonspec-orchestrate",
        scope: "global",
        title: "MoonSpec Orchestrate",
        description: "MoonSpec preset.",
        latestVersion: "1.0.0",
        version: "1.0.0",
      },
    ]);

    expect(preferred?.slug).toBe("moonspec-orchestrate");
    expect(preferred?.scope).toBe("global");
  });

  it("falls back to the legacy SpecKit orchestrate template when MoonSpec is absent", () => {
    const preferred = preferredTemplate([
      {
        key: "global::::speckit-orchestrate",
        slug: "speckit-orchestrate",
        scope: "global",
        title: "SpecKit Orchestrate",
        description: "Legacy preset.",
        latestVersion: "1.0.0",
        version: "1.0.0",
      },
      {
        key: "global::::other-template",
        slug: "other-template",
        scope: "global",
        title: "Other Template",
        description: "Other preset.",
        latestVersion: "1.0.0",
        version: "1.0.0",
      },
    ]);

    expect(preferred?.slug).toBe("speckit-orchestrate");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    consoleInfoSpy.mockRestore();
    window.sessionStorage.clear();
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

  it("builds the canonical Temporal UpdateInputs payload without mutating historical artifacts", () => {
    expect(
      buildTemporalArtifactEditUpdatePayload({
        updateName: "UpdateInputs",
        inputArtifactRef: "art-edited-input",
        parametersPatch: {
          repository: "MoonLadderStudios/MoonMind",
          task: { instructions: "Edited instructions." },
        },
      }),
    ).toEqual({
      updateName: "UpdateInputs",
      inputArtifactRef: "art-edited-input",
      parametersPatch: {
        repository: "MoonLadderStudios/MoonMind",
        task: { instructions: "Edited instructions." },
      },
    });
  });

  it("builds the canonical Temporal RequestRerun payload with replacement input lineage", () => {
    expect(
      buildTemporalArtifactEditUpdatePayload({
        updateName: "RequestRerun",
        inputArtifactRef: "art-rerun-input",
        parametersPatch: {
          repository: "MoonLadderStudios/MoonMind",
          task: { instructions: "Rerun with reviewed instructions." },
        },
      }),
    ).toEqual({
      updateName: "RequestRerun",
      inputArtifactRef: "art-rerun-input",
      parametersPatch: {
        repository: "MoonLadderStudios/MoonMind",
        task: { instructions: "Rerun with reviewed instructions." },
      },
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

  it("renders the create submit action as an icon-only right-pointing arrow with a stable label", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const createButton = await screen.findByRole("button", { name: "Create" });
    const arrow = createButton.querySelector<HTMLElement>(
      "[data-submit-arrow='right']",
    );

    expect(arrow).not.toBeNull();
    expect(arrow?.getAttribute("aria-hidden")).toBe("true");
    const arrowIcon = arrow?.querySelector("svg");
    expect(arrowIcon).not.toBeNull();
    expect(arrowIcon?.getAttribute("aria-hidden")).toBe("true");
    expect(arrowIcon?.getAttribute("focusable")).toBe("false");
    expect(createButton.classList.contains("queue-submit-primary")).toBe(true);
    expect(createButton.classList.contains("queue-submit-primary--icon")).toBe(
      true,
    );
    expect(createButton.getAttribute("aria-label")).toBe("Create");
    expect(createButton.getAttribute("title")).toBe("Create this task");
    expect(createButton.textContent?.trim()).toBe("");
  });

  it("shows the primary step requirement only after submit validation fails", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(await screen.findByText("Step 1 (Primary)")).toBeTruthy();
    expect(
      screen.queryByText("Primary step must include instructions or an explicit skill."),
    ).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    const createButton = screen.getByRole("button", { name: "Create" });
    expect(createButton.querySelector("[data-submit-arrow='right']")).not.toBeNull();
    expect(createButton.getAttribute("aria-busy")).toBe("false");
    expect(
      await screen.findByText(
        "Primary step must include instructions or an explicit skill.",
      ),
    ).toBeTruthy();
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url) === "/api/executions"),
    ).toBe(false);
  });

  it("keeps create page authoring controls available with the arrow submit action", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const createButton = await screen.findByRole("button", { name: "Create" });
    expect(createButton.querySelector("[data-submit-arrow='right']")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Add Step" })).toBeTruthy();
    expect(screen.getByLabelText("Runtime")).toBeTruthy();
    expect(screen.getByLabelText("Publish Mode")).toBeTruthy();
    expect(canonicalCreateSections()).toEqual(
      expect.arrayContaining(["Task Presets", "Dependencies"]),
    );
    expect(
      screen.getByRole("button", {
        name: "Browse Jira issues for Step 1 instructions",
      }),
    ).toBeTruthy();
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
      branch: "main",
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
      branch: "main",
      legacyBranchWarning: null,
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

  it("reconstructs pr publish with merge automation into draft state", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:merge-edit",
      workflowType: "MoonMind.Run",
      targetRuntime: "codex_cli",
      repository: "MoonLadderStudios/MoonMind",
      publishMode: "pr",
      inputParameters: {
        mergeAutomation: { enabled: true },
        task: {
          instructions: "Preserve PR merge automation state.",
          publish: { mode: "pr" },
        },
      },
    });

    expect(draft.publishMode).toBe("pr");
    expect(draft.mergeAutomationEnabled).toBe(true);
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
      branch: "main",
      legacyBranchWarning: null,
      publishMode: "pr",
      taskInstructions: "Rerun from immutable artifact input.",
      primarySkill: "speckit-orchestrate",
    });
  });

  it("reconstructs persisted objective and step attachments from the authoritative task snapshot", () => {
    const draft = buildTemporalSubmissionDraftFromExecution(
      {
        workflowId: "mm:attachment-snapshot",
        workflowType: "MoonMind.Run",
        taskInputSnapshot: {
          available: true,
          artifactRef: "art-snapshot",
          snapshotVersion: 1,
          sourceKind: "create",
          reconstructionMode: "authoritative",
          disabledReasons: {},
          fallbackEvidenceRefs: [],
        },
        inputParameters: {
          task: {
            instructions: "Fallback inline instructions.",
          },
        },
      },
      {
        snapshotVersion: 1,
        source: { kind: "create" },
        draft: {
          taskShape: "multi_step",
          task: {
            instructions: "Preserve attachments from the snapshot.",
            inputAttachments: [
              {
                artifactId: "art-objective",
                filename: "objective.png",
                contentType: "image/png",
                sizeBytes: 1234,
              },
            ],
            steps: [
              {
                id: "step-with-attachment",
                title: "Attached step",
                instructions: "Use the step attachment.",
                inputAttachments: [
                  {
                    artifactId: "art-step",
                    filename: "step.webp",
                    contentType: "image/webp",
                    sizeBytes: 4567,
                  },
                ],
              },
            ],
          },
        },
        attachmentRefs: [
          {
            artifactId: "art-objective",
            filename: "objective.png",
            contentType: "image/png",
            sizeBytes: 1234,
            targetKind: "objective",
          },
          {
            artifactId: "art-step",
            filename: "step.webp",
            contentType: "image/webp",
            sizeBytes: 4567,
            targetKind: "step",
            stepId: "step-with-attachment",
            stepOrdinal: 0,
          },
        ],
      },
    );

    expect(draft.taskInstructions).toBe(
      [
        "Preserve attachments from the snapshot.",
        "Use the step attachment.",
      ].join("\n\n"),
    );
    expect(draft.inputAttachments).toEqual([
      {
        artifactId: "art-objective",
        filename: "objective.png",
        contentType: "image/png",
        sizeBytes: 1234,
      },
    ]);
    expect(draft.steps).toEqual([
      expect.objectContaining({
        id: "step-with-attachment",
        inputAttachments: [
          {
            artifactId: "art-step",
            filename: "step.webp",
            contentType: "image/webp",
            sizeBytes: 4567,
          },
        ],
      }),
    ]);
  });

  it("fails reconstruction when compact attachment refs cannot be bound from the task snapshot", () => {
    expect(() =>
      buildTemporalSubmissionDraftFromExecution(
        {
          workflowId: "mm:unbound-attachments",
          workflowType: "MoonMind.Run",
          taskInputSnapshot: {
            available: true,
            artifactRef: "art-snapshot",
            snapshotVersion: 1,
            sourceKind: "create",
            reconstructionMode: "authoritative",
            disabledReasons: {},
            fallbackEvidenceRefs: [],
          },
          inputParameters: {
            task: {},
          },
        },
        {
          snapshotVersion: 1,
          source: { kind: "create" },
          draft: {
            taskShape: "inline_instructions",
            task: {
              instructions: "This snapshot lost structured attachment bindings.",
            },
          },
          attachmentRefs: [
            {
              artifactId: "art-lost",
              filename: "lost.png",
              contentType: "image/png",
              sizeBytes: 100,
              targetKind: "step",
              stepOrdinal: 0,
            },
          ],
        },
      ),
    ).toThrow("Attachment bindings could not be reconstructed from this execution.");
  });

  it("fails reconstruction when a repeated artifact loses one target binding", () => {
    expect(() =>
      buildTemporalSubmissionDraftFromExecution(
        {
          workflowId: "mm:duplicate-artifact-binding",
          workflowType: "MoonMind.Run",
          taskInputSnapshot: {
            available: true,
            artifactRef: "art-snapshot",
            snapshotVersion: 1,
            sourceKind: "create",
            reconstructionMode: "authoritative",
            disabledReasons: {},
            fallbackEvidenceRefs: [],
          },
          inputParameters: {
            task: {},
          },
        },
        {
          snapshotVersion: 1,
          source: { kind: "create" },
          draft: {
            taskShape: "multi_step",
            task: {
              instructions: "The artifact is still objective-scoped.",
              inputAttachments: [
                {
                  artifactId: "art-shared",
                  filename: "shared.png",
                  contentType: "image/png",
                  sizeBytes: 100,
                },
              ],
            },
          },
          attachmentRefs: [
            {
              artifactId: "art-shared",
              filename: "shared.png",
              contentType: "image/png",
              sizeBytes: 100,
              targetKind: "objective",
            },
            {
              artifactId: "art-shared",
              filename: "shared.png",
              contentType: "image/png",
              sizeBytes: 100,
              targetKind: "step",
              stepId: "missing-step-binding",
              stepOrdinal: 0,
            },
          ],
        },
      ),
    ).toThrow("Attachment bindings could not be reconstructed from this execution.");
  });

  it("reconstructs primary skill from object-shaped task skill selectors", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:skill-selectors",
      workflowType: "MoonMind.Run",
      inputParameters: {
        task: {
          instructions: "Resolve the selected PR.",
          skills: {
            include: [{ name: "pr-resolver" }],
          },
        },
      },
    });

    expect(draft.primarySkill).toBe("pr-resolver");
  });

  it("reconstructs a skill-only task snapshot without free-text instructions", () => {
    const draft = buildTemporalSubmissionDraftFromExecution(
      {
        workflowId: "mm:snapshot-skill-only",
        workflowType: "MoonMind.Run",
        taskInputSnapshot: {
          available: true,
          artifactRef: "art_snapshot_skill_only",
          snapshotVersion: 1,
          sourceKind: "create",
          reconstructionMode: "authoritative",
          disabledReasons: {},
          fallbackEvidenceRefs: [],
        },
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
      },
      {
        snapshotVersion: 1,
        source: { kind: "create" },
        draft: {
          taskShape: "skill_only",
          runtime: "codex_cli",
          model: "gpt-5.4",
          effort: "medium",
          repository: "MoonLadderStudios/MoonMind",
          startingBranch: "main",
          targetBranch: "durable-task-edit-reconstruction",
          publish: { mode: "none" },
          instructions: "",
          primarySkill: {
            name: "moonspec-orchestrate",
            inputs: {
              request: "Define durable task edit reconstruction model",
            },
          },
          appliedTemplates: [],
          dependencies: [],
          attachments: [],
          proposeTasks: false,
          proposalPolicy: null,
        },
      },
    );

    expect(draft).toMatchObject({
      runtime: "codex_cli",
      model: "gpt-5.4",
      effort: "medium",
      repository: "MoonLadderStudios/MoonMind",
      branch: "main",
      legacyBranchWarning:
        "This older task used separate starting and target branches. The new form submits one branch, so review it before saving or rerunning.",
      publishMode: "none",
      taskInstructions: "",
      primarySkill: "moonspec-orchestrate",
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

  it("reconstructs ordered editable steps from Temporal execution fields", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:ordered-steps",
      workflowType: "MoonMind.Run",
      inputParameters: {
        task: {
          instructions: "Primary operator objective.",
          steps: [
            {
              id: "step-primary",
              title: "Primary",
              instructions: "Primary operator objective.",
              skill: {
                id: "moonspec-orchestrate",
                args: { mode: "runtime" },
                requiredCapabilities: ["git"],
              },
            },
            {},
            {
              id: "step-second",
              title: "Second",
              instructions: "Second step instructions.",
              storyOutput: {
                mode: "jira",
                jira: {
                  projectKey: "MM",
                  issueTypeName: "Story",
                  dependencyMode: "linear_blocker_chain",
                },
              },
              tool: {
                name: "pr-resolver",
                inputs: { merge: false },
              },
            },
          ],
        },
      },
    });

    expect(draft.steps).toEqual([
      {
        id: "step-primary",
        title: "Primary",
        instructions: "Primary operator objective.",
        skillId: "moonspec-orchestrate",
        skillArgs: { mode: "runtime" },
        skillRequiredCapabilities: ["git"],
        templateStepId: "",
        templateInstructions: "",
      },
      {
        id: "step-second",
        title: "Second",
        instructions: "Second step instructions.",
        skillId: "pr-resolver",
        skillArgs: { merge: false },
        skillRequiredCapabilities: [],
        templateStepId: "",
        templateInstructions: "",
        storyOutput: {
          mode: "jira",
          jira: {
            projectKey: "MM",
            issueTypeName: "Story",
            dependencyMode: "linear_blocker_chain",
          },
        },
      },
    ]);
  });

  it("reconstructs template attachments for editable Temporal steps", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:template-attachments",
      workflowType: "MoonMind.Run",
      inputParameters: {
        task: {
          instructions: "Edit a template-backed task.",
          steps: [
            {
              id: "tpl:speckit-demo:1.2.3:01",
              instructions: "Clarify the template scope.",
              templateStepId: "tpl:speckit-demo:1.2.3:01",
              templateInstructions: "Clarify the template scope.",
              inputAttachments: [
                {
                  artifactId: "art-template",
                  filename: "template.png",
                  contentType: "image/png",
                  sizeBytes: 14,
                },
              ],
            },
          ],
        },
      },
    });

    expect(draft.steps[0]?.templateAttachments).toEqual([
      {
        artifactId: "art-template",
        filename: "template.png",
        contentType: "image/png",
        sizeBytes: 14,
      },
    ]);
  });

  it("does not synthesize a task objective into an editable step", () => {
    const draft = buildTemporalSubmissionDraftFromExecution({
      workflowId: "mm:objective-differs",
      workflowType: "MoonMind.Run",
      inputParameters: {
        task: {
          instructions: "Preset-level objective text.",
          steps: [
            {
              id: "step-1",
              title: "Execute preset",
              instructions: "Run the first explicit preset step.",
            },
          ],
        },
      },
    });

    expect(draft.steps).toEqual([
      expect.objectContaining({
        id: "step-1",
        title: "Execute preset",
        instructions: "Run the first explicit preset step.",
      }),
    ]);
  });

  it("uses artifact steps when inline execution steps are partial", () => {
    const draft = buildTemporalSubmissionDraftFromExecution(
      {
        workflowId: "mm:partial-inline-steps",
        workflowType: "MoonMind.Run",
        inputArtifactRef: "full-input",
        inputParameters: {
          task: {
            instructions: "Run the artifact-backed plan.",
            steps: [
              {
                id: "placeholder",
                instructions: "Inline placeholder step.",
              },
            ],
          },
        },
      },
      {
        task: {
          instructions: "Run the artifact-backed plan.",
          steps: [
            {
              id: "artifact-1",
              instructions: "Hydrated artifact step one.",
            },
            {
              id: "artifact-2",
              instructions: "Hydrated artifact step two.",
            },
          ],
        },
      },
    );

    expect(draft.steps.map((step) => step.id)).toEqual([
      "artifact-1",
      "artifact-2",
    ]);
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
      branch: null,
      legacyBranchWarning: null,
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
        (screen.getByLabelText("Branch") as HTMLInputElement).value,
      ).toBe("main");
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

  it("loads every step when editing a multi-step Temporal execution", async () => {
    renderForEdit("mm:multi-step-edit");

    expect(await screen.findByRole("heading", { name: "Edit Task" })).toBeTruthy();
    await waitFor(() => {
      const instructions = screen.getAllByLabelText(
        "Instructions",
      ) as HTMLTextAreaElement[];
      expect(instructions.map((item) => item.value)).toEqual([
        "Investigate why edit shows only step 1.",
        "Patch the edit reconstruction path.",
        "Run the focused task-create tests.",
      ]);
      expect(screen.getByText("Step 1 (Primary)")).toBeTruthy();
      expect(screen.getByText("Step 2")).toBeTruthy();
      expect(screen.getByText("Step 3")).toBeTruthy();
    });
  });

  it("uses the target skill when the first reconstructed step is auto", async () => {
    renderForEdit("mm:auto-primary-skill");

    expect(await screen.findByRole("heading", { name: "Edit Task" })).toBeTruthy();
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/Skill \(optional\)/) as HTMLInputElement).value,
      ).toBe("moonspec-orchestrate");
    });
  });

  it("preserves unchanged later steps when saving a multi-step edit", async () => {
    renderForEdit("mm:multi-step-edit");

    await waitFor(() => {
      expect(screen.getAllByLabelText("Instructions")).toHaveLength(3);
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions/mm%3Amulti-step-edit/update",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const updateCall = fetchSpy.mock.calls
      .filter(
        ([url]) => String(url) === "/api/executions/mm%3Amulti-step-edit/update",
      )
      .at(-1);
    const request = JSON.parse(String(updateCall?.[1]?.body));
    expect(request).toMatchObject({
      updateName: "UpdateInputs",
      parametersPatch: {
        task: {
          instructions: "Investigate why edit shows only step 1.",
          steps: [
            {
              id: "step-primary",
              title: "Investigate",
              instructions: "Investigate why edit shows only step 1.",
              skill: {
                id: "moonspec-orchestrate",
                args: { mode: "runtime" },
                requiredCapabilities: ["git"],
              },
            },
            {
              id: "step-patch",
              title: "Patch",
              instructions: "Patch the edit reconstruction path.",
            },
            {
              id: "step-verify",
              title: "Verify",
              instructions: "Run the focused task-create tests.",
            },
          ],
        },
      },
    });
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

  it("submits terminal rerun mode through RequestRerun and opens the created rerun detail view", async () => {
    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Arerun-123",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Rerun from artifact-backed instructions.");
    });
    fireEvent.change(instructions, {
      target: { value: "Rerun with reviewed Temporal inputs." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Rerun Task" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions/mm%3Arerun-123/update",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const updateCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions/mm%3Arerun-123/update")
      .at(-1);
    const request = JSON.parse(String(updateCall?.[1]?.body));
    const artifactCreateCall = fetchSpy.mock.calls.find(
      ([url, init]) => String(url) === "/api/artifacts" && init?.method === "POST",
    );
    const artifactCreateRequest = JSON.parse(
      String(artifactCreateCall?.[1]?.body),
    );
    expect(artifactCreateRequest).toMatchObject({
      metadata: {
        sourceWorkflowId: "mm:rerun-123",
      },
    });
    expect(request).toMatchObject({
      updateName: "RequestRerun",
      inputArtifactRef: "art-001",
      parametersPatch: {
        inputArtifactRef: "art-001",
        repository: "MoonLadderStudios/MoonMind",
        targetRuntime: "codex_cli",
        task: {
          instructions: "Rerun with reviewed Temporal inputs.",
          runtime: {
            mode: "codex_cli",
            model: "gpt-5.4",
            effort: "medium",
            profileId: "profile:codex-secondary",
          },
          publish: {
            mode: "pr",
          },
        },
      },
    });
    expect(request.inputArtifactRef).not.toBe("historical-input");
    expect(
      fetchSpy.mock.calls.some(
        ([url, init]) => String(url) === "/api/executions" && init?.method === "POST",
      ),
    ).toBe(false);
    await waitFor(() => {
      expect(navigateTo).toHaveBeenCalledWith(
        "/tasks/mm%3Arerun-created?source=temporal",
      );
    });
    expect(
      window.sessionStorage.getItem("moonmind.temporalTaskEditing.notice"),
    ).toBe("Rerun was requested and the latest execution view is ready.");
  });

  it("shows backend stale-state failures without redirecting from rerun mode", async () => {
    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Astale-rerun",
    );

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Rerunnable before submit.");
    });
    fireEvent.change(instructions, {
      target: { value: "Try to rerun after state changed." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Rerun Task" }));

    expect(
      await screen.findByText(
        "Workflow state changed and rerun is no longer available.",
      ),
    ).toBeTruthy();
    expect(navigateTo).not.toHaveBeenCalled();
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

  it("submits edit updates through the configured Temporal update route", async () => {
    const customPayload = JSON.parse(JSON.stringify(mockPayload)) as BootPayload;
    (
      customPayload.initialData as {
        dashboardConfig: {
          sources: {
            temporal: {
              detail: string;
              update: string;
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
      update: "/gateway/api/executions/{workflowId}/updates",
    };

    renderForEdit("mm:custom-edit", customPayload);

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Save through configured endpoint.");
    });
    fireEvent.change(instructions, {
      target: { value: "Edited through configured update endpoint." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/gateway/api/executions/mm%3Acustom-edit/updates",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const updateCall = fetchSpy.mock.calls.find(
      ([url]) => String(url) === "/gateway/api/executions/mm%3Acustom-edit/updates",
    );
    const request = JSON.parse(String(updateCall?.[1]?.body));
    expect(request).toMatchObject({
      updateName: "UpdateInputs",
      parametersPatch: {
        repository: "MoonLadderStudios/MoonMind",
        targetRuntime: "codex_cli",
        task: {
          instructions: "Edited through configured update endpoint.",
        },
      },
    });
  });

  it("shows a feature-disabled error without loading execution detail", async () => {
    const disabledPayload = JSON.parse(JSON.stringify(mockPayload)) as BootPayload;
    (
      disabledPayload.initialData as {
        dashboardConfig: {
          features: { temporalDashboard: { temporalTaskEditing: boolean } };
        };
      }
    ).dashboardConfig.features.temporalDashboard.temporalTaskEditing = false;

    renderForEdit("mm:edit-123", disabledPayload);

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
    renderForEdit("mm:unsupported");

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
    renderForEdit("mm:no-edit");

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
    renderForEdit("mm:incomplete");

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

  it("submits active edit mode through UpdateInputs and returns to the Temporal detail view", async () => {
    const telemetryEvents: Array<Record<string, unknown>> = [];
    const onTelemetry = (event: Event) => {
      telemetryEvents.push((event as CustomEvent).detail);
    };
    window.addEventListener("moonmind:temporal-task-editing", onTelemetry);

    renderForEdit("mm:edit-123");

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Rebuild the Temporal task draft.");
    });
    fireEvent.change(instructions, {
      target: { value: "Save edited Temporal inputs." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions/mm%3Aedit-123/update",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const updateCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions/mm%3Aedit-123/update")
      .at(-1);
    const request = JSON.parse(String(updateCall?.[1]?.body));
    expect(request).toMatchObject({
      updateName: "UpdateInputs",
      parametersPatch: {
        repository: "MoonLadderStudios/MoonMind",
        operatorNote: "Keep this unedited top-level value.",
        targetRuntime: "gemini_cli",
        task: {
          instructions: "Save edited Temporal inputs.",
          proposeTasks: true,
          tool: {
            type: "skill",
            name: "speckit-implement",
          },
          skill: {
            id: "speckit-implement",
          },
          skills: {
            include: [{ name: "speckit-implement" }],
          },
          runtime: {
            mode: "gemini_cli",
            model: "gemini-2.5-pro",
            effort: "high",
            profileId: "profile:gemini-default",
          },
          git: {
            branch: "main",
          },
          publish: {
            mode: "branch",
          },
          appliedStepTemplates: [
            expect.objectContaining({
              slug: "speckit-demo",
              version: "1.2.3",
              inputs: { feature_name: "Task Editing" },
              stepIds: ["tpl:speckit-demo:1.2.3:01"],
              capabilities: ["git"],
            }),
          ],
        },
      },
    });
    expect(request.parametersPatch).not.toHaveProperty("startingBranch");
    expect(request.parametersPatch).not.toHaveProperty("targetBranch");
    expect(request.inputArtifactRef).toBeUndefined();
    await waitFor(() => {
      expect(navigateTo).toHaveBeenCalledWith(
        "/tasks/mm%3Aedit-123?source=temporal",
      );
    });
    expect(
      window.sessionStorage.getItem("moonmind.temporalTaskEditing.notice"),
    ).toBe("Changes were saved to this execution.");
    expect(telemetryEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event: "draft_reconstruction_success",
          mode: "edit",
          workflowId: "mm:edit-123",
        }),
        expect.objectContaining({
          event: "update_submit_attempt",
          mode: "edit",
          workflowId: "mm:edit-123",
          updateName: "UpdateInputs",
        }),
        expect.objectContaining({
          event: "update_submit_result",
          mode: "edit",
          workflowId: "mm:edit-123",
          updateName: "UpdateInputs",
          result: "success",
          applied: "immediate",
        }),
      ]),
    );
    window.removeEventListener("moonmind:temporal-task-editing", onTelemetry);
  });

  it("clears persisted merge automation when edit mode deselects the combined publish mode", async () => {
    renderForEdit("mm:merge-automation-edit");

    const publishSelect = (await screen.findByLabelText(
      "Publish Mode",
    )) as HTMLSelectElement;
    await waitFor(() => {
      expect(publishSelect.value).toBe("pr_with_merge_automation");
    });
    fireEvent.change(publishSelect, { target: { value: "pr" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions/mm%3Amerge-automation-edit/update",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const updateCall = fetchSpy.mock.calls
      .filter(
        ([url]) =>
          String(url) === "/api/executions/mm%3Amerge-automation-edit/update",
      )
      .at(-1);
    const request = JSON.parse(String(updateCall?.[1]?.body));
    expect(request.parametersPatch).not.toHaveProperty("mergeAutomation");
    expect(request.parametersPatch).toMatchObject({
      publishMode: "pr",
      task: {
        publish: { mode: "pr" },
      },
    });
  });

  it("shows continue-as-new success copy and redirects to the returned execution context", async () => {
    renderForEdit("mm:continue-edit");

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    fireEvent.change(instructions, {
      target: { value: "Accept these inputs for the refreshed run." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(navigateTo).toHaveBeenCalledWith(
        "/tasks/mm%3Acontinue-edit-next?source=temporal",
      );
    });
    expect(
      window.sessionStorage.getItem("moonmind.temporalTaskEditing.notice"),
    ).toBe("Changes were accepted and will continue in a refreshed run.");
  });

  it("creates a fresh input artifact when editing an artifact-backed execution", async () => {
    renderForEdit("mm:artifact-edit");

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Rerun from artifact-backed instructions.");
    });
    fireEvent.change(instructions, {
      target: { value: "Edited artifact-backed instructions." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions/mm%3Aartifact-edit/update",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const artifactUploadCall = fetchSpy.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/artifacts/art-001/content" &&
        init?.method === "PUT",
    );
    expect(String(artifactUploadCall?.[1]?.body)).toContain(
      "Edited artifact-backed instructions.",
    );
    const updateCall = fetchSpy.mock.calls
      .filter(
        ([url]) => String(url) === "/api/executions/mm%3Aartifact-edit/update",
      )
      .at(-1);
    const request = JSON.parse(String(updateCall?.[1]?.body));
    expect(request).toMatchObject({
      updateName: "UpdateInputs",
      inputArtifactRef: "art-001",
      parametersPatch: {
        repository: "MoonLadderStudios/MoonMind",
        task: {
          instructions: "Edited artifact-backed instructions.",
        },
      },
    });
    expect(request.inputArtifactRef).not.toBe("historical-input");
    await waitFor(() => {
      expect(navigateTo).toHaveBeenCalledWith(
        "/tasks/mm%3Aartifact-edit?source=temporal",
      );
    });
    expect(
      window.sessionStorage.getItem("moonmind.temporalTaskEditing.notice"),
    ).toBe("Changes were scheduled for the next safe point.");
  });

  it("retains unchanged persisted attachment refs when editing an artifact-backed execution", async () => {
    renderForEdit("mm:attachment-edit", withAttachmentPolicy());

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Preserve the existing attachments.");
    });
    expect(await screen.findByText("objective.png (1.2 KB)")).toBeTruthy();
    expect(await screen.findByText("step.webp (4.5 KB)")).toBeTruthy();
    const downloadLinks = await screen.findAllByRole("link", {
      name: "Download",
    });
    expect(downloadLinks.map((link) => link.getAttribute("href"))).toEqual([
      "/api/artifacts/art-objective/download",
      "/api/artifacts/art-step/download",
    ]);

    fireEvent.change(instructions, {
      target: { value: "Edited instructions with existing attachments." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions/mm%3Aattachment-edit/update",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const updateCall = fetchSpy.mock.calls
      .filter(
        ([url]) => String(url) === "/api/executions/mm%3Aattachment-edit/update",
      )
      .at(-1);
    const request = JSON.parse(String(updateCall?.[1]?.body));
    expect(request).toMatchObject({
      updateName: "UpdateInputs",
      parametersPatch: {
        repository: "MoonLadderStudios/MoonMind",
        task: {
          instructions: "Edited instructions with existing attachments.",
          inputAttachments: [
            {
              artifactId: "art-objective",
              filename: "objective.png",
              contentType: "image/png",
              sizeBytes: 1234,
            },
          ],
          steps: [
            {
              id: "step-with-attachment",
              title: "Attached step",
              instructions: "Edited instructions with existing attachments.",
              inputAttachments: [
                {
                  artifactId: "art-step",
                  filename: "step.webp",
                  contentType: "image/webp",
                  sizeBytes: 4567,
                },
              ],
            },
          ],
        },
      },
    });
  });

  it("serializes an explicit empty objective attachment list when refs are removed during edit", async () => {
    renderForEdit("mm:attachment-edit", withAttachmentPolicy());

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Preserve the existing attachments.");
    });

    const objectiveItem = (await screen.findByText("objective.png (1.2 KB)"))
      .closest("li") as HTMLElement;
    fireEvent.click(within(objectiveItem).getByRole("button", { name: "Remove" }));
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions/mm%3Aattachment-edit/update",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const updateCall = fetchSpy.mock.calls
      .filter(
        ([url]) => String(url) === "/api/executions/mm%3Aattachment-edit/update",
      )
      .at(-1);
    const request = JSON.parse(String(updateCall?.[1]?.body));
    expect(request.parametersPatch.task.inputAttachments).toEqual([]);
    expect(request.parametersPatch.task.steps[0].inputAttachments).toEqual([
      {
        artifactId: "art-step",
        filename: "step.webp",
        contentType: "image/webp",
        sizeBytes: 4567,
      },
    ]);
  });

  it("counts persisted refs in client attachment policy validation", async () => {
    renderForEdit("mm:attachment-edit", withAttachmentPolicy());

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Preserve the existing attachments.");
    });

    const attachmentInput = await screen.findByLabelText("Step 1 attachment file picker");
    fireEvent.change(attachmentInput, {
      target: {
        files: [
          new File(["a"], "one.png", { type: "image/png" }),
          new File(["b"], "two.png", { type: "image/png" }),
          new File(["c"], "three.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(await screen.findByText("Too many attachments (5/4).")).toBeTruthy();
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url) === "/api/artifacts"),
    ).toBe(false);
  });

  it("externalizes oversized edited input before sending UpdateInputs", async () => {
    renderForEdit("mm:edit-123");

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Rebuild the Temporal task draft.");
    });
    const oversizedInstructions = `Edited oversized instructions. ${"x".repeat(9000)}`;
    fireEvent.change(instructions, {
      target: { value: oversizedInstructions },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions/mm%3Aedit-123/update",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const artifactCreateCall = fetchSpy.mock.calls.find(
      ([url, init]) => String(url) === "/api/artifacts" && init?.method === "POST",
    );
    expect(artifactCreateCall).toBeTruthy();
    const artifactUploadCall = fetchSpy.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/artifacts/art-001/content" &&
        init?.method === "PUT",
    );
    expect(String(artifactUploadCall?.[1]?.body)).toContain(
      oversizedInstructions,
    );
    const updateCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions/mm%3Aedit-123/update")
      .at(-1);
    const request = JSON.parse(String(updateCall?.[1]?.body));
    expect(request).toMatchObject({
      updateName: "UpdateInputs",
      inputArtifactRef: "art-001",
      parametersPatch: {
        inputArtifactRef: "art-001",
      },
    });
    expect(request.parametersPatch.task.instructions).toBeUndefined();
  });

  it("shows backend stale-state failures without redirecting from edit mode", async () => {
    renderForEdit("mm:stale-edit");

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    await waitFor(() => {
      expect(instructions.value).toBe("Editable before submit.");
    });
    fireEvent.change(instructions, {
      target: { value: "Try to update after state changed." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(
      await screen.findByText(
        "Workflow is in a terminal state and no longer accepts updates.",
      ),
    ).toBeTruthy();
    expect(navigateTo).not.toHaveBeenCalled();
  });

  it("shows backend validation failures without redirecting from edit mode", async () => {
    renderForEdit("mm:validation-edit");

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    fireEvent.change(instructions, {
      target: { value: "Submit invalid target branch." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(
      await screen.findByText(
        "Task input validation failed: target branch is invalid.",
      ),
    ).toBeTruthy();
    expect(navigateTo).not.toHaveBeenCalled();
    expect(
      window.sessionStorage.getItem("moonmind.temporalTaskEditing.notice"),
    ).toBeNull();
  });

  it("shows artifact preparation failures without submitting UpdateInputs", async () => {
    const telemetryEvents: Array<Record<string, unknown>> = [];
    const onTelemetry = (event: Event) => {
      telemetryEvents.push((event as CustomEvent).detail);
    };
    window.addEventListener("moonmind:temporal-task-editing", onTelemetry);
    artifactCreateResponseOverride = {
      ok: false,
      status: 422,
      text: async () =>
        JSON.stringify({
          detail: {
            code: "artifact_create_failed",
            message: "Artifact storage is unavailable.",
          },
        }),
    } as Response;
    renderForEdit("mm:artifact-failure");

    const instructions = (await screen.findByLabelText(
      "Instructions",
    )) as HTMLTextAreaElement;
    fireEvent.change(instructions, {
      target: { value: "Attempt artifact-backed edit." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(await screen.findByText("Artifact storage is unavailable.")).toBeTruthy();
    expect(
      fetchSpy.mock.calls.some(
        ([url]) => String(url) === "/api/executions/mm%3Aartifact-failure/update",
      ),
    ).toBe(false);
    expect(telemetryEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event: "draft_reconstruction_success",
          workflowId: "mm:artifact-failure",
        }),
      ]),
    );
    expect(
      telemetryEvents.some(
        (event) => String(event.event) === "update_submit_attempt",
      ),
    ).toBe(false);
    expect(navigateTo).not.toHaveBeenCalled();
    window.removeEventListener("moonmind:temporal-task-editing", onTelemetry);
  });

  it("submits the Temporal task payload and redirects on success", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(
      (await screen.findByRole("heading", { name: "Create Task" })).closest(
        ".task-create-page",
      ),
    ).not.toBeNull();

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

    const createButton = screen.getByRole("button", { name: "Create" });
    expect(createButton.querySelector("[data-submit-arrow='right']")).not.toBeNull();
    fireEvent.click(createButton);

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

  it("offers repository options while preserving editable repository entry", async () => {
    renderWithClient(<TaskCreatePage payload={withRepositoryOptions()} />);

    const repositoryInput = await screen.findByLabelText(/GitHub Repo/);
    expect(repositoryInput.getAttribute("list")).toBe("queue-repository-options");

    const datalist = document.querySelector<HTMLDataListElement>(
      "#queue-repository-options",
    );
    expect(datalist).not.toBeNull();
    expect(
      Array.from(datalist?.querySelectorAll("option") || []).map(
        (option) => option.value,
      ),
    ).toEqual(["MoonLadderStudios/MoonMind", "Octo/Repo"]);

    fireEvent.change(repositoryInput, {
      target: { value: "Custom/Repo" },
    });
    expect((repositoryInput as HTMLInputElement).value).toBe("Custom/Repo");
  });

  it("submits a selected repository option without changing unrelated draft fields", async () => {
    renderWithClient(<TaskCreatePage payload={withRepositoryOptions()} />);

    const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    );
    expect(primaryStep).not.toBeNull();
    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Run repository dropdown regression." },
    });
    fireEvent.change(screen.getByLabelText(/GitHub Repo/), {
      target: { value: "Octo/Repo" },
    });
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "moonspec-orchestrate" },
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

    const request = latestCreateRequest();
    expect(request).toMatchObject({
      payload: {
        repository: "Octo/Repo",
        task: {
          instructions: "Run repository dropdown regression.",
          runtime: {
            mode: "codex_cli",
          },
        },
      },
    });
  });

  it("reveals per-step advanced skill options from the bottom toggle", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    );
    expect(primaryStep).not.toBeNull();
    expect(
      within(primaryStep as HTMLElement).queryByLabelText(
        /skill args/i,
      ),
    ).toBeNull();
    expect(
      within(primaryStep as HTMLElement).queryByLabelText(
        /skill required capabilities/i,
      ),
    ).toBeNull();

    expect(document.querySelector("#queue-advanced-settings")).toBeNull();

    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "custom-skill" },
      },
    );
    expect(
      within(primaryStep as HTMLElement).queryByLabelText(
        /skill args/i,
      ),
    ).toBeNull();

    const advancedToggle = screen.getByLabelText("Show advanced step options");
    expect(advancedToggle.closest('[data-canonical-create-section="Submit"]')).not.toBeNull();
    fireEvent.click(advancedToggle);

    expect(
      within(primaryStep as HTMLElement).getByLabelText(
        "Step 1 Skill Args (optional JSON object)",
      ),
    ).toBeTruthy();
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(
        /Step 1 Skill Required Capabilities \(optional CSV\)/,
      ),
      {
        target: { value: "docker, qdrant" },
      },
    );
    fireEvent.change(screen.getByLabelText("Instructions"), {
      target: { value: "Run advanced routing regression flow." },
    });

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
    expect(request.payload.task.tool.requiredCapabilities).toEqual([
      "docker",
      "qdrant",
    ]);
    expect(request.payload.requiredCapabilities).toEqual([
      "codex_cli",
      "git",
      "gh",
      "docker",
      "qdrant",
    ]);
  });

  it("does not submit hidden advanced step capabilities after toggling advanced mode off", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    );
    expect(primaryStep).not.toBeNull();

    fireEvent.click(screen.getByLabelText("Show advanced step options"));
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(
        "Step 1 Skill Args (optional JSON object)",
      ),
      {
        target: { value: '{"hidden":true}' },
      },
    );
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(
        /Step 1 Skill Required Capabilities \(optional CSV\)/,
      ),
      {
        target: { value: "docker, qdrant" },
      },
    );
    fireEvent.click(screen.getByLabelText("Show advanced step options"));
    fireEvent.change(screen.getByLabelText("Instructions"), {
      target: { value: "Run without hidden advanced routing." },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({
          method: "POST",
        }),
      );
    });

    const payload = latestCreateRequest().payload as {
      task: { tool: Record<string, unknown> };
      requiredCapabilities: string[];
    };
    expect(payload.task.tool).toEqual({
      type: "skill",
      name: "auto",
      version: "1.0",
    });
    expect(payload.requiredCapabilities).toEqual([
      "codex_cli",
      "git",
      "gh",
    ]);
  });

  it("uploads a step attachment as a structured step input without rewriting instructions", async () => {
    renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Review the provided screenshot." },
    });
    const attachmentInput = await screen.findByLabelText("Step 1 attachment file picker");
    const file = new File(["fake image"], "wireframe.png", {
      type: "image/png",
    });
    fireEvent.change(attachmentInput, {
      target: { files: [file] },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/artifacts",
        expect.objectContaining({
          method: "POST",
        }),
      );
    });
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({
          method: "POST",
        }),
      );
    });

    const artifactCreateCall = fetchSpy.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/artifacts" &&
        String(init?.body || "").includes("task-dashboard-step-attachment"),
    );
    expect(JSON.parse(String(artifactCreateCall?.[1]?.body))).toMatchObject({
      content_type: "image/png",
      size_bytes: file.size,
      metadata: {
        filename: "wireframe.png",
        source: "task-dashboard-step-attachment",
        stepLabel: "Step 1",
      },
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.task.instructions).toBe(
      "Review the provided screenshot.",
    );
    expect(request.payload.task.instructions).not.toContain(
      "Step input attachments:",
    );
    expect(request.payload.task.inputAttachments).toBeUndefined();
    expect(request.payload.task.steps[0].instructions).toBe(
      "Review the provided screenshot.",
    );
    expect(request.payload.task.steps[0].instructions).not.toContain(
      "Step input attachments:",
    );
    expect(request.payload.task.steps[0].inputAttachments).toEqual([
      {
        artifactId: "art-001",
        filename: "wireframe.png",
        contentType: "image/png",
        sizeBytes: file.size,
      },
    ]);
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/artifacts/art-001/links",
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining("input.attachment"),
      }),
    );
  });

  it("hides attachment entry points when policy is disabled and preserves text-only authoring", async () => {
    renderWithClient(
      <TaskCreatePage payload={withDisabledAttachmentPolicy()} />,
    );

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Create a text-only task while image inputs are disabled." },
    });

    expect(screen.queryByLabelText("Step 1 attachment file picker")).toBeNull();
    expect(
      screen.queryByLabelText("Feature Request / Initial Instructions attachments"),
    ).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("uses image-specific labels when policy allows only image MIME types", async () => {
    renderWithClient(
      <TaskCreatePage payload={withImageOnlyAttachmentPolicy()} />,
    );

    expect(await screen.findByText("Step 1 Images (optional)")).toBeTruthy();
    expect(
      await screen.findByText(
        "Feature Request / Initial Instructions Images",
      ),
    ).toBeTruthy();
  });

  it("reports attachment validation failures at the affected target before upload", async () => {
    renderWithClient(
      <TaskCreatePage payload={withImageOnlyAttachmentPolicy()} />,
    );

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Validate unsupported attachment types." },
    });
    const file = new File(["not an image"], "notes.txt", {
      type: "text/plain",
    });
    fireEvent.change(await screen.findByLabelText("Step 1 image file picker"), {
      target: { files: [file] },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(
        screen.getAllByText("Step 1: Unsupported file type for notes.txt.")
          .length,
      ).toBeGreaterThanOrEqual(1);
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url) === "/api/artifacts"),
    ).toBe(false);
  });

  it("keeps step upload failures target-scoped with retry and remove actions", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/artifacts/art-001/content") {
        return Promise.resolve({
          ok: false,
          status: 500,
          text: async () =>
            JSON.stringify({
              detail: { message: "Object storage rejected the upload." },
            }),
        } as Response);
      }
      return defaultFetch?.(input, init) as ReturnType<typeof window.fetch>;
    });

    renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Upload a step attachment and handle failure." },
    });
    const file = new File(["fake image"], "wireframe.png", {
      type: "image/png",
    });
    fireEvent.change(await screen.findByLabelText("Step 1 attachment file picker"), {
      target: { files: [file] },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(
        screen.getAllByText("Step 1: Object storage rejected the upload.")
          .length,
      ).toBeGreaterThanOrEqual(1);
    });
    expect(
      screen.getByRole("button", {
        name: "Retry upload for Step 1 attachment wireframe.png",
      }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", {
        name: "Remove Step 1 attachment wireframe.png",
      }),
    ).toBeTruthy();
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url) === "/api/executions"),
    ).toBe(false);
  });

  it("renders a compact image add button for step attachments when policy is image-only", async () => {
    renderWithClient(
      <TaskCreatePage payload={withImageOnlyAttachmentPolicy()} />,
    );

    const addButton = await screen.findByRole("button", {
      name: "Add images to Step 1",
    });
    expect(addButton.textContent).toBe("+");
    const fileInput = screen.getByLabelText(
      "Step 1 image file picker",
    ) as HTMLInputElement;
    expect(fileInput.type).toBe("file");
    expect(fileInput.accept).toBe("image/png,image/jpeg,image/webp");
    expect(fileInput.multiple).toBe(true);
  });

  it("uses generic step attachment add copy when policy permits non-image files", async () => {
    renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

    expect(
      await screen.findByRole("button", {
        name: "Add attachments to Step 1",
      }),
    ).toBeTruthy();
  });

  it("appends step attachments selected through repeated add actions", async () => {
    renderWithClient(
      <TaskCreatePage payload={withImageOnlyAttachmentPolicy()} />,
    );

    const fileInput = await screen.findByLabelText("Step 1 image file picker");
    const firstFile = new File(["first"], "first.png", {
      type: "image/png",
      lastModified: 1,
    });
    const secondFile = new File(["second"], "second.png", {
      type: "image/png",
      lastModified: 2,
    });

    fireEvent.change(fileInput, { target: { files: [firstFile] } });
    expect(await screen.findByText("first.png")).toBeTruthy();

    fireEvent.change(fileInput, { target: { files: [secondFile] } });
    expect(await screen.findByText("first.png")).toBeTruthy();
    expect(await screen.findByText("second.png")).toBeTruthy();
  });

  it("dedupes exact duplicate step attachments selected through add actions", async () => {
    renderWithClient(
      <TaskCreatePage payload={withImageOnlyAttachmentPolicy()} />,
    );

    const fileInput = await screen.findByLabelText("Step 1 image file picker");
    const duplicate = new File(["same"], "same.png", {
      type: "image/png",
      lastModified: 7,
    });

    fireEvent.change(fileInput, { target: { files: [duplicate] } });
    fireEvent.change(fileInput, { target: { files: [duplicate] } });

    await screen.findByText("same.png");
    expect(screen.getAllByText("same.png")).toHaveLength(1);
  });

  it("preserves attachment metadata and remove action when preview fails", async () => {
    const originalCreateObjectUrl = URL.createObjectURL;
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:preview-wireframe"),
    });
    try {
      renderWithClient(
        <TaskCreatePage payload={withImageOnlyAttachmentPolicy()} />,
      );

      const file = new File(["fake image"], "wireframe.png", {
        type: "image/png",
      });
      fireEvent.click(
        await screen.findByRole("button", { name: "Add images to Step 1" }),
      );
      fireEvent.change(await screen.findByLabelText("Step 1 image file picker"), {
        target: { files: [file] },
      });

      const preview = await screen.findByAltText(
        "Preview of Step 1 attachment wireframe.png",
      );
      fireEvent.error(preview);

      expect(
        await screen.findByText(
          "Step 1: Preview unavailable for wireframe.png. Attachment metadata remains available.",
        ),
      ).toBeTruthy();
      expect(screen.getByText("wireframe.png")).toBeTruthy();
      expect(
        screen.getByRole("button", {
          name: "Remove Step 1 attachment wireframe.png",
        }),
      ).toBeTruthy();
    } finally {
      Object.defineProperty(URL, "createObjectURL", {
        configurable: true,
        value: originalCreateObjectUrl,
      });
    }
  });

  it("uploads an objective-scoped attachment only as a task input attachment", async () => {
    renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Review objective context." },
    });
    fireEvent.change(
      screen.getByLabelText("Feature Request / Initial Instructions"),
      {
        target: { value: "Use the product sketch as objective context." },
      },
    );
    const objectiveInput = await screen.findByLabelText(
      "Feature Request / Initial Instructions attachments",
    );
    const file = new File(["fake image"], "objective.png", {
      type: "image/png",
    });
    fireEvent.change(objectiveInput, {
      target: { files: [file] },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({
          method: "POST",
        }),
      );
    });

    const artifactCreateCall = fetchSpy.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/artifacts" &&
        String(init?.body || "").includes("task-dashboard-objective-attachment"),
    );
    expect(JSON.parse(String(artifactCreateCall?.[1]?.body))).toMatchObject({
      content_type: "image/png",
      size_bytes: file.size,
      metadata: {
        filename: "objective.png",
        source: "task-dashboard-objective-attachment",
        target: "objective",
      },
    });

    const payload = latestCreateRequest().payload as {
      task: {
        instructions: string;
        inputAttachments?: unknown;
        steps?: Array<Record<string, unknown>>;
      };
    };
    expect(payload.task.instructions).toBe(
      "Use the product sketch as objective context.",
    );
    expect(payload.task.instructions).not.toContain(
      "Step input attachments:",
    );
    expect(payload.task.inputAttachments).toEqual([
      {
        artifactId: "art-001",
        filename: "objective.png",
        contentType: "image/png",
        sizeBytes: file.size,
      },
    ]);
    expect(payload.task.steps?.[0]?.inputAttachments).toBeUndefined();
  });

  it("keeps step attachments with their owning steps after reorder", async () => {
    renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Primary screenshot instructions." },
    });
    const firstFile = new File(["first image"], "primary.png", {
      type: "image/png",
    });
    fireEvent.change(await screen.findByLabelText("Step 1 attachment file picker"), {
      target: { files: [firstFile] },
    });

    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));
    const stepTwo = (await screen.findByText("Step 2")).closest("section");
    expect(stepTwo).not.toBeNull();
    fireEvent.change(
      within(stepTwo as HTMLElement).getByLabelText("Instructions"),
      {
        target: { value: "Second screenshot instructions." },
      },
    );
    const secondFile = new File(["second image"], "second.png", {
      type: "image/png",
    });
    fireEvent.change(await screen.findByLabelText("Step 2 attachment file picker"), {
      target: { files: [secondFile] },
    });

    const stepOne = screen.getByText("Step 1 (Primary)").closest("section");
    expect(stepOne).not.toBeNull();
    fireEvent.click(
      within(stepOne as HTMLElement).getByRole("button", {
        name: "Move step down",
      }),
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

    const payload = latestCreateRequest().payload as {
      task: {
        steps: Array<Record<string, unknown>>;
      };
    };
    expect(payload.task.steps[0]).toMatchObject({
      instructions: "Second screenshot instructions.",
      inputAttachments: [
        {
          filename: "second.png",
          contentType: "image/png",
          sizeBytes: secondFile.size,
        },
      ],
    });
    expect(payload.task.steps[1]).toMatchObject({
      instructions: "Primary screenshot instructions.",
      inputAttachments: [
        {
          filename: "primary.png",
          contentType: "image/png",
          sizeBytes: firstFile.size,
        },
      ],
    });
  });

  it("keeps same-name step attachments scoped to different owning steps", async () => {
    renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Review the first same-name attachment." },
    });
    const firstFile = new File(["first image"], "wireframe.png", {
      type: "image/png",
      lastModified: 10,
    });
    fireEvent.change(
      await screen.findByLabelText("Step 1 attachment file picker"),
      {
        target: { files: [firstFile] },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));
    const stepTwo = (await screen.findByText("Step 2")).closest("section");
    expect(stepTwo).not.toBeNull();
    fireEvent.change(
      within(stepTwo as HTMLElement).getByLabelText("Instructions"),
      {
        target: { value: "Review the second same-name attachment." },
      },
    );
    const secondFile = new File(["second image"], "wireframe.png", {
      type: "image/png",
      lastModified: 20,
    });
    fireEvent.change(
      await screen.findByLabelText("Step 2 attachment file picker"),
      {
        target: { files: [secondFile] },
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

    const payload = latestCreateRequest().payload as {
      task: {
        steps: Array<Record<string, unknown>>;
      };
    };
    expect(payload.task.steps[0]).toMatchObject({
      instructions: "Review the first same-name attachment.",
      inputAttachments: [
        {
          filename: "wireframe.png",
          contentType: "image/png",
          sizeBytes: firstFile.size,
        },
      ],
    });
    expect(payload.task.steps[1]).toMatchObject({
      instructions: "Review the second same-name attachment.",
      inputAttachments: [
        {
          filename: "wireframe.png",
          contentType: "image/png",
          sizeBytes: secondFile.size,
        },
      ],
    });
  });

  it("does not upload step attachments when later client validation fails", async () => {
    renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Review the provided screenshot." },
    });
    const attachmentInput = await screen.findByLabelText("Step 1 attachment file picker");
    fireEvent.change(attachmentInput, {
      target: {
        files: [
          new File(["fake image"], "wireframe.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));

    const additionalStep = (await screen.findByText("Step 2")).closest(
      "section",
    );
    expect(additionalStep).not.toBeNull();
    fireEvent.change(
      within(additionalStep as HTMLElement).getByLabelText(/Skill \(optional\)/),
      {
        target: { value: "pr-resolver" },
      },
    );
    fireEvent.click(screen.getByLabelText("Show advanced step options"));
    await waitFor(() => {
      expect(
        within(additionalStep as HTMLElement).getByLabelText(
          /Skill Args \(optional JSON object\)/,
        ),
      ).not.toBeNull();
    });
    fireEvent.change(
      within(additionalStep as HTMLElement).getByLabelText(
        /Skill Args \(optional JSON object\)/,
      ),
      {
        target: { value: '{"broken":' },
      },
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(
        screen.getByText("Step 2 Skill Args must be valid JSON object text."),
      ).not.toBeNull();
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url) === "/api/artifacts"),
    ).toBe(false);
  });

  it("does not upload step attachments when schedule validation fails", async () => {
    renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Review the provided screenshot." },
    });
    const attachmentInput = await screen.findByLabelText("Step 1 attachment file picker");
    fireEvent.change(attachmentInput, {
      target: {
        files: [
          new File(["fake image"], "wireframe.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.change(screen.getByLabelText("Schedule Mode"), {
      target: { value: "deferred_minutes" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(
        screen.getByText(
          "A valid positive whole number of minutes is required for deferred scheduling.",
        ),
      ).not.toBeNull();
    });
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url) === "/api/artifacts"),
    ).toBe(false);
  });

  it(
    "retries transient step attachment completion conflicts before creating the task",
    async () => {
      let completeAttempts = 0;
      const originalRetryDelays = [...ARTIFACT_COMPLETE_RETRY_DELAYS_MS];
      const defaultFetch = fetchSpy.getMockImplementation();

      try {
        ARTIFACT_COMPLETE_RETRY_DELAYS_MS.splice(
          0,
          ARTIFACT_COMPLETE_RETRY_DELAYS_MS.length,
          1,
          1,
        );
        fetchSpy.mockImplementation(
          (input: RequestInfo | URL, init?: RequestInit) => {
            const url = String(input);
            if (url === "/api/artifacts/art-001/complete") {
              completeAttempts += 1;
              if (completeAttempts < 3) {
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
            }
            return (
              defaultFetch?.(input, init) ??
              Promise.reject(new Error("Unhandled fetch"))
            );
          },
        );

        renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

        fireEvent.change(await screen.findByLabelText("Instructions"), {
          target: { value: "Review the provided screenshot." },
        });
        const attachmentInput =
          await screen.findByLabelText("Step 1 attachment file picker");
        fireEvent.change(attachmentInput, {
          target: {
            files: [
              new File(["fake image"], "wireframe.png", { type: "image/png" }),
            ],
          },
        });
        fireEvent.click(screen.getByRole("button", { name: "Create" }));

        await waitFor(
          () => {
            expect(completeAttempts).toBe(3);
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

  it("defaults publish mode to none when selecting the Jira Breakdown preset", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/task-step-templates?scope=global")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [
              {
                slug: "jira-breakdown",
                scope: "global",
                title: "Jira Breakdown",
                description: "Create Jira stories from a breakdown.",
                latestVersion: "1.0.0",
                version: "1.0.0",
              },
              {
                slug: "moonspec-orchestrate",
                scope: "global",
                title: "MoonSpec Orchestrate",
                description: "Keep the default preset off Jira Breakdown.",
                latestVersion: "1.0.0",
                version: "1.0.0",
              },
            ],
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) as ReturnType<typeof window.fetch>;
    });

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const presetSelect = await screen.findByLabelText("Preset");
    await waitFor(() => {
      expect(
        Array.from((presetSelect as HTMLSelectElement).options).some(
          (option) => option.text === "Jira Breakdown (Global)",
        ),
      ).toBe(true);
    });
    await waitFor(() => {
      expect((presetSelect as HTMLSelectElement).value).toBe(
        "global::::moonspec-orchestrate",
      );
    });

    const publishSelect = screen.getByLabelText(
      "Publish Mode",
    ) as HTMLSelectElement;
    expect(publishSelect.value).toBe("pr");

    fireEvent.change(presetSelect, {
      target: { value: "global::::jira-breakdown" },
    });

    await waitFor(() => {
      expect(
        (screen.getByLabelText("Publish Mode") as HTMLSelectElement).value,
      ).toBe("none");
    });
  });

  it("sets parent publish mode to none when selecting the Jira Breakdown and Orchestrate preset", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/task-step-templates?scope=global")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [
              {
                slug: "jira-breakdown-orchestrate",
                scope: "global",
                title: "Jira Breakdown and Orchestrate",
                description: "Create dependent Jira Orchestrate tasks.",
                latestVersion: "1.0.0",
                version: "1.0.0",
              },
              {
                slug: "moonspec-orchestrate",
                scope: "global",
                title: "MoonSpec Orchestrate",
                description: "Default preset.",
                latestVersion: "1.0.0",
                version: "1.0.0",
              },
            ],
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) as ReturnType<typeof window.fetch>;
    });

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const presetSelect = await screen.findByLabelText("Preset");
    await waitFor(() => {
      expect(
        Array.from((presetSelect as HTMLSelectElement).options).some(
          (option) => option.text === "Jira Breakdown and Orchestrate (Global)",
        ),
      ).toBe(true);
    });

    fireEvent.change(presetSelect, {
      target: { value: "global::::jira-breakdown-orchestrate" },
    });

    await waitFor(() => {
      expect(
        (screen.getByLabelText("Publish Mode") as HTMLSelectElement).value,
      ).toBe("none");
    });
  });

  it("preserves draft publish mode in edit mode when Jira Breakdown is the selected preset", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/task-step-templates?scope=global")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [
              {
                slug: "jira-breakdown",
                scope: "global",
                title: "Jira Breakdown",
                description: "Create Jira stories from a breakdown.",
                latestVersion: "1.0.0",
                version: "1.0.0",
              },
            ],
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) as ReturnType<typeof window.fetch>;
    });

    renderForEdit("mm:edit-123");

    expect(await screen.findByRole("heading", { name: "Edit Task" })).toBeTruthy();
    const presetSelect = await screen.findByLabelText("Preset");
    await waitFor(() => {
      expect((presetSelect as HTMLSelectElement).value).toBe(
        "global::::jira-breakdown",
      );
    });
    await waitFor(() => {
      expect(
        (screen.getByLabelText("Publish Mode") as HTMLSelectElement).value,
      ).toBe("branch");
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

  it("shows merge automation as a publish mode choice only for ordinary pr-publishing tasks", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const publishModeSelect = (await screen.findByLabelText(
      "Publish Mode",
    )) as HTMLSelectElement;
    expect(
      Array.from(publishModeSelect.options).some(
        (option) =>
          option.value === "pr_with_merge_automation" &&
          /merge automation/i.test(option.text),
      ),
    ).toBe(true);
    expect(screen.queryByLabelText("Enable merge automation")).toBeNull();
    expect(screen.queryByText(/uses pr-resolver/i)).toBeNull();
    expect(screen.queryByText(/direct auto-merge/i)).toBeNull();

    fireEvent.change(screen.getByLabelText("Publish Mode"), {
      target: { value: "branch" },
    });
    await waitFor(() => {
      expect(
        Array.from(
          (screen.getByLabelText("Publish Mode") as HTMLSelectElement).options,
        ).some((option) => option.value === "pr_with_merge_automation"),
      ).toBe(true);
    });

    fireEvent.change(screen.getByLabelText("Publish Mode"), {
      target: { value: "none" },
    });
    await waitFor(() => {
      expect(screen.queryByLabelText("Enable merge automation")).toBeNull();
    });

    fireEvent.change(screen.getByLabelText("Publish Mode"), {
      target: { value: "pr" },
    });
    expect(
      Array.from(
        (screen.getByLabelText("Publish Mode") as HTMLSelectElement).options,
      ).some((option) => option.value === "pr_with_merge_automation"),
    ).toBe(true);
  });

  it("submits merge automation with the existing pr publish contracts", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Implement MM-412." },
    });
    fireEvent.change(await screen.findByLabelText("Publish Mode"), {
      target: { value: "pr_with_merge_automation" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const request = latestCreateRequest();
    const payload = request.payload as Record<string, unknown>;
    const task = payload.task as Record<string, unknown>;
    expect(payload.publishMode).toBe("pr");
    expect(task.publish).toMatchObject({ mode: "pr" });
    expect(payload.mergeAutomation).toEqual({ enabled: true });
  });

  it("omits merge automation when publish mode is unavailable", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Implement without merge automation." },
    });
    fireEvent.change(screen.getByLabelText("Publish Mode"), {
      target: { value: "branch" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const payload = latestCreateRequest().payload as Record<string, unknown>;
    expect(payload.publishMode).toBe("branch");
    expect(payload).not.toHaveProperty("mergeAutomation");
  });

  it("omits merge automation when a resolver skill is selected", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const primaryStep = (await screen.findByText("Step 1 (Primary)")).closest(
      "section",
    );
    expect(primaryStep).not.toBeNull();
    fireEvent.change(await screen.findByLabelText("Publish Mode"), {
      target: { value: "pr_with_merge_automation" },
    });
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

    const payload = latestCreateRequest().payload as Record<string, unknown>;
    const task = payload.task as Record<string, unknown>;
    expect(task.publish).toMatchObject({ mode: "none" });
    expect(payload).not.toHaveProperty("mergeAutomation");
  });

  it("hides merge automation when the effective template skill is a resolver", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Publish Mode"), {
      target: { value: "pr_with_merge_automation" },
    });
    expect(
      (screen.getByLabelText("Publish Mode") as HTMLSelectElement).value,
    ).toBe("pr_with_merge_automation");

    const presetSelect = await screen.findByLabelText("Preset");
    await waitFor(() => {
      expect(
        Array.from((presetSelect as HTMLSelectElement).options).some(
          (option) => option.text === "PR Resolver (Global)",
        ),
      ).toBe(true);
    });
    fireEvent.change(presetSelect, {
      target: { value: "global::::pr-resolver" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));

    await screen.findByDisplayValue("Resolve the current branch PR.");
    await waitFor(() => {
      expect(
        (screen.getByLabelText("Publish Mode") as HTMLSelectElement).value,
      ).toBe("none");
    });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const payload = latestCreateRequest().payload as Record<string, unknown>;
    const task = payload.task as Record<string, unknown>;
    expect(payload.publishMode).toBe("none");
    expect(payload).not.toHaveProperty("mergeAutomation");
    expect(task.publish).toMatchObject({ mode: "none" });
    expect(task.skills).toEqual({ include: [{ name: "pr-resolver" }] });
  });

  it("submits deferred pr-resolver tasks with object-shaped skill selectors", async () => {
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
    fireEvent.change(screen.getByLabelText("Schedule Mode"), {
      target: { value: "deferred_minutes" },
    });
    fireEvent.change(screen.getByLabelText("Minutes from now"), {
      target: { value: "5" },
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
    expect(request.payload.task.skills).toEqual({
      include: [{ name: "pr-resolver" }],
    });
    expect(request.payload.schedule.mode).toBe("once");
    expect(
      new Date(request.payload.schedule.scheduledFor).getTime(),
    ).toBeGreaterThan(Date.now());
  });

  it("renders the restored legacy create-task controls", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(await screen.findByPlaceholderText("owner/repo")).not.toBeNull();
    expect(await screen.findByLabelText("Provider profile")).not.toBeNull();
    expect(
      await screen.findByLabelText("Feature Request / Initial Instructions"),
    ).not.toBeNull();
    expect(await screen.findByLabelText("Branch")).not.toBeNull();
    expect(screen.queryByLabelText("Target Branch (optional)")).toBeNull();
    expect(await screen.findByDisplayValue("3")).not.toBeNull();
    expect(screen.getByText("Task Presets (optional)")).not.toBeNull();
    expect(screen.getByText("Schedule (optional)")).not.toBeNull();
  });

  it("right-aligns Task Presets actions with Apply last", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const presetsSection = await screen.findByLabelText("Task Presets");
    const saveButton = within(presetsSection).getByRole("button", {
      name: "Save preset",
    });
    const deleteButton = within(presetsSection).getByRole("button", {
      name: "Delete preset",
    });
    const applyButton = within(presetsSection).getByRole("button", {
      name: "Apply",
    });
    const actionRow = applyButton.closest(".actions");

    expect(actionRow?.classList.contains("queue-template-actions")).toBe(true);
    expect(
      saveButton.compareDocumentPosition(applyButton) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      saveButton.compareDocumentPosition(deleteButton) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      deleteButton.compareDocumentPosition(applyButton) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(saveButton.getAttribute("title")).toBe(
      "Save the current steps as a reusable preset",
    );
    expect(saveButton.querySelector("svg")).not.toBeNull();
    expect(saveButton.textContent).toBe("");
    expect(deleteButton.getAttribute("title")).toBe("Choose a preset to delete");
    expect((deleteButton as HTMLButtonElement).disabled).toBe(true);
    expect(deleteButton.querySelector("svg")).not.toBeNull();
    expect(deleteButton.textContent).toBe("");
  });

  it("hides preset write actions when template saving is disabled", async () => {
    const disabledPayload = JSON.parse(JSON.stringify(mockPayload)) as BootPayload;
    (
      disabledPayload.initialData as {
        dashboardConfig: {
          system: { taskTemplateCatalog: { templateSaveEnabled: boolean } };
        };
      }
    ).dashboardConfig.system.taskTemplateCatalog.templateSaveEnabled = false;

    renderWithClient(<TaskCreatePage payload={disabledPayload} />);

    const presetsSection = await screen.findByLabelText("Task Presets");
    expect(
      within(presetsSection).queryByRole("button", { name: "Save preset" }),
    ).toBeNull();
    expect(
      within(presetsSection).queryByRole("button", { name: "Delete preset" }),
    ).toBeNull();
    expect(within(presetsSection).getByRole("button", { name: "Apply" }))
      .toBeTruthy();
  });

  it("disables preset deletion for global presets", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const presetsSection = await screen.findByLabelText("Task Presets");
    await within(presetsSection).findByRole("option", {
      name: "Spec Kit Demo (Global)",
    });
    fireEvent.change(within(presetsSection).getByLabelText("Preset"), {
      target: { value: "global::::speckit-demo" },
    });

    const deleteButton = within(presetsSection).getByRole("button", {
      name: "Delete preset",
    }) as HTMLButtonElement;
    await waitFor(() => {
      expect(deleteButton.disabled).toBe(true);
      expect(deleteButton.getAttribute("title")).toBe(
        "Only personal presets can be deleted",
      );
    });
    fireEvent.click(deleteButton);

    expect(
      fetchSpy.mock.calls.some(
        ([url, init]) =>
          String(url).startsWith("/api/task-step-templates/speckit-demo") &&
          init?.method === "DELETE",
      ),
    ).toBe(false);
  });

  it("deletes the selected preset from the Task Presets actions", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const presetsSection = await screen.findByLabelText("Task Presets");
    await within(presetsSection).findByRole("option", {
      name: "Personal Demo (Personal)",
    });
    fireEvent.change(within(presetsSection).getByLabelText("Preset"), {
      target: { value: "personal::::personal-demo" },
    });
    await waitFor(() => {
      expect(
        within(presetsSection)
          .getByRole("button", { name: "Delete preset" })
          .getAttribute("title"),
      ).toBe("Delete the selected preset");
    });
    fireEvent.click(
      within(presetsSection).getByRole("button", { name: "Delete preset" }),
    );

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/task-step-templates/personal-demo?scope=personal",
        expect.objectContaining({
          method: "DELETE",
        }),
      );
    });
    expect(confirmSpy).toHaveBeenCalledWith(
      "Delete preset 'Personal Demo'? This cannot be undone.",
    );
    expect(await screen.findByText("Deleted preset 'Personal Demo'.")).toBeTruthy();

    confirmSpy.mockRestore();
  });

  it("adds hover tooltips to Create page buttons", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    await screen.findByText("Step 1 (Primary)");

    expectAllButtonsHaveTitles();
    expect(
      screen.getByRole("button", { name: "Add Step" }).getAttribute("title"),
    ).toBe("Add another task step");
    expect(
      screen.getByRole("button", { name: "Create" }).getAttribute("title"),
    ).toBe("Create this task");
  });

  it("adds hover tooltips to Jira browser buttons", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    const dialog = await screen.findByRole("dialog", {
      name: "Browse Jira issue",
    });
    await waitFor(() => {
      expect(within(dialog).getByRole("button", { name: "To Do 1" }))
        .toBeTruthy();
    });

    expectAllButtonsHaveTitles(dialog);
    expect(
      within(dialog)
        .getByRole("button", { name: "Close Jira browser" })
        .getAttribute("title"),
    ).toBe("Close Jira browser");
    expect(
      within(dialog).getByRole("button", { name: "To Do 1" }).getAttribute("title"),
    ).toBe("Show Jira issues in To Do");
    expect(
      within(dialog).getByRole("button", { name: /ENG-101/ }).getAttribute("title"),
    ).toBe(
      "Import Jira issue ENG-101 into Feature Request / Initial Instructions",
    );
  });

  it("exposes the canonical Create page section order in create mode", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    await screen.findByText("Step 1 (Primary)");

    expect(canonicalCreateSections()).toEqual([
      "Header",
      "Steps",
      "Task Presets",
      "Dependencies",
      "Execution context",
      "Execution controls",
      "Schedule",
      "Submit",
    ]);
  });

  it("uses the same Create page composition surface for edit and rerun modes", async () => {
    const { unmount } = renderForEdit("mm:artifact-edit");

    await screen.findByRole("heading", { name: "Edit Task" });
    expect(canonicalCreateSections()).toEqual([
      "Header",
      "Steps",
      "Task Presets",
      "Dependencies",
      "Execution context",
      "Execution controls",
      "Submit",
    ]);
    unmount();

    window.history.pushState(
      {},
      "Task Rerun",
      "/tasks/new?rerunExecutionId=mm%3Arerun-123",
    );
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    await screen.findByRole("heading", { name: "Rerun Task" });
    expect(canonicalCreateSections()).toEqual([
      "Header",
      "Steps",
      "Task Presets",
      "Dependencies",
      "Execution context",
      "Execution controls",
      "Submit",
    ]);
  });

  it("keeps manual authoring available without optional presets Jira or image upload", async () => {
    renderWithClient(
      <TaskCreatePage payload={withoutOptionalAuthoringIntegrations()} />,
    );

    expect(await screen.findByText("Step 1 (Primary)")).not.toBeNull();
    expect(await screen.findByLabelText("Instructions")).not.toBeNull();
    expect(screen.queryByLabelText("Preset")).toBeNull();
    expect(screen.queryByText("Browse Jira issue")).toBeNull();
    expect(screen.queryByLabelText(/attachments/i)).toBeNull();
    expect(screen.getByRole("button", { name: "Create" })).not.toBeNull();
  });

  it("does not let Jira import bypass repository validation", async () => {
    renderWithClient(
      <TaskCreatePage payload={withJiraIntegration(withoutDefaultRepository())} />,
    );

    const stepInstructions = await screen.findByLabelText("Instructions");
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });
    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Complete Jira issue ENG-202: Build browser shell",
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(
      await screen.findByText(
        "Repository is required because no system default repository is configured.",
      ),
    ).toBeTruthy();
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url) === "/api/executions"),
    ).toBe(false);
  });

  it("does not let image upload bypass repository validation or upload artifacts first", async () => {
    renderWithClient(
      <TaskCreatePage payload={withAttachmentPolicy(withoutDefaultRepository())} />,
    );

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Review the provided screenshot." },
    });
    fireEvent.change(await screen.findByLabelText("Step 1 attachment file picker"), {
      target: {
        files: [
          new File(["fake image"], "wireframe.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(
      await screen.findByText(
        "Repository is required because no system default repository is configured.",
      ),
    ).toBeTruthy();
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url) === "/api/artifacts"),
    ).toBe(false);
    expect(
      fetchSpy.mock.calls.some(([url]) => String(url) === "/api/executions"),
    ).toBe(false);
  });

  it("keeps resolver publish restrictions after Jira import", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });

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
    await waitFor(() => {
      expect(screen.queryByLabelText("Enable merge automation")).toBeNull();
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const payload = latestCreateRequest().payload as Record<string, unknown>;
    const task = payload.task as Record<string, unknown>;
    expect(task.publish).toMatchObject({ mode: "none" });
    expect(payload).not.toHaveProperty("mergeAutomation");
  });

  it("keeps step authoring inside Steps and submission controls in one Submit floating bar", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const primaryStepLabel = await screen.findByText("Step 1 (Primary)");

    const stepsSection = document.querySelector<HTMLElement>(
      '[data-canonical-create-section="Steps"]',
    );
    expect(stepsSection).not.toBeNull();
    expect(stepsSection?.classList.contains("card")).toBe(true);
    expect(
      Array.from(document.querySelectorAll<HTMLElement>(".queue-step-section"))
        .some((element) => element.classList.contains("card")),
    ).toBe(false);

    const submitSection = document.querySelector<HTMLElement>(
      '[data-canonical-create-section="Submit"]',
    );
    expect(submitSection).not.toBeNull();

    const addStepButton = screen.getByRole("button", { name: "Add Step" });
    const createButton = screen.getByRole("button", { name: "Create" });
    const repoInput = screen.getByLabelText(/GitHub Repo/);
    const branchSelect = screen.getByLabelText("Branch");
    const publishModeSelect = screen.getByLabelText("Publish Mode");
    const stepExtension = addStepButton.closest(".queue-step-extension");
    const floatingBar = createButton.closest(".queue-floating-bar");
    const floatingBarRow = createButton.closest(".queue-floating-bar-row");

    expect(stepExtension).not.toBeNull();
    expect(floatingBar).not.toBeNull();
    expect(floatingBarRow).not.toBeNull();
    expect(addStepButton.closest('[data-canonical-create-section="Steps"]')).toBe(
      stepsSection,
    );
    expect(addStepButton.classList.contains("queue-step-extension-button")).toBe(
      true,
    );
    expect(floatingBar?.classList.contains("queue-step-submit-actions")).toBe(
      true,
    );
    expect(repoInput.closest(".queue-floating-bar-row")).toBe(floatingBarRow);
    expect(branchSelect.closest(".queue-floating-bar-row")).toBe(floatingBarRow);
    expect(publishModeSelect.closest(".queue-floating-bar-row")).toBe(
      floatingBarRow,
    );
    expect(createButton.closest('[data-canonical-create-section="Submit"]')).toBe(
      submitSection,
    );
    expect(repoInput.closest('[data-canonical-create-section="Submit"]')).toBe(
      submitSection,
    );
    expect(branchSelect.closest('[data-canonical-create-section="Submit"]')).toBe(
      submitSection,
    );
    expect(
      publishModeSelect.closest('[data-canonical-create-section="Submit"]'),
    ).toBe(submitSection);
    expect(createButton.classList.contains("queue-submit-primary--icon")).toBe(
      true,
    );
    expect(createButton.getAttribute("aria-label")).toBe("Create");
    expect(createButton.getAttribute("title")).toBe("Create this task");
    expect(
      repoInput.closest('[data-canonical-create-section="Steps"]'),
    ).toBeNull();
    expect(
      publishModeSelect.closest('[data-canonical-create-section="Execution context"]'),
    ).toBeNull();
    expect(screen.queryByLabelText("Enable merge automation")).toBeNull();
    expect(screen.queryByLabelText("Target Branch (optional)")).toBeNull();
    expect(
      primaryStepLabel.compareDocumentPosition(repoInput) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      primaryStepLabel.compareDocumentPosition(addStepButton) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      addStepButton.compareDocumentPosition(repoInput) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      repoInput.compareDocumentPosition(branchSelect) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      branchSelect.compareDocumentPosition(publishModeSelect) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      publishModeSelect.compareDocumentPosition(createButton) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      addStepButton.compareDocumentPosition(createButton) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("applies the liquid glass treatment to the bottom submission controls without changing accessible controls", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const createButton = await screen.findByRole("button", { name: "Create" });
    const floatingBar = createButton.closest<HTMLElement>(".queue-floating-bar");

    expect(floatingBar).not.toBeNull();
    expect(floatingBar?.classList.contains("queue-floating-bar--liquid-glass")).toBe(
      true,
    );
    expect(within(floatingBar as HTMLElement).getByLabelText("GitHub Repo")).toBe(
      screen.getByLabelText("GitHub Repo"),
    );
    expect(within(floatingBar as HTMLElement).getByLabelText("Branch")).toBe(
      screen.getByLabelText("Branch"),
    );
    expect(within(floatingBar as HTMLElement).getByLabelText("Publish Mode")).toBe(
      screen.getByLabelText("Publish Mode"),
    );
    expect(within(floatingBar as HTMLElement).getByRole("button", { name: "Create" })).toBe(
      createButton,
    );
  });

  it("gives repository, branch, and publish controls equal desktop space in the floating bar", async () => {
    const { readFileSync } = await import("node:fs");
    const missionControlCss = readFileSync(
      `${process.cwd()}/frontend/src/styles/mission-control.css`,
      "utf8",
    );

    expect(missionControlCss).toMatch(
      /\.queue-floating-bar-row\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)\s*auto/s,
    );
  });

  it("centers the constrained create page panel with equal side margins", async () => {
    const { readFileSync } = await import("node:fs");
    const missionControlCss = readFileSync(
      `${process.cwd()}/frontend/src/styles/mission-control.css`,
      "utf8",
    );

    expect(missionControlCss).toMatch(
      /\.panel:has\(\.task-create-page\)\s*\{[^}]*margin-inline:\s*auto/s,
    );
  });

  it("loads branches through MoonMind and submits one authored branch", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const branchInput = await screen.findByLabelText("Branch");
    expect(branchInput.getAttribute("list")).toBe("queue-branch-options");
    await waitFor(() => {
      const datalist = document.querySelector<HTMLDataListElement>(
        "#queue-branch-options",
      );
      expect(datalist).not.toBeNull();
      expect(
        Array.from(datalist?.querySelectorAll("option") ?? []).some(
          (option) => option.value === "feature/create-page",
        ),
      ).toBe(true);
    });
    expect(
      fetchSpy.mock.calls.some(([url]) =>
        String(url).startsWith(
          "/api/github/branches?repository=MoonLadderStudios%2FMoonMind",
        ),
      ),
    ).toBe(true);

    fireEvent.change(branchInput, {
      target: { value: "feature/create-page" },
    });
    fireEvent.change(screen.getByLabelText("Instructions"), {
      target: { value: "Implement single branch create page." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const payload = latestCreateRequest().payload as Record<string, unknown>;
    const task = payload.task as Record<string, unknown>;
    expect(task.git).toEqual({ branch: "feature/create-page" });
    expect(JSON.stringify(task)).not.toContain("targetBranch");
    expect(JSON.stringify(task)).not.toContain("startingBranch");
  });

  it("keeps branch loading text inside the dropdown only", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/github/branches")) {
        return new Promise<Response>(() => {});
      }
      return defaultFetch?.(input, init) as ReturnType<typeof fetch>;
    });

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const branchInput = (await screen.findByLabelText(
      "Branch",
    )) as HTMLInputElement;

    await waitFor(() => {
      expect(branchInput.getAttribute("placeholder")).toBe("Loading branches...");
    });
    expect(
      Array.from(document.querySelectorAll("p")).some(
        (element) => element.textContent?.trim() === "Loading branches...",
      ),
    ).toBe(false);
  });

  it("shows branch loading text below the dropdown when a selected branch is hidden during reload", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (
        url.startsWith(
          "/api/github/branches?repository=MoonLadderStudios%2FOtherRepo",
        )
      ) {
        return new Promise<Response>(() => {});
      }
      return defaultFetch?.(input, init) as ReturnType<typeof fetch>;
    });

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    const branchInput = (await screen.findByLabelText(
      "Branch",
    )) as HTMLInputElement;
    await waitFor(() => {
      const datalist = document.querySelector<HTMLDataListElement>(
        "#queue-branch-options",
      );
      expect(datalist).not.toBeNull();
      expect(
        Array.from(datalist?.querySelectorAll("option") ?? []).some(
          (option) => option.value === "feature/create-page",
        ),
      ).toBe(true);
    });

    fireEvent.change(branchInput, {
      target: { value: "feature/create-page" },
    });
    fireEvent.change(screen.getByLabelText(/GitHub Repo/), {
      target: { value: "MoonLadderStudios/OtherRepo" },
    });

    await waitFor(() => {
      expect(
        Array.from(document.querySelectorAll("p")).some(
          (element) => element.textContent?.trim() === "Loading branches...",
        ),
      ).toBe(true);
    });
  });

  it("loads branches for URL repository values accepted by submission", async () => {
    renderWithClient(
      <TaskCreatePage
        payload={withDefaultRepository(
          "https://github.com/MoonLadderStudios/MoonMind.git",
        )}
      />,
    );

    const branchInput = await screen.findByLabelText("Branch");
    expect(branchInput.getAttribute("list")).toBe("queue-branch-options");
    await waitFor(() => {
      const datalist = document.querySelector<HTMLDataListElement>(
        "#queue-branch-options",
      );
      expect(datalist).not.toBeNull();
      expect(
        Array.from(datalist?.querySelectorAll("option") ?? []).some(
          (option) => option.value === "feature/create-page",
        ),
      ).toBe(true);
    });
    expect(
      fetchSpy.mock.calls.some(([url]) =>
        String(url).startsWith(
          "/api/github/branches?repository=https%3A%2F%2Fgithub.com%2FMoonLadderStudios%2FMoonMind.git",
        ),
      ),
    ).toBe(true);
    expect(
      screen.queryByText("Branch lookup requires a valid GitHub repository value."),
    ).toBeNull();
  });

  it("uses only MoonMind REST endpoints while submitting a manually authored task", async () => {
    renderWithClient(
      <TaskCreatePage payload={withoutOptionalAuthoringIntegrations()} />,
    );

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Submit through MoonMind REST only." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const urls = fetchSpy.mock.calls.map(([url]) => String(url));
    expect(urls).toContain("/api/executions");
    expect(
      urls.every(
        (url) =>
          url.startsWith("/api/") ||
          url.startsWith("/api?") ||
          url === "/api",
      ),
    ).toBe(true);
    expect(
      urls.some((url) =>
        /atlassian|jira\.|amazonaws|storage\.googleapis|openai|anthropic|googleapis/i.test(
          url,
        ),
      ),
    ).toBe(false);
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
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
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
                jiraOrchestration: {},
                skill: {
                  id: "speckit-clarify",
                  args: { feature: "Task Create" },
                },
              },
              {
                id: "tpl:speckit-demo:1.2.3:02",
                title: "Plan implementation",
                instructions: "Write a plan for the task builder recovery.",
                storyOutput: {
                  mode: "jira",
                  jira: {
                    projectKey: "MM",
                    issueTypeName: "Story",
                    dependencyMode: "linear_blocker_chain",
                  },
                },
                jiraOrchestration: {
                  task: {
                    repository: "MoonLadderStudios/MoonMind",
                    runtime: { mode: "codex_cli" },
                    publish: {
                      mode: "pr",
                      mergeAutomation: { enabled: true },
                    },
                    orchestrationMode: "runtime",
                  },
                  traceability: { sourceIssueKey: "MM-404" },
                },
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
      return defaultFetch?.(input, init) as ReturnType<typeof window.fetch>;
    });

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
    expect(request.payload.task.skills).toEqual({
      include: [{ name: "speckit-clarify" }],
    });
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
        storyOutput: {
          mode: "jira",
          jira: {
            projectKey: "MM",
            issueTypeName: "Story",
            dependencyMode: "linear_blocker_chain",
          },
        },
        jiraOrchestration: {
          task: {
            repository: "MoonLadderStudios/MoonMind",
            runtime: { mode: "codex_cli" },
            publish: {
              mode: "pr",
              mergeAutomation: { enabled: true },
            },
            orchestrationMode: "runtime",
          },
          traceability: { sourceIssueKey: "MM-404" },
        },
      },
    ]);
    expect(request.payload.task.appliedStepTemplates).toEqual([
      expect.objectContaining({
        slug: "speckit-demo",
        version: "1.2.3",
      }),
    ]);
  });

  it("does not mutate the draft when selecting a preset before apply", async () => {
    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Keep this authored step." },
    });

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

    expect(screen.getByDisplayValue("Keep this authored step.")).toBeTruthy();
    expect(
      screen.queryByDisplayValue("Clarify the {{ inputs.feature_name }} scope."),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Apply" })).toBeTruthy();
  });

  it("marks an applied preset dirty when preset objective text changes manually", async () => {
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

    fireEvent.change(
      screen.getByLabelText("Feature Request / Initial Instructions"),
      {
        target: { value: "Task Create with revised objective" },
      },
    );

    expect(
      screen.getByText(
        "Preset instructions changed. Reapply the preset to regenerate preset-derived steps.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reapply preset" })).toBeTruthy();
    expect(
      screen.getByDisplayValue("Clarify the {{ inputs.feature_name }} scope."),
    ).toBeTruthy();
  });

  it("marks an applied preset dirty when objective attachments change and submits them as task attachments", async () => {
    renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

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

    const objectiveFile = new File(["objective image"], "objective.png", {
      type: "image/png",
    });
    fireEvent.change(
      await screen.findByLabelText(
        "Feature Request / Initial Instructions attachments",
      ),
      {
        target: { files: [objectiveFile] },
      },
    );

    expect(screen.getByRole("button", { name: "Reapply preset" })).toBeTruthy();
    expect(
      screen.getByDisplayValue("Clarify the {{ inputs.feature_name }} scope."),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const artifactCreateCall = fetchSpy.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/artifacts" &&
        String(init?.body || "").includes(
          "task-dashboard-objective-attachment",
        ),
    );
    expect(JSON.parse(String(artifactCreateCall?.[1]?.body))).toMatchObject({
      content_type: "image/png",
      size_bytes: objectiveFile.size,
      metadata: {
        filename: "objective.png",
        source: "task-dashboard-objective-attachment",
        target: "objective",
      },
    });

    const executionCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/executions")
      .at(-1);
    const request = JSON.parse(String(executionCall?.[1]?.body));
    expect(request.payload.task.inputAttachments).toEqual([
      {
        artifactId: "art-001",
        filename: "objective.png",
        contentType: "image/png",
        sizeBytes: objectiveFile.size,
      },
    ]);
    expect(request.payload.task.steps[0].inputAttachments).toBeUndefined();
  });

  it("detaches template step identity when a template-bound step attachment changes", async () => {
    renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

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

    const file = new File(["step image"], "step.png", { type: "image/png" });
    fireEvent.change(await screen.findByLabelText("Step 1 attachment file picker"), {
      target: { files: [file] },
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
    expect(request.payload.task.steps[0]).toEqual(
      expect.objectContaining({
        instructions: expect.stringContaining(
          "Clarify the {{ inputs.feature_name }} scope.",
        ),
        inputAttachments: [
          {
            artifactId: "art-001",
            filename: "step.png",
            contentType: "image/png",
            sizeBytes: file.size,
          },
        ],
      }),
    );
    expect([undefined, null, ""]).toContain(
      request.payload.task.steps[0]?.id,
    );
  });

  it("preserves template step identity when template attachments remain unchanged", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
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
                inputAttachments: [
                  {
                    artifactId: "art-template",
                    filename: "template.png",
                    contentType: "image/png",
                    sizeBytes: 10,
                  },
                ],
                skill: {
                  id: "speckit-clarify",
                  args: { feature: "Task Create" },
                },
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
      return defaultFetch?.(input, init) as ReturnType<typeof window.fetch>;
    });

    renderWithClient(<TaskCreatePage payload={withAttachmentPolicy()} />);

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
    expect(request.payload.task.steps[0]).toEqual(
      expect.objectContaining({
        id: "tpl:speckit-demo:1.2.3:01",
        instructions: "Clarify the {{ inputs.feature_name }} scope.",
      }),
    );
    expect(request.payload.task.steps[0].inputAttachments).toBeUndefined();
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
    fireEvent.click(screen.getByLabelText("Show advanced step options"));
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
      screen.getByRole("button", { name: "Save preset" }),
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

  it("persists advanced-mode skill args for auto steps when saving presets", async () => {
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
        target: { value: "Capture auto skill args in a preset." },
      },
    );
    fireEvent.click(screen.getByLabelText("Show advanced step options"));
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(
        "Step 1 Skill Args (optional JSON object)",
      ),
      {
        target: { value: '{"mode":"advanced"}' },
      },
    );
    fireEvent.change(
      within(primaryStep as HTMLElement).getByLabelText(
        /Step 1 Skill Required Capabilities \(optional CSV\)/,
      ),
      {
        target: { value: "docker" },
      },
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Save preset" }),
    );

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/task-step-templates/save-from-task",
        expect.objectContaining({
          method: "POST",
        }),
      );
    });

    const saveCall = fetchSpy.mock.calls
      .filter(([url]) => String(url) === "/api/task-step-templates/save-from-task")
      .at(-1);
    const request = JSON.parse(String(saveCall?.[1]?.body)) as {
      steps: Array<{
        tool?: Record<string, unknown>;
        skill?: Record<string, unknown>;
      }>;
    };
    const savedStep = request.steps[0];
    expect(savedStep).toBeDefined();
    expect(savedStep?.tool).toEqual({
      type: "skill",
      name: "auto",
      version: "1.0",
      inputs: { mode: "advanced" },
      requiredCapabilities: ["docker"],
    });
    expect(savedStep?.skill).toEqual({
      id: "auto",
      args: { mode: "advanced" },
      requiredCapabilities: ["docker"],
    });

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

  it("keeps manual task submission available when dependency options fail to load", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (
        url.startsWith(
          "/api/executions?source=temporal&pageSize=50&workflowType=MoonMind.Run&entry=run",
        )
      ) {
        return Promise.resolve({
          ok: false,
          status: 503,
          text: async () =>
            JSON.stringify({
              detail: {
                message: "Temporal visibility is unavailable.",
              },
            }),
        } as Response);
      }
      return defaultFetch?.(input, init) as ReturnType<typeof window.fetch>;
    });

    renderWithClient(<TaskCreatePage payload={mockPayload} />);

    expect(
      await screen.findByText(
        /Failed to load recent runs\. You can still create the task without dependencies/,
      ),
    ).toBeTruthy();
    fireEvent.change(await screen.findByLabelText("Instructions"), {
      target: { value: "Create the task manually after dependency lookup fails." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const request = latestCreateRequest();
    const task = (request.payload as Record<string, unknown>).task as Record<
      string,
      unknown
    >;
    expect(task.instructions).toBe(
      "Create the task manually after dependency lookup fails.",
    );
    expect(task).not.toHaveProperty("dependsOn");
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
      screen.queryByRole("button", { name: /Browse Jira issue/ }),
    ).toBeNull();
  });

  it("opens the Jira browser from the preset target", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    expect(
      await screen.findByRole("dialog", { name: "Browse Jira issue" }),
    ).toBeTruthy();
    expect(screen.getByText("Target: Feature Request / Initial Instructions"))
      .toBeTruthy();
  });

  it("opens the Jira browser from a step target", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for Step 1 instructions",
      }),
    );

    expect(
      await screen.findByRole("dialog", { name: "Browse Jira issue" }),
    ).toBeTruthy();
    expect(screen.getByText("Target: Step 1 Instructions")).toBeTruthy();
  });

  it("loads board columns in order and switches visible Jira issues by column", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
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

  it("renders Jira board items for task, bug, and sub-task issue types", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      if (path === "/api/jira/boards/42/issues") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            boardId: "42",
            columns: [
              { id: "todo", name: "To Do", count: 2 },
              { id: "doing", name: "Doing", count: 1 },
            ],
            itemsByColumn: {
              todo: [
                {
                  issueKey: "ENG-301",
                  summary: "Fix import bug",
                  issueType: "Bug",
                  statusName: "Selected",
                  assignee: "Ada",
                },
                {
                  issueKey: "ENG-302",
                  summary: "Wire task import",
                  issueType: "Task",
                  statusName: "Selected",
                  assignee: "Grace",
                },
              ],
              doing: [
                {
                  issueKey: "ENG-303",
                  summary: "Update browser copy",
                  issueType: "Sub-task",
                  statusName: "In Progress",
                  assignee: "Lin",
                },
              ],
            },
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    expect(await screen.findByText("ENG-301")).toBeTruthy();
    expect(screen.getByText("Fix import bug")).toBeTruthy();
    expect(screen.getByText("Bug / Selected / Ada")).toBeTruthy();
    expect(screen.getByText("ENG-302")).toBeTruthy();
    expect(screen.getByText("Task / Selected / Grace")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Doing 1" }));

    expect(await screen.findByText("ENG-303")).toBeTruthy();
    expect(screen.getByText("Sub-task / In Progress / Lin")).toBeTruthy();
  });

  it("uses validated Jira issue columns as the count source of truth", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      if (path === "/api/jira/boards/42/columns") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            board: { id: "42", name: "Delivery", projectKey: "ENG" },
            columns: [
              { id: "todo", name: "To Do", count: 7 },
              { id: "doing", name: "Doing", count: 9 },
              { id: "", count: 3 },
            ],
          }),
        } as Response);
      }
      if (path === "/api/jira/boards/42/issues") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            boardId: "42",
            columns: [
              { id: "todo", name: "To Do" },
              { id: "doing", name: "Doing", count: 2 },
              { id: 17, name: "Invalid" },
              { id: "missing-name", count: 1 },
            ],
            itemsByColumn: {
              todo: [
                {
                  issueKey: "ENG-101",
                  summary: "Plan controls",
                  issueType: "Story",
                  statusName: "Selected",
                  assignee: "Ada",
                },
              ],
              doing: [],
            },
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "To Do 0" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "Doing 2" })).toBeTruthy();
    });
    expect(screen.queryByRole("button", { name: "Invalid 0" })).toBeNull();
    expect(screen.queryByRole("button", { name: "missing-name 1" })).toBeNull();
  });

  it("sends the selected Jira project scope with board and issue requests", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });
    const requestUrls = fetchSpy.mock.calls.map(([input]) => String(input));
    expect(requestUrls).toContain("/api/jira/boards/42/columns?projectKey=ENG");
    expect(requestUrls).toContain("/api/jira/boards/42/issues?projectKey=ENG");
    expect(requestUrls).toContain(
      "/api/jira/issues/ENG-202?boardId=42&projectKey=ENG",
    );
  });

  it("shows project load failures only inside the Jira browser and keeps manual editing available", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jira/projects") {
        return Promise.resolve({
          ok: false,
          status: 503,
          text: async () =>
            JSON.stringify({
              detail: {
                code: "jira_provider_unavailable",
                message: "Jira is unavailable.",
              },
            }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const stepInstructions = await screen.findByLabelText("Instructions");
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    expect(
      await screen.findByText(
        "Failed to load Jira projects. You can continue creating the task manually. Jira is unavailable.",
      ),
    ).toBeTruthy();
    fireEvent.change(stepInstructions, {
      target: { value: "Write the task manually after Jira failed." },
    });

    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Write the task manually after Jira failed.",
    );
    expect((screen.getByRole("button", { name: "Create" }) as HTMLButtonElement).disabled)
      .toBe(false);
  });

  it("renders empty Jira browser states with manual continuation guidance", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jira/projects/ENG/boards") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [] }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    expect(
      await screen.findByText(
        "No Jira boards are available for this project. You can continue creating the task manually.",
      ),
    ).toBeTruthy();
    expect(screen.getByLabelText("Instructions")).toBeTruthy();
  });

  it("renders empty Jira projects with manual continuation guidance", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jira/projects") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [] }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    expect(
      await screen.findByText(
        "No Jira projects are available. You can continue creating the task manually.",
      ),
    ).toBeTruthy();
    expect(screen.getByLabelText("Instructions")).toBeTruthy();
  });

  it("renders empty Jira columns and issue lists with manual continuation guidance", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      if (path === "/api/jira/boards/42/columns") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            board: { id: "42", name: "Delivery", projectKey: "ENG" },
            columns: [{ id: "todo", name: "To Do", count: 0 }],
          }),
        } as Response);
      }
      if (path === "/api/jira/boards/42/issues") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            boardId: "42",
            columns: [{ id: "todo", name: "To Do", count: 0 }],
            itemsByColumn: { todo: [] },
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    expect(
      await screen.findByText(
        "No Jira issues are available in this column. You can continue creating the task manually.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /ENG-202/ })).toBeNull();
  });

  it("renders empty Jira columns with manual continuation guidance", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      if (path === "/api/jira/boards/42/columns") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            board: { id: "42", name: "Delivery", projectKey: "ENG" },
            columns: [],
          }),
        } as Response);
      }
      if (path === "/api/jira/boards/42/issues") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            boardId: "42",
            columns: [],
            itemsByColumn: {},
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    expect(
      await screen.findByText(
        "No Jira columns are available for this board. You can continue creating the task manually.",
      ),
    ).toBeTruthy();
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
      screen.queryByRole("button", { name: /Browse Jira issue/ }),
    ).toBeNull();
  });

  it("keeps the Jira browser disabled when endpoint templates are not MoonMind-owned paths", async () => {
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
                  ...(initialData.dashboardConfig.sources?.jira as Record<
                    string,
                    unknown
                  >),
                  projects: "https://jira.example.test/rest/api/3/project",
                },
              },
            },
          },
        }}
      />,
    );

    expect(await screen.findByText("Step 1 (Primary)")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /Browse Jira issue/ }),
    ).toBeNull();
  });

  it("keeps the Jira browser disabled when endpoint templates include padding", async () => {
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
                  ...(initialData.dashboardConfig.sources?.jira as Record<
                    string,
                    unknown
                  >),
                  projects: "/api/jira/projects ",
                },
              },
            },
          },
        }}
      />,
    );

    expect(await screen.findByText("Step 1 (Primary)")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /Browse Jira issue/ }),
    ).toBeNull();
  });

  it("does not restore Jira project or board defaults after a manual clear", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
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

  it("appends Jira issue text immediately when an issue is selected", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const presetInstructions = await screen.findByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });
    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "ENG-202: Build browser shell\n\nLet operators browse Jira stories.",
    );
    expect(
      screen.queryByText("Given a board, users can select a story preview."),
    ).toBeNull();
  });

  it("appends only to the selected target when selecting a Jira issue", async () => {
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
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });
    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing step instructions.",
    );
    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing preset instructions.\n\n---\n\nENG-202: Build browser shell\n\nLet operators browse Jira stories.",
    );
  });

  it("keeps issue-detail failures local and leaves import actions unavailable", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      if (path === "/api/jira/issues/ENG-202") {
        return Promise.resolve({
          ok: false,
          status: 502,
          text: async () =>
            JSON.stringify({
              detail: {
                code: "jira_browser_request_failed",
                message: "Jira issue detail failed.",
              },
            }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
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
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));

    expect(
      await screen.findByText(
        "Failed to load Jira issue. You can continue creating the task manually. Jira issue detail failed.",
      ),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: /ENG-202/ }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    expect(screen.queryByRole("button", { name: "Replace target text" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Append to target text" })).toBeNull();
    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing step instructions.",
    );
    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing preset instructions.",
    );
  });

  it("waits for a fresh Jira issue detail response before appending cached issue text", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    let issueDetailRequests = 0;
    const freshIssue = { resolve: null as (() => void) | null };
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      if (path === "/api/jira/issues/ENG-202") {
        issueDetailRequests += 1;
        if (issueDetailRequests === 1) {
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
                  "Complete Jira issue ENG-202: Build browser shell",
              },
            }),
          } as Response);
        }
        return new Promise<Response>((resolve) => {
          freshIssue.resolve = () => {
            resolve({
              ok: true,
              json: async () => ({
                issueKey: "ENG-202",
                url: "https://jira.example.test/browse/ENG-202",
                summary: "Build browser shell",
                issueType: "Story",
                column: { id: "doing", name: "Doing" },
                status: { id: "3", name: "In Progress" },
                descriptionText: "Fresh Jira issue details.",
                acceptanceCriteriaText:
                  "Given a board, users can select a story preview.",
                recommendedImports: {
                  presetInstructions:
                    "ENG-202: Build browser shell\n\nFresh Jira issue details.",
                  stepInstructions:
                    "Complete Jira issue ENG-202: Build browser shell",
                },
              }),
            } as Response);
          };
        });
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const presetInstructions = await screen.findByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });
    fireEvent.change(presetInstructions, {
      target: { value: "Reset instructions." },
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "To Do 1" }));
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));

    await waitFor(() => {
      expect(freshIssue.resolve).not.toBeNull();
    });
    expect(screen.getByRole("dialog", { name: "Browse Jira issue" })).toBeTruthy();
    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Reset instructions.",
    );

    freshIssue.resolve?.();
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });
    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Reset instructions.\n\n---\n\nENG-202: Build browser shell\n\nFresh Jira issue details.",
    );
  });

  it("appends preset instructions with selected Jira import text", async () => {
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
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });


    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing step instructions.",
    );
    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing preset instructions.\n\n---\n\nENG-202: Build browser shell\n\nLet operators browse Jira stories.",
    );
  });

  it("appends Jira import text to preset instructions", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const presetInstructions = await screen.findByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.change(presetInstructions, {
      target: { value: "Keep existing preset instructions." },
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });


    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing preset instructions.\n\n---\n\nENG-202: Build browser shell\n\nLet operators browse Jira stories.",
    );
  });

  it("appends only the selected step instructions with Jira import text", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const primaryStep = await screen.findByLabelText("Instructions");
    const presetInstructions = screen.getByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.change(primaryStep, {
      target: { value: "Keep primary instructions." },
    });
    fireEvent.change(presetInstructions, {
      target: { value: "Keep preset instructions." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));
    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));
    const stepInstructions = screen.getAllByLabelText("Instructions");
    const secondStep = stepInstructions[1] as HTMLTextAreaElement;
    const thirdStep = stepInstructions[2] as HTMLTextAreaElement;
    fireEvent.change(secondStep, {
      target: { value: "Replace this secondary step." },
    });
    fireEvent.change(thirdStep, {
      target: { value: "Keep tertiary instructions." },
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for Step 2 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });


    expect((primaryStep as HTMLTextAreaElement).value).toBe(
      "Keep primary instructions.",
    );
    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Keep preset instructions.",
    );
    expect(secondStep.value).toBe(
      "Replace this secondary step.\n\n---\n\nComplete Jira issue ENG-202: Build browser shell",
    );
    expect(thirdStep.value).toBe("Keep tertiary instructions.");
  });

  it("switches the Jira import target inside the browser before importing", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const primaryStep = await screen.findByLabelText("Instructions");
    fireEvent.change(primaryStep, {
      target: { value: "Keep the primary step." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add Step" }));
    const secondStep = screen.getAllByLabelText("Instructions")[1];
    if (!secondStep) {
      throw new Error("Expected second step instructions field.");
    }
    fireEvent.change(secondStep, {
      target: { value: "Replace this secondary step." },
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for Step 1 instructions",
      }),
    );
    fireEvent.change(await screen.findByLabelText("Import target"), {
      target: { value: "step-text:step-2" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });

    expect((primaryStep as HTMLTextAreaElement).value).toBe(
      "Keep the primary step.",
    );
    expect((secondStep as HTMLTextAreaElement).value).toBe(
      "Replace this secondary step.\n\n---\n\nComplete Jira issue ENG-202: Build browser shell",
    );
  });

  it("replaces step text when Jira text import mode is replace", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const stepInstructions = await screen.findByLabelText("Instructions");
    fireEvent.change(stepInstructions, {
      target: { value: "Replace this step text." },
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for Step 1 instructions",
      }),
    );
    fireEvent.change(await screen.findByLabelText("Text import"), {
      target: { value: "replace" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });

    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Complete Jira issue ENG-202: Build browser shell",
    );
  });

  it("uses execution brief text for step-target Jira imports", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const stepInstructions = await screen.findByLabelText("Instructions");
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });
    expect(screen.queryByLabelText("Import mode")).toBeNull();
    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Complete Jira issue ENG-202: Build browser shell",
    );
  });

  it("uses an unnamed Jira issue fallback when issue title metadata is empty", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      if (path === "/api/jira/issues/ENG-202") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            issueKey: "",
            url: "https://jira.example.test/browse/ENG-202",
            summary: "",
            issueType: "Story",
            column: { id: "doing", name: "Doing" },
            status: { id: "3", name: "In Progress" },
            descriptionText: "",
            acceptanceCriteriaText: "",
            recommendedImports: {
              presetInstructions: "",
              stepInstructions: "",
            },
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const stepInstructions = await screen.findByLabelText("Instructions");
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));

    await waitFor(() => {
      expect((stepInstructions as HTMLTextAreaElement).value).toBe(
        "Complete Jira issue (unnamed)",
      );
    });
  });

  it("preserves existing target text when selected Jira import text is empty", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      if (path === "/api/jira/issues/ENG-202") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            issueKey: "ENG-202",
            url: "https://jira.example.test/browse/ENG-202",
            summary: "Build browser shell",
            issueType: "Story",
            column: { id: "doing", name: "Doing" },
            status: { id: "3", name: "In Progress" },
            descriptionText: "",
            acceptanceCriteriaText: "",
            recommendedImports: {
              presetInstructions: "",
              stepInstructions: "",
            },
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
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
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "Browse Jira issue" }),
      ).toBeNull();
    });


    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing preset instructions.\n\n---\n\nENG-202: Build browser shell",
    );
    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "Browse Jira issue" }),
      ).toBeNull();
    });

    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Keep existing step instructions.",
    );
  });

  it("imports selected Jira text in the standard preset format", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const presetInstructions = await screen.findByLabelText(
      "Feature Request / Initial Instructions",
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });

    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "ENG-202: Build browser shell\n\nLet operators browse Jira stories.",
    );
  });

  it("marks preset instructions as needing reapply after Jira import changes an applied preset", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

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

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });


    expect(
      screen.getByText(
        "Preset instructions changed. Reapply the preset to regenerate preset-derived steps.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reapply preset" })).toBeTruthy();
    expect(
      screen.getByDisplayValue("Clarify the {{ inputs.feature_name }} scope."),
    ).toBeTruthy();

    fireEvent.change(
      screen.getByLabelText("Feature Request / Initial Instructions"),
      {
        target: { value: "Task Create" },
      },
    );
    expect(
      screen.queryByText(
        "Preset instructions changed. Reapply the preset to regenerate preset-derived steps.",
      ),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Apply" })).toBeTruthy();
  });

  it("marks preset instructions as needing reapply when Jira import appends to matching text", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

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
    const importedText =
      "ENG-202: Build browser shell\n\nLet operators browse Jira stories.";
    fireEvent.change(
      screen.getByLabelText("Feature Request / Initial Instructions"),
      {
        target: { value: importedText },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await screen.findByDisplayValue(
      "Clarify the {{ inputs.feature_name }} scope.",
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });


    expect(
      screen.getByText(
        "Preset instructions changed. Reapply the preset to regenerate preset-derived steps.",
      ),
    ).toBeTruthy();
  });

  it("detaches template step identity when Jira import edits a template-bound step", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

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

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for Step 2 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
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
    expect(request.payload.task.steps).toEqual([
      expect.objectContaining({
        id: "tpl:speckit-demo:1.2.3:01",
        instructions: "Clarify the {{ inputs.feature_name }} scope.",
      }),
      expect.objectContaining({
        instructions:
          "Write a plan for the task builder recovery.\n\n---\n\nComplete Jira issue ENG-202: Build browser shell",
      }),
    ]);
    expect([undefined, null, ""]).toContain(
      request.payload.task.steps[1]?.id,
    );
  });

  it("warns before importing Jira text into a template-bound step", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

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

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for Step 2 instructions",
      }),
    );

    expect(
      await screen.findByText(
        "Importing into this template-bound step will make it manually customized.",
      ),
    ).toBeTruthy();

    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });


    expect(
      screen.queryByText(
        "Importing into this template-bound step will make it manually customized.",
      ),
    ).toBeNull();
  });

  it("shows Jira provenance chips after importing into preset and step targets", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });


    expect(
      screen.getByLabelText(
        "Jira import provenance for Feature Request / Initial Instructions",
      ).textContent,
    ).toBe("Jira: ENG-202");
    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "Browse Jira issue" }),
      ).toBeNull();
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });


    expect(
      screen.getByLabelText("Jira import provenance for Step 1 instructions")
        .textContent,
    ).toBe("Jira: ENG-202");
  });

  it("keeps a reopened Jira browser open after a slow image import finishes", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    const imageDownload: { resolve: (() => void) | null } = { resolve: null };
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      if (path === "/api/jira/issues/ENG-202") {
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
                "Complete Jira issue ENG-202: Build browser shell",
            },
            attachments: [
              {
                id: "img-1",
                filename: "wireframe.png",
                contentType: "image/png",
                sizeBytes: 10,
                downloadUrl: "/api/jira/attachments/wireframe.png",
              },
            ],
          }),
        } as Response);
      }
      if (path === "/api/jira/attachments/wireframe.png") {
        return new Promise<Response>((resolve) => {
          imageDownload.resolve = () => {
            resolve({
              ok: true,
              blob: async () => new Blob(["fake image"], { type: "image/png" }),
            } as Response);
          };
        });
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(
      <TaskCreatePage payload={withAttachmentPolicy(withJiraIntegration())} />,
    );

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira images for objective attachments",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });

    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "Browse Jira issue" }),
      ).toBeNull();
    });
    await waitFor(() => {
      expect(imageDownload.resolve).not.toBeNull();
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira images for Step 1 attachments",
      }),
    );
    expect(
      await screen.findByRole("dialog", { name: "Browse Jira issue" }),
    ).toBeTruthy();

    if (!imageDownload.resolve) {
      throw new Error("Expected Jira image download to be pending.");
    }
    imageDownload.resolve();
    expect(await screen.findByText("wireframe.png")).toBeTruthy();
    expect(
      screen.getByRole("dialog", { name: "Browse Jira issue" }),
    ).toBeTruthy();
  });

  it("imports Jira images into the objective attachment target without changing text", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      if (path === "/api/jira/issues/ENG-202") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            issueKey: "ENG-202",
            summary: "Build browser shell",
            issueType: "Story",
            column: { id: "doing", name: "Doing" },
            status: { id: "3", name: "In Progress" },
            descriptionText: "Let operators browse Jira stories.",
            recommendedImports: {
              presetInstructions:
                "ENG-202: Build browser shell\n\nLet operators browse Jira stories.",
              stepInstructions:
                "Complete Jira issue ENG-202: Build browser shell",
            },
            attachments: [
              {
                id: "img-1",
                filename: "wireframe.png",
                contentType: "image/png",
                sizeBytes: 10,
                downloadUrl: "/api/jira/attachments/wireframe.png",
              },
            ],
          }),
        } as Response);
      }
      if (path === "/api/jira/attachments/wireframe.png") {
        return Promise.resolve({
          ok: true,
          blob: async () => new Blob(["fake image"], { type: "image/png" }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(
      <TaskCreatePage payload={withAttachmentPolicy(withJiraIntegration())} />,
    );

    const presetInstructions = await screen.findByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.change(presetInstructions, {
      target: { value: "Keep objective text." },
    });
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira images for objective attachments",
      }),
    );
    expect(
      (await screen.findByLabelText("Import target") as HTMLSelectElement).value,
    ).toBe("preset-attachments");
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });

    expect((presetInstructions as HTMLTextAreaElement).value).toBe(
      "Keep objective text.",
    );
    expect(await screen.findByText("wireframe.png")).toBeTruthy();
  });

  it("imports Jira images into a step attachment target", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      if (path === "/api/jira/issues/ENG-202") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            issueKey: "ENG-202",
            summary: "Build browser shell",
            issueType: "Story",
            column: { id: "doing", name: "Doing" },
            status: { id: "3", name: "In Progress" },
            descriptionText: "Let operators browse Jira stories.",
            recommendedImports: {
              presetInstructions:
                "ENG-202: Build browser shell\n\nLet operators browse Jira stories.",
              stepInstructions:
                "Complete Jira issue ENG-202: Build browser shell",
            },
            attachments: [
              {
                id: "img-1",
                filename: "wireframe.png",
                contentType: "image/png",
                sizeBytes: 10,
                downloadUrl: "/api/jira/attachments/wireframe.png",
              },
            ],
          }),
        } as Response);
      }
      if (path === "/api/jira/attachments/wireframe.png") {
        return Promise.resolve({
          ok: true,
          blob: async () => new Blob(["fake image"], { type: "image/png" }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(
      <TaskCreatePage payload={withAttachmentPolicy(withJiraIntegration())} />,
    );

    const stepInstructions = await screen.findByLabelText("Instructions");
    fireEvent.change(stepInstructions, {
      target: { value: "Keep step text." },
    });
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira images for Step 1 attachments",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });

    expect((stepInstructions as HTMLTextAreaElement).value).toBe(
      "Keep step text.",
    );
    expect(await screen.findByText("wireframe.png")).toBeTruthy();
  });

  it("does not import Jira images when importing into text targets", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    let attachmentDownloads = 0;
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      if (path === "/api/jira/issues/ENG-202") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            issueKey: "ENG-202",
            summary: "Build browser shell",
            issueType: "Story",
            column: { id: "doing", name: "Doing" },
            status: { id: "3", name: "In Progress" },
            descriptionText: "Let operators browse Jira stories.",
            recommendedImports: {
              presetInstructions:
                "ENG-202: Build browser shell\n\nLet operators browse Jira stories.",
              stepInstructions:
                "Complete Jira issue ENG-202: Build browser shell",
            },
            attachments: [
              {
                id: "img-1",
                filename: "wireframe.png",
                contentType: "image/png",
                sizeBytes: 10,
                downloadUrl: "/api/jira/attachments/wireframe.png",
              },
            ],
          }),
        } as Response);
      }
      if (path === "/api/jira/attachments/wireframe.png") {
        attachmentDownloads += 1;
        return Promise.resolve({
          ok: true,
          blob: async () => new Blob(["fake image"], { type: "image/png" }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(
      <TaskCreatePage payload={withAttachmentPolicy(withJiraIntegration())} />,
    );

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });

    expect(attachmentDownloads).toBe(0);
    expect(screen.queryByText("wireframe.png")).toBeNull();
    expect(
      (screen.getByLabelText(
        "Feature Request / Initial Instructions",
      ) as HTMLTextAreaElement).value,
    ).toBe("ENG-202: Build browser shell\n\nLet operators browse Jira stories.");
    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe(
      "Complete Jira issue ENG-202: Build browser shell",
    );
  });

  it("detaches template step identity and records provenance after Jira step attachment import", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      if (path === "/api/jira/issues/ENG-202") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            issueKey: "ENG-202",
            summary: "Build browser shell",
            issueType: "Story",
            column: { id: "doing", name: "Doing" },
            status: { id: "3", name: "In Progress" },
            descriptionText: "Let operators browse Jira stories.",
            recommendedImports: {
              presetInstructions:
                "ENG-202: Build browser shell\n\nLet operators browse Jira stories.",
              stepInstructions:
                "Complete Jira issue ENG-202: Build browser shell",
            },
            attachments: [
              {
                id: "img-1",
                filename: "wireframe.png",
                contentType: "image/png",
                sizeBytes: 10,
                downloadUrl: "/api/jira/attachments/wireframe.png",
              },
            ],
          }),
        } as Response);
      }
      if (path === "/api/jira/attachments/wireframe.png") {
        return Promise.resolve({
          ok: true,
          blob: async () => new Blob(["fake image"], { type: "image/png" }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(
      <TaskCreatePage payload={withAttachmentPolicy(withJiraIntegration())} />,
    );

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

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira images for Step 1 attachments",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });

    expect(await screen.findByText("wireframe.png")).toBeTruthy();
    expect(
      screen.getByLabelText("Jira import provenance for Step 1 instructions")
        .textContent,
    ).toBe("Jira: ENG-202");

    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/executions",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const request = latestCreateRequest();
    const task = request.payload as {
      task: { steps: Array<Record<string, unknown>> };
    };
    expect([undefined, null, ""]).toContain(task.task.steps[0]?.id);
    expect(task.task.steps[0]?.inputAttachments).toEqual([
      {
        artifactId: "art-001",
        filename: "wireframe.png",
        contentType: "image/png",
        sizeBytes: 10,
      },
    ]);
  });

  it("reopens Jira from an imported field with the prior issue selected", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });

    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "Browse Jira issue" }),
      ).toBeNull();
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    expect(
      await screen.findByRole("dialog", { name: "Browse Jira issue" }),
    ).toBeTruthy();
    expect((screen.getByLabelText("Project") as HTMLSelectElement).value).toBe(
      "ENG",
    );
    expect((screen.getByLabelText("Board") as HTMLSelectElement).value).toBe(
      "42",
    );
    expect(
      screen
        .getByRole("button", { name: "Doing 1" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("reopens Jira from an imported field with a hyphenated project key selected", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    await screen.findByText("Hyphenated Project (MY-PROJ)");
    fireEvent.change(screen.getByLabelText("Project"), {
      target: { value: "MY-PROJ" },
    });
    await screen.findByText("Hyphenated Delivery");
    fireEvent.change(screen.getByLabelText("Board"), {
      target: { value: "84" },
    });
    await waitFor(() => {
      expect((screen.getByLabelText("Board") as HTMLSelectElement).value).toBe(
        "84",
      );
    });
    fireEvent.click(await screen.findByRole("button", { name: "Selected 0" }));
    fireEvent.click(
      await screen.findByRole("button", { name: /MY-PROJ-123/ }),
    );
    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "Browse Jira issue" }),
      ).toBeNull();
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    expect(
      await screen.findByRole("dialog", { name: "Browse Jira issue" }),
    ).toBeTruthy();
    expect((screen.getByLabelText("Project") as HTMLSelectElement).value).toBe(
      "MY-PROJ",
    );
    expect((screen.getByLabelText("Board") as HTMLSelectElement).value).toBe(
      "84",
    );
  });

  it("clears Jira provenance chips when imported text is manually edited", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const presetInstructions = await screen.findByLabelText(
      "Feature Request / Initial Instructions",
    );
    const stepInstructions = screen.getByLabelText("Instructions");

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });


    expect(
      screen.getByLabelText(
        "Jira import provenance for Feature Request / Initial Instructions",
      ),
    ).toBeTruthy();
    fireEvent.change(presetInstructions, {
      target: { value: "Manual preset instructions." },
    });
    expect(
      screen.queryByLabelText(
        "Jira import provenance for Feature Request / Initial Instructions",
      ),
    ).toBeNull();
    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "Browse Jira issue" }),
      ).toBeNull();
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });


    expect(
      screen.getByLabelText("Jira import provenance for Step 1 instructions"),
    ).toBeTruthy();
    fireEvent.change(stepInstructions, {
      target: { value: "Manual step instructions." },
    });
    expect(
      screen.queryByLabelText("Jira import provenance for Step 1 instructions"),
    ).toBeNull();
  });

  it("clears step Jira provenance when the imported step is removed", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });


    expect(
      screen.getByLabelText("Jira import provenance for Step 1 instructions"),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove step" }));
    expect(
      screen.queryByLabelText("Jira import provenance for Step 1 instructions"),
    ).toBeNull();
  });

  it("does not render a Jira provenance chip when the imported issue has no issue key", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      if (path === "/api/jira/issues/ENG-202") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            issueKey: "",
            url: "https://jira.example.test/browse/ENG-202",
            summary: "Build browser shell",
            issueType: "Story",
            column: { id: "doing", name: "Doing" },
            status: { id: "3", name: "In Progress" },
            descriptionText: "Let operators browse Jira stories.",
            acceptanceCriteriaText: "",
            recommendedImports: {
              presetInstructions: "Let operators browse Jira stories.",
              stepInstructions: "Complete the Jira browser story.",
            },
          }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });


    expect(
      screen.queryByLabelText(
        "Jira import provenance for Feature Request / Initial Instructions",
      ),
    ).toBeNull();
  });

  it("keeps Jira provenance out of the task submission payload", async () => {
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for Step 1 instructions",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Doing 1" }));
    fireEvent.click(await screen.findByRole("button", { name: /ENG-202/ }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Browse Jira issue" })).toBeNull();
    });

    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "Browse Jira issue" }),
      ).toBeNull();
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
    const requestKeys = collectObjectKeys(request);
    expect(requestKeys).not.toContain("jiraProvenance");
    expect(requestKeys).not.toContain("sessionJiraSelection");
    expect(requestKeys).not.toContain("issueKey");
    expect(requestKeys).not.toContain("boardId");
    expect(requestKeys).not.toContain("importMode");
    expect(requestKeys).not.toContain("targetType");
  });

  it("submits a manual task with unchanged payload shape after Jira failure", async () => {
    const defaultFetch = fetchSpy.getMockImplementation();
    fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jira/projects") {
        return Promise.resolve({
          ok: false,
          status: 503,
          text: async () =>
            JSON.stringify({
              detail: {
                code: "jira_provider_unavailable",
                message: "Jira is unavailable.",
              },
            }),
        } as Response);
      }
      return defaultFetch?.(input, init) ?? Promise.reject(new Error("fetch missing"));
    });
    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    const stepInstructions = await screen.findByLabelText("Instructions");
    const presetInstructions = screen.getByLabelText(
      "Feature Request / Initial Instructions",
    );
    fireEvent.change(presetInstructions, {
      target: { value: "Manual objective after Jira failure." },
    });
    fireEvent.change(stepInstructions, {
      target: { value: "Manual step after Jira failure." },
    });
    fireEvent.click(
      screen.getByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );
    expect(
      await screen.findByText(
        "Failed to load Jira projects. You can continue creating the task manually. Jira is unavailable.",
      ),
    ).toBeTruthy();
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
    expect(request.payload.task.instructions).toBe(
      "Manual objective after Jira failure.",
    );
    expect(request.payload.task.steps[0].instructions).toBe(
      "Manual step after Jira failure.",
    );
    const requestKeys = collectObjectKeys(request);
    expect(requestKeys).not.toContain("jiraProvenance");
    expect(requestKeys).not.toContain("jiraFailure");
    expect(requestKeys).not.toContain("issueKey");
  });

  it("restores the last selected Jira project and board from session storage", async () => {
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-project-key",
      "ENG",
    );
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-board-id",
      "7",
    );

    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    const projectSelect = await screen.findByLabelText("Project");
    const boardSelect = screen.getByLabelText("Board");
    await waitFor(() => {
      expect((projectSelect as HTMLSelectElement).value).toBe("ENG");
      expect((boardSelect as HTMLSelectElement).value).toBe("7");
    });
  });

  it("falls back when remembered Jira project and board selections are stale", async () => {
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-project-key",
      "OLD",
    );
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-board-id",
      "999",
    );

    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    const projectSelect = await screen.findByLabelText("Project");
    const boardSelect = screen.getByLabelText("Board");
    await waitFor(() => {
      expect((projectSelect as HTMLSelectElement).value).toBe("ENG");
      expect((boardSelect as HTMLSelectElement).value).toBe("42");
    });
    expect(
      window.sessionStorage.getItem("moonmind.task-create.jira.last-project-key"),
    ).toBeNull();
    expect(window.sessionStorage.getItem("moonmind.task-create.jira.last-board-id"))
      .toBeNull();
  });

  it("does not write or restore Jira project and board session memory when disabled", async () => {
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-project-key",
      "ENG",
    );
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-board-id",
      "7",
    );

    renderWithClient(
      <TaskCreatePage payload={withJiraSessionMemory(false)} />,
    );

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    const projectSelect = await screen.findByLabelText("Project");
    const boardSelect = screen.getByLabelText("Board");
    await waitFor(() => {
      expect((projectSelect as HTMLSelectElement).value).toBe("ENG");
      expect((boardSelect as HTMLSelectElement).value).toBe("42");
    });

    window.sessionStorage.clear();
    fireEvent.change(boardSelect, { target: { value: "7" } });
    expect(window.sessionStorage.getItem("moonmind.task-create.jira.last-board-id"))
      .toBeNull();
  });

  it("clears remembered Jira project and board when selections are manually cleared", async () => {
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-project-key",
      "ENG",
    );
    window.sessionStorage.setItem(
      "moonmind.task-create.jira.last-board-id",
      "7",
    );

    renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Browse Jira issues for preset instructions",
      }),
    );

    const projectSelect = await screen.findByLabelText("Project");
    const boardSelect = screen.getByLabelText("Board");
    await waitFor(() => {
      expect((projectSelect as HTMLSelectElement).value).toBe("ENG");
      expect((boardSelect as HTMLSelectElement).value).toBe("7");
    });

    fireEvent.change(projectSelect, { target: { value: "" } });
    expect(window.sessionStorage.getItem("moonmind.task-create.jira.last-project-key"))
      .toBeNull();
    expect(window.sessionStorage.getItem("moonmind.task-create.jira.last-board-id"))
      .toBeNull();

    fireEvent.change(projectSelect, { target: { value: "ENG" } });
    fireEvent.change(boardSelect, { target: { value: "7" } });
    expect(window.sessionStorage.getItem("moonmind.task-create.jira.last-board-id"))
      .toBe("7");

    fireEvent.change(boardSelect, { target: { value: "" } });
    expect(window.sessionStorage.getItem("moonmind.task-create.jira.last-board-id"))
      .toBeNull();
  });

  it("keeps Jira browsing and manual task creation available when session storage fails", async () => {
    const getItemSpy = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation((key: string) => {
        if (key.startsWith("moonmind.task-create.jira.")) {
          throw new Error("session storage unavailable");
        }
        return null;
      });
    const setItemSpy = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation((key: string) => {
        if (key.startsWith("moonmind.task-create.jira.")) {
          throw new Error("session storage unavailable");
        }
      });
    const removeItemSpy = vi
      .spyOn(Storage.prototype, "removeItem")
      .mockImplementation((key: string) => {
        if (key.startsWith("moonmind.task-create.jira.")) {
          throw new Error("session storage unavailable");
        }
      });

    try {
      renderWithClient(<TaskCreatePage payload={withJiraIntegration()} />);

      fireEvent.click(
        await screen.findByRole("button", {
          name: "Browse Jira issues for preset instructions",
        }),
      );

      expect(
        await screen.findByRole("dialog", { name: "Browse Jira issue" }),
      ).toBeTruthy();
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
    } finally {
      getItemSpy.mockRestore();
      setItemSpy.mockRestore();
      removeItemSpy.mockRestore();
    }
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
        name: "Browse Jira issues for preset instructions",
      }),
    );

    expect(
      await screen.findByText(
        "Failed to load Jira projects. You can continue creating the task manually. Jira unavailable",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Browse Jira issue" }))
      .toBeTruthy();
    expect(screen.getByLabelText("Instructions")).toBeTruthy();
  });

  it("keeps structured Jira endpoint failures local and manual creation available", async () => {
    fetchSpy.mockImplementation(
      (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/jira/projects") {
          return Promise.resolve({
            ok: false,
            status: 502,
            statusText: "Bad Gateway",
            text: async () =>
              JSON.stringify({
                detail: {
                  code: "jira_request_failed",
                  message: "Jira was temporarily unavailable.",
                  source: "jira_browser",
                },
              }),
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
        name: "Browse Jira issues for preset instructions",
      }),
    );
    expect(
      await screen.findByText(
        "Failed to load Jira projects. You can continue creating the task manually. Jira was temporarily unavailable.",
      ),
    ).toBeTruthy();

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
