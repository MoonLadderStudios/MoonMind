import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { mountPage } from "../boot/mountPage";
import type { BootPayload } from "../boot/parseBootPayload";
import { navigateTo } from "../lib/navigation";

// This cutoff is enforced on UTF-8 encoded request bytes, not JavaScript string length.
const INLINE_TASK_INPUT_LIMIT_BYTES = 8_000;
const ARTIFACT_COMPLETE_RETRY_DELAYS_MS = [250, 500, 1000, 2000, 2000];
const ARTIFACT_COMPLETE_RETRY_MESSAGE = "artifact upload is not complete";
const SKILL_OPTIONS_DATALIST_ID = "queue-skill-options";
const MODEL_OPTIONS_DATALIST_ID = "queue-model-options";
const EFFORT_OPTIONS_DATALIST_ID = "queue-effort-options";
const OWNER_REPO_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const PR_RESOLVER_SKILLS = new Set(["pr-resolver", "batch-pr-resolver"]);
const PROPOSE_TASKS_PREFERENCE_KEY = "moonmind.task-create.propose-tasks";

function readProposeTasksPreference(defaultValue: boolean): boolean {
  try {
    const raw = window.localStorage.getItem(PROPOSE_TASKS_PREFERENCE_KEY);
    if (raw === null) {
      return defaultValue;
    }
    if (raw === "true" || raw === "1") {
      return true;
    }
    if (raw === "false" || raw === "0") {
      return false;
    }
  } catch {
    // Preserve default behavior when localStorage is unavailable.
  }
  return defaultValue;
}

function writeProposeTasksPreference(value: boolean): void {
  try {
    window.localStorage.setItem(
      PROPOSE_TASKS_PREFERENCE_KEY,
      value ? "true" : "false",
    );
  } catch {
    // Ignore localStorage write failures to keep task submission behavior unaffected.
  }
}

type TemplateScope = "global" | "personal";
type ScheduleMode = "immediate" | "once" | "deferred_minutes" | "recurring";

interface DashboardConfig {
  sources?: {
    temporal?: {
      create?: string;
      artifactCreate?: string;
    };
  };
  system?: {
    defaultRepository?: string;
    defaultTaskRuntime?: string;
    defaultTaskModel?: string;
    defaultTaskEffort?: string;
    defaultPublishMode?: string;
    defaultProposeTasks?: boolean;
    defaultTaskModelByRuntime?: Record<string, string>;
    defaultTaskEffortByRuntime?: Record<string, string>;
    supportedTaskRuntimes?: string[];
    providerProfiles?: {
      list?: string;
    };
    taskTemplateCatalog?: {
      enabled?: boolean;
      templateSaveEnabled?: boolean;
      list?: string;
      detail?: string;
      expand?: string;
      saveFromTask?: string;
    };
    attachmentPolicy?: {
      enabled?: boolean;
      maxCount?: number;
      maxBytes?: number;
      totalBytes?: number;
      allowedContentTypes?: string[];
    };
  };
}

interface ProviderProfile {
  profile_id: string;
  account_label?: string | null;
  default_model?: string | null;
}

interface SkillsResponse {
  items?: {
    worker?: string[];
  };
}

interface ExecutionCreateResponse {
  workflowId?: string;
  runId?: string;
  temporalRunId?: string;
  namespace?: string;
  redirectPath?: string;
  definitionId?: string;
}

interface TaskTemplateInputDefinition {
  name: string;
  label: string;
  type:
    | "text"
    | "textarea"
    | "markdown"
    | "enum"
    | "boolean"
    | "user"
    | "team"
    | "repo_path";
  required?: boolean;
  default?: unknown;
  options?: string[];
}

interface TaskTemplateSummary {
  slug: string;
  scope: TemplateScope;
  scopeRef?: string | null;
  title: string;
  description: string;
  latestVersion: string;
  version: string;
}

interface TaskTemplateDetail extends TaskTemplateSummary {
  inputs?: TaskTemplateInputDefinition[];
}

interface TaskTemplateListResponse {
  items?: TaskTemplateSummary[];
}

interface TaskTemplateStepSkill {
  id?: string;
  name?: string;
  args?: Record<string, unknown>;
  inputs?: Record<string, unknown>;
  requiredCapabilities?: string[];
}

interface ExpandedStepPayload {
  id?: string;
  title?: string;
  instructions?: string;
  skill?: TaskTemplateStepSkill;
  tool?: TaskTemplateStepSkill;
}

interface TaskTemplateExpandResponse {
  steps?: ExpandedStepPayload[];
  appliedTemplate?: {
    slug?: string;
    version?: string;
    inputs?: Record<string, unknown>;
    stepIds?: string[];
    appliedAt?: string;
  };
  capabilities?: string[];
}

interface TemplateOption extends TaskTemplateSummary {
  key: string;
}

interface TemplateCatalogResult {
  items: TemplateOption[];
  failedScopes: TemplateScope[];
}

interface AttachmentPolicy {
  enabled: boolean;
  maxCount: number;
  maxBytes: number;
  totalBytes: number;
  allowedContentTypes: string[];
}

interface StepState {
  localId: string;
  id: string;
  title: string;
  instructions: string;
  skillId: string;
  skillArgs: string;
  skillRequiredCapabilities: string;
  templateStepId: string;
  templateInstructions: string;
}

interface AppliedTemplateState {
  slug: string;
  version: string;
  inputs: Record<string, unknown>;
  stepIds: string[];
  appliedAt: string;
  capabilities: string[];
}

function readDashboardConfig(payload: BootPayload): DashboardConfig {
  const raw = payload.initialData as
    | { dashboardConfig?: DashboardConfig }
    | undefined;
  return raw?.dashboardConfig ?? {};
}

function templateKey(
  scope: TemplateScope,
  slug: string,
  scopeRef?: string | null,
): string {
  return `${scope}::${String(scopeRef || "").trim()}::${slug}`;
}

function interpolatePath(
  template: string,
  replacements: Record<string, string>,
): string {
  return Object.entries(replacements).reduce(
    (value, [key, replacement]) =>
      value.replaceAll(`{${key}}`, encodeURIComponent(replacement)),
    template,
  );
}

function withQueryParams(
  baseUrl: string,
  params: Record<string, string | null | undefined>,
): string {
  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      searchParams.set(key, value);
    }
  });
  const serialized = searchParams.toString();
  if (!serialized) {
    return baseUrl;
  }
  return `${baseUrl}${baseUrl.includes("?") ? "&" : "?"}${serialized}`;
}

function createStepStateEntry(
  index: number,
  overrides: Partial<StepState> = {},
): StepState {
  return {
    localId: `step-${index}`,
    id: "",
    title: "",
    instructions: "",
    skillId: "",
    skillArgs: "",
    skillRequiredCapabilities: "",
    templateStepId: "",
    templateInstructions: "",
    ...overrides,
  };
}

function hasExplicitSkillSelection(skillId: string): boolean {
  const normalized = skillId.trim().toLowerCase();
  return normalized !== "" && normalized !== "auto";
}

function isResolverSkill(skillId: string): boolean {
  return PR_RESOLVER_SKILLS.has(skillId.trim().toLowerCase());
}

function shouldShowSkillArgs(step: StepState | null | undefined): boolean {
  return hasExplicitSkillSelection(String(step?.skillId || ""));
}

function parseCapabilitiesCsv(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(",")
        .map((item) => item.trim().toLowerCase())
        .filter(Boolean),
    ),
  );
}

function stringifySkillArgs(
  args: Record<string, unknown> | null | undefined,
): string {
  if (
    !args ||
    typeof args !== "object" ||
    Array.isArray(args) ||
    Object.keys(args).length === 0
  ) {
    return "";
  }
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return "[unserializable skill args]";
  }
}

function extractCapabilityCsv(value: unknown): string {
  if (!Array.isArray(value)) {
    return "";
  }
  return value
    .map((item) =>
      String(item || "")
        .trim()
        .toLowerCase(),
    )
    .filter(Boolean)
    .join(",");
}

function isEmptyStepStateEntry(step: StepState | null | undefined): boolean {
  if (!step) {
    return true;
  }
  return (
    !step.id.trim() &&
    !step.instructions.trim() &&
    !step.skillId.trim() &&
    !step.skillArgs.trim() &&
    !step.skillRequiredCapabilities.trim() &&
    !step.templateStepId.trim() &&
    !step.templateInstructions.trim()
  );
}

function validatePrimaryStepSubmission(
  primaryStep: StepState | null,
  options: { additionalStepsCount?: number } = {},
):
  | { ok: true; value: { instructions: string; skillId: string } }
  | { ok: false; error: string } {
  if (!primaryStep) {
    return { ok: false, error: "Add at least one step before submitting." };
  }
  const instructions = primaryStep.instructions.trim();
  const skillId = primaryStep.skillId.trim();
  const additionalStepsCount = Number(options.additionalStepsCount) || 0;
  if (!instructions && additionalStepsCount > 0) {
    return {
      ok: false,
      error:
        "Primary step instructions are required when additional steps are provided.",
    };
  }
  if (instructions || hasExplicitSkillSelection(skillId)) {
    return { ok: true, value: { instructions, skillId } };
  }
  return {
    ok: false,
    error: "Primary step requires instructions or an explicit skill selection.",
  };
}

function isValidRepositoryInput(value: string): boolean {
  const candidate = value.trim();
  if (!candidate) {
    return false;
  }
  if (OWNER_REPO_PATTERN.test(candidate)) {
    return true;
  }
  if (candidate.startsWith("http://") || candidate.startsWith("https://")) {
    try {
      const parsed = new URL(candidate);
      return (
        Boolean(parsed.hostname) &&
        Boolean(parsed.pathname) &&
        parsed.pathname !== "/" &&
        !parsed.username &&
        !parsed.password
      );
    } catch {
      return false;
    }
  }
  return candidate.startsWith("git@");
}

function scopeLabel(scope: TemplateScope): string {
  return scope === "personal" ? "Personal" : "Global";
}

function preferredTemplate(items: TemplateOption[]): TemplateOption | null {
  const preferredGlobal = items.find(
    (item) => item.slug === "speckit-orchestrate" && item.scope === "global",
  );
  if (preferredGlobal) {
    return preferredGlobal;
  }
  const preferredAny = items.find(
    (item) => item.slug === "speckit-orchestrate",
  );
  if (preferredAny) {
    return preferredAny;
  }
  return items[0] || null;
}

function formatAttachmentBytes(value: number): string {
  const bytes = Math.max(0, Number(value) || 0);
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function validateAttachmentFiles(
  files: File[],
  policy: AttachmentPolicy,
): {
  ok: boolean;
  errors: string[];
  totalBytes: number;
} {
  const errors: string[] = [];
  if (files.length > policy.maxCount) {
    errors.push(`Too many attachments (${files.length}/${policy.maxCount}).`);
  }
  let totalBytes = 0;
  files.forEach((file) => {
    const type = String(file.type || "")
      .trim()
      .toLowerCase();
    if (!policy.allowedContentTypes.includes(type)) {
      errors.push(`Unsupported file type for ${file.name || "attachment"}.`);
    }
    const sizeBytes = Math.max(0, Number(file.size) || 0);
    if (sizeBytes > policy.maxBytes) {
      errors.push(
        `${file.name || "attachment"} exceeds ${formatAttachmentBytes(policy.maxBytes)}.`,
      );
    }
    totalBytes += sizeBytes;
  });
  if (totalBytes > policy.totalBytes) {
    errors.push(
      `Total attachment size exceeds ${formatAttachmentBytes(policy.totalBytes)}.`,
    );
  }
  return { ok: errors.length === 0, errors, totalBytes };
}

function deriveRequiredCapabilities(args: {
  runtimeMode: string;
  publishMode: string;
  taskSkillRequiredCapabilities: string[];
  stepSkillRequiredCapabilities: string[];
  templateCapabilities: string[];
}): string[] {
  return Array.from(
    new Set(
      [
        args.runtimeMode,
        "git",
        ...(args.publishMode === "pr" ? ["gh"] : []),
        ...args.taskSkillRequiredCapabilities,
        ...args.stepSkillRequiredCapabilities,
        ...args.templateCapabilities
          .map((item) => item.trim().toLowerCase())
          .filter(Boolean),
      ].filter(Boolean),
    ),
  );
}

function mapExpandedStepToState(
  index: number,
  step: ExpandedStepPayload,
): StepState {
  const tool = step.tool || step.skill || {};
  const inlineInputs =
    tool.inputs && typeof tool.inputs === "object"
      ? tool.inputs
      : tool.args && typeof tool.args === "object"
        ? tool.args
        : {};
  const stepId = String(step.id || "").trim();
  const instructions = String(step.instructions || "").trim();
  return createStepStateEntry(index, {
    id: stepId,
    title: String(step.title || "").trim(),
    instructions,
    skillId: String(tool.name || tool.id || "").trim(),
    skillArgs: stringifySkillArgs(inlineInputs),
    skillRequiredCapabilities: extractCapabilityCsv(tool.requiredCapabilities),
    templateStepId: stepId,
    templateInstructions: instructions,
  });
}

function normalizeTemplateInputKey(rawKey: string): string {
  return rawKey
    .trim()
    .toLowerCase()
    .replaceAll(/[^a-z0-9]/g, "");
}

function isFeatureRequestInputKey(rawKey: string): boolean {
  const normalizedKey = normalizeTemplateInputKey(rawKey);
  return (
    normalizedKey === "featurerequest" ||
    normalizedKey === "feature" ||
    normalizedKey === "request"
  );
}

export function resolveObjectiveInstructions(
  featureRequest: string,
  primaryInstructions: string,
  appliedTemplates: AppliedTemplateState[],
): string {
  const explicit = featureRequest.trim();
  if (explicit) {
    return explicit;
  }
  if (primaryInstructions.trim()) {
    return primaryInstructions.trim();
  }
  for (let index = appliedTemplates.length - 1; index >= 0; index -= 1) {
    const candidate = appliedTemplates[index];
    if (!candidate) {
      continue;
    }
    const value = Object.entries(candidate.inputs || {}).find(
      ([rawKey, rawValue]) => {
        return (
          isFeatureRequestInputKey(rawKey) && String(rawValue || "").trim()
        );
      },
    );
    if (value) {
      return String(value[1] || "").trim();
    }
  }
  return "";
}

async function responseErrorMessage(
  response: Response,
  fallback: string,
): Promise<string> {
  try {
    const rawText = (await response.text()).trim();
    if (!rawText) {
      return fallback;
    }
    try {
      const payload = JSON.parse(rawText) as {
        detail?: string | { message?: string };
      };
      if (typeof payload.detail === "string" && payload.detail.trim()) {
        return payload.detail.trim();
      }
      if (payload.detail && typeof payload.detail === "object") {
        const detailMessage = String(payload.detail.message || "").trim();
        if (detailMessage) {
          return detailMessage;
        }
      }
    } catch {
      return rawText;
    }
  } catch {
    return fallback;
  }
  return fallback;
}

async function createInputArtifact(
  createEndpoint: string,
  body: string,
  repository: string,
): Promise<{ artifactId: string }> {
  let createResponse: Response;
  try {
    createResponse = await fetch(createEndpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({
        content_type: "application/json; charset=utf-8",
        size_bytes: new TextEncoder().encode(body).length,
        metadata: {
          label: "Submitted Task Input",
          repository: repository || null,
          source: "task-dashboard-submit",
        },
      }),
    });
  } catch (error) {
    if (error instanceof TypeError && error.message === "Failed to fetch") {
      console.error("[TaskCreate] Network failure during artifact creation.", {
        endpoint: createEndpoint,
        possibleCauses:
          "API service unreachable, CORS block, or network issue.",
      });
      throw new Error(
        `Failed to reach the artifact creation API (endpoint: ${createEndpoint}). ` +
          "The API service may be unreachable or a CORS policy is blocking the request.",
        { cause: error },
      );
    }
    throw error;
  }
  if (!createResponse.ok) {
    throw new Error(
      await responseErrorMessage(
        createResponse,
        "Failed to create input artifact.",
      ),
    );
  }
  const created = (await createResponse.json()) as {
    artifact_ref?: { artifact_id?: string };
    upload?: {
      mode?: string;
      upload_url?: string;
      required_headers?: Record<string, string>;
    };
  };
  const artifactId = String(created.artifact_ref?.artifact_id || "").trim();
  const uploadMode = String(created.upload?.mode || "single_put")
    .trim()
    .toLowerCase();
  if (!artifactId) {
    throw new Error("Artifact upload details were incomplete.");
  }
  if (uploadMode === "multipart") {
    throw new Error(
      "Task input artifact is too large for the current browser submission flow. " +
        "Reduce the submitted instructions or task step payload and retry.",
    );
  }

  const uploadUrl =
    String(created.upload?.upload_url || "").trim() ||
    `/api/artifacts/${encodeURIComponent(artifactId)}/content`;
  const requiredHeaders =
    created.upload?.required_headers &&
    typeof created.upload.required_headers === "object"
      ? created.upload.required_headers
      : {};

  let uploadResponse: Response;
  try {
    const uploadHeaders = new Headers(requiredHeaders);
    if (!uploadHeaders.has("content-type")) {
      uploadHeaders.set("content-type", "application/json; charset=utf-8");
    }
    uploadResponse = await fetch(uploadUrl, {
      method: "PUT",
      headers: uploadHeaders,
      body,
    });
  } catch (error) {
    if (error instanceof TypeError && error.message === "Failed to fetch") {
      console.error(
        "[TaskCreate] Network failure during artifact content upload.",
        {
          uploadUrl,
          possibleCauses:
            "API service unreachable, CORS block, or network issue.",
        },
      );
      throw new Error(
        `Failed to upload artifact content (upload URL: ${uploadUrl}). ` +
          "The API service may be unreachable or a CORS policy is blocking the request.",
        { cause: error },
      );
    }
    throw error;
  }
  if (!uploadResponse.ok) {
    throw new Error(
      await responseErrorMessage(
        uploadResponse,
        "Failed to upload task input artifact content.",
      ),
    );
  }

  const completeUrl = `/api/artifacts/${encodeURIComponent(artifactId)}/complete`;
  let completeError: Error | null = null;
  for (
    let attempt = 0;
    attempt <= ARTIFACT_COMPLETE_RETRY_DELAYS_MS.length;
    attempt += 1
  ) {
    let completeResponse: Response;
    try {
      completeResponse = await fetch(completeUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ parts: [] }),
      });
    } catch (error) {
      if (error instanceof TypeError && error.message === "Failed to fetch") {
        console.error(
          "[TaskCreate] Network failure during artifact completion.",
          {
            endpoint: completeUrl,
            artifactId,
            possibleCauses:
              "API service unreachable, CORS block, or network issue.",
          },
        );
        throw new Error(
          `Failed to finalize artifact upload (endpoint: ${completeUrl}). ` +
            "The API service may be unreachable or a CORS policy is blocking the request.",
          { cause: error },
        );
      }
      throw error;
    }
    if (completeResponse.ok) {
      return { artifactId };
    }

    const message = await responseErrorMessage(
      completeResponse,
      "Failed to finalize task input artifact upload.",
    );
    completeError = new Error(message);
    if (
      !message.includes(ARTIFACT_COMPLETE_RETRY_MESSAGE) ||
      attempt === ARTIFACT_COMPLETE_RETRY_DELAYS_MS.length
    ) {
      throw completeError;
    }
    await new Promise((resolve) =>
      window.setTimeout(resolve, ARTIFACT_COMPLETE_RETRY_DELAYS_MS[attempt]),
    );
  }

  throw (
    completeError ?? new Error("Failed to finalize task input artifact upload.")
  );
}

function utf8ByteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

function stripOversizedInlineInstructions(
  requestBody: Record<string, unknown>,
): void {
  const payload = requestBody.payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return;
  }

  const payloadRecord = payload as Record<string, unknown>;
  const task = payloadRecord.task;
  if (!task || typeof task !== "object" || Array.isArray(task)) {
    return;
  }

  const taskRecord = task as Record<string, unknown>;
  const steps = Array.isArray(taskRecord.steps) ? taskRecord.steps : [];

  const fitsInlineLimit = () =>
    utf8ByteLength(JSON.stringify(requestBody)) <=
    INLINE_TASK_INPUT_LIMIT_BYTES;
  if (fitsInlineLimit()) {
    return;
  }

  for (let index = steps.length - 1; index >= 1; index -= 1) {
    const step = steps[index];
    if (
      !step ||
      typeof step !== "object" ||
      Array.isArray(step) ||
      !("instructions" in step)
    ) {
      continue;
    }
    delete (step as Record<string, unknown>).instructions;
    if (fitsInlineLimit()) {
      return;
    }
  }

  if ("instructions" in taskRecord) {
    delete taskRecord.instructions;
    if (fitsInlineLimit()) {
      return;
    }
  }

  for (let index = 0; index < steps.length; index += 1) {
    const step = steps[index];
    if (
      !step ||
      typeof step !== "object" ||
      Array.isArray(step) ||
      !("instructions" in step)
    ) {
      continue;
    }
    delete (step as Record<string, unknown>).instructions;
    if (fitsInlineLimit()) {
      return;
    }
  }
}

async function linkInputArtifact(
  artifactId: string,
  execution: ExecutionCreateResponse,
): Promise<void> {
  const workflowId = String(execution.workflowId || "").trim();
  const runId = String(execution.runId || execution.temporalRunId || "").trim();
  const namespace = String(execution.namespace || "").trim();
  if (!artifactId || !workflowId || !runId || !namespace) {
    return;
  }
  const linkEndpoint = `/api/artifacts/${encodeURIComponent(artifactId)}/links`;
  let response: Response;
  try {
    response = await fetch(linkEndpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({
        namespace,
        workflow_id: workflowId,
        run_id: runId,
        link_type: "input.instructions",
        label: "Submitted Task Input",
      }),
    });
  } catch (error) {
    if (error instanceof TypeError && error.message === "Failed to fetch") {
      console.error("[TaskCreate] Network failure during artifact linking.", {
        endpoint: linkEndpoint,
        artifactId,
        possibleCauses:
          "API service unreachable, CORS block, or network issue.",
      });
      throw new Error(
        `Failed to reach the artifact linking API (endpoint: ${linkEndpoint}). ` +
          "The API service may be unreachable or a CORS policy is blocking the request.",
        { cause: error },
      );
    }
    throw error;
  }
  if (!response.ok) {
    throw new Error(
      await responseErrorMessage(
        response,
        "Failed to link input artifact to execution.",
      ),
    );
  }
}

function ArrowUpIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M12 6v12m0-12-4 4m4-4 4 4" />
    </svg>
  );
}

function ArrowDownIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M12 18V6m0 12 4-4m-4 4-4-4" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M7 7l10 10M17 7l-10 10" />
    </svg>
  );
}

export function TaskCreatePage({ payload }: { payload: BootPayload }) {
  const dashboardConfig = readDashboardConfig(payload);
  const temporalCreateEndpoint = String(
    dashboardConfig.sources?.temporal?.create || "/api/executions",
  );
  const artifactCreateEndpoint = String(
    dashboardConfig.sources?.temporal?.artifactCreate || "/api/artifacts",
  );
  const providerProfilesEndpoint = String(
    dashboardConfig.system?.providerProfiles?.list ||
      "/api/v1/provider-profiles",
  );
  const taskTemplateCatalog = dashboardConfig.system?.taskTemplateCatalog;
  const taskTemplateCatalogEnabled = Boolean(taskTemplateCatalog?.enabled);
  const taskTemplateSaveEnabled = Boolean(
    taskTemplateCatalog?.templateSaveEnabled,
  );
  const taskTemplateListEndpoint = String(
    taskTemplateCatalog?.list || "/api/task-step-templates",
  );
  const taskTemplateDetailEndpoint = String(
    taskTemplateCatalog?.detail || "/api/task-step-templates/{slug}",
  );
  const taskTemplateExpandEndpoint = String(
    taskTemplateCatalog?.expand || "/api/task-step-templates/{slug}:expand",
  );
  const taskTemplateSaveEndpoint = String(
    taskTemplateCatalog?.saveFromTask ||
      "/api/task-step-templates/save-from-task",
  );

  const attachmentPolicy = useMemo<AttachmentPolicy>(() => {
    const config = dashboardConfig.system?.attachmentPolicy;
    const allowedContentTypes =
      Array.isArray(config?.allowedContentTypes) &&
      config.allowedContentTypes.length > 0
        ? config.allowedContentTypes
            .map((item) =>
              String(item || "")
                .trim()
                .toLowerCase(),
            )
            .filter(Boolean)
        : ["image/png", "image/jpeg", "image/webp"];
    return {
      enabled: Boolean(config?.enabled),
      maxCount: Math.max(1, Number(config?.maxCount) || 10),
      maxBytes: Math.max(1, Number(config?.maxBytes) || 10 * 1024 * 1024),
      totalBytes: Math.max(1, Number(config?.totalBytes) || 25 * 1024 * 1024),
      allowedContentTypes,
    };
  }, [dashboardConfig.system?.attachmentPolicy]);

  const defaultRuntime = String(
    dashboardConfig.system?.defaultTaskRuntime || "codex_cli",
  );
  const defaultRepository = String(
    dashboardConfig.system?.defaultRepository || "",
  );
  const defaultPublishMode = String(
    dashboardConfig.system?.defaultPublishMode || "pr",
  );
  const defaultProposeTasks = Boolean(
    dashboardConfig.system?.defaultProposeTasks,
  );
  const defaultTaskModelByRuntime =
    dashboardConfig.system?.defaultTaskModelByRuntime || {};
  const defaultTaskEffortByRuntime =
    dashboardConfig.system?.defaultTaskEffortByRuntime || {};
  const supportedTaskRuntimes = dashboardConfig.system
    ?.supportedTaskRuntimes || ["codex_cli", "gemini_cli", "claude_code"];

  const [steps, setSteps] = useState<StepState[]>([createStepStateEntry(1)]);
  const [nextStepNumber, setNextStepNumber] = useState(2);
  const [runtime, setRuntime] = useState(defaultRuntime);
  const [model, setModel] = useState(
    String(
      defaultTaskModelByRuntime[defaultRuntime] ||
        dashboardConfig.system?.defaultTaskModel ||
        "",
    ),
  );
  const [modelManualOverride, setModelManualOverride] = useState(false);
  const [effort, setEffort] = useState(
    String(
      defaultTaskEffortByRuntime[defaultRuntime] ||
        dashboardConfig.system?.defaultTaskEffort ||
        "",
    ),
  );
  const [repository, setRepository] = useState(defaultRepository);
  const [providerProfile, setProviderProfile] = useState("");
  const [startingBranch, setStartingBranch] = useState("");
  const [targetBranch, setTargetBranch] = useState("");
  const [publishMode, setPublishMode] = useState(defaultPublishMode);
  const [priority, setPriority] = useState(0);
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [proposeTasks, setProposeTasks] = useState(() =>
    readProposeTasksPreference(defaultProposeTasks),
  );
  const isInitialMount = useRef(true);
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>("immediate");
  const [scheduledFor, setScheduledFor] = useState("");
  const [scheduleDeferredMinutes, setScheduleDeferredMinutes] = useState("");
  const [scheduleCron, setScheduleCron] = useState("");
  const [scheduleTimezone, setScheduleTimezone] = useState("UTC");
  const [scheduleName, setScheduleName] = useState("");
  const [templateFeatureRequest, setTemplateFeatureRequest] = useState("");
  const [selectedPresetKey, setSelectedPresetKey] = useState("");
  const [templateMessage, setTemplateMessage] = useState<string | null>(null);
  const [appliedTemplates, setAppliedTemplates] = useState<
    AppliedTemplateState[]
  >([]);
  const [selectedAttachmentFiles, setSelectedAttachmentFiles] = useState<
    File[]
  >([]);
  const [submitMessage, setSubmitMessage] = useState<string | null>(null);
  const [isApplyingPreset, setIsApplyingPreset] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const templateInputMemoryRef = useRef<Record<string, unknown>>({});
  const prevRuntimeRef = useRef(runtime);
  const prevProviderProfileRef = useRef(providerProfile);

  const providerProfilesQuery = useQuery({
    queryKey: ["task-create", "provider-profiles", runtime],
    queryFn: async (): Promise<ProviderProfile[]> => {
      const separator = providerProfilesEndpoint.includes("?") ? "&" : "?";
      const response = await fetch(
        `${providerProfilesEndpoint}${separator}runtime_id=${encodeURIComponent(runtime)}`,
        {
          headers: { Accept: "application/json" },
        },
      );
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(
            response,
            "Failed to load provider profiles.",
          ),
        );
      }
      return (await response.json()) as ProviderProfile[];
    },
    enabled: Boolean(runtime),
  });

  useEffect(() => {
    const runtimeChanged = prevRuntimeRef.current !== runtime;
    const profileChanged = prevProviderProfileRef.current !== providerProfile;

    if (runtimeChanged || profileChanged) {
      setModelManualOverride(false);
    }

    if (runtimeChanged) {
      setProviderProfile("");
      prevRuntimeRef.current = runtime;
    }

    if (profileChanged) {
      prevProviderProfileRef.current = providerProfile;
    }

    setEffort(
      String(
        defaultTaskEffortByRuntime[runtime] ||
          dashboardConfig.system?.defaultTaskEffort ||
          "",
      ),
    );

    if (modelManualOverride && !runtimeChanged && !profileChanged) {
      return;
    }

    const profileIdForModel = runtimeChanged ? "" : providerProfile;
    const profiles = providerProfilesQuery.data || [];
    const selectedProfile = profiles.find(
      (p) => p.profile_id === profileIdForModel,
    );
    if (selectedProfile?.default_model) {
      setModel(selectedProfile.default_model);
    } else {
      setModel(
        String(
          defaultTaskModelByRuntime[runtime] ||
            dashboardConfig.system?.defaultTaskModel ||
            "",
        ),
      );
    }
  }, [
    dashboardConfig.system?.defaultTaskEffort,
    dashboardConfig.system?.defaultTaskModel,
    defaultTaskEffortByRuntime,
    defaultTaskModelByRuntime,
    modelManualOverride,
    providerProfilesQuery.data,
    providerProfile,
    runtime,
  ]);

  useEffect(() => {
    const primarySkill = String(steps[0]?.skillId || "")
      .trim()
      .toLowerCase();
    if (isResolverSkill(primarySkill)) {
      setPublishMode("none");
    }
  }, [steps[0]?.skillId]);

  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    writeProposeTasksPreference(proposeTasks);
  }, [proposeTasks]);

  const skillsQuery = useQuery({
    queryKey: ["task-create", "skills"],
    queryFn: async (): Promise<string[]> => {
      const response = await fetch("/api/tasks/skills", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(response, "Failed to load skills."),
        );
      }
      const data = (await response.json()) as SkillsResponse;
      return data.items?.worker || [];
    },
  });

  const templateOptionsQuery = useQuery({
    queryKey: [
      "task-create",
      "task-template-catalog",
      taskTemplateListEndpoint,
    ],
    enabled: taskTemplateCatalogEnabled,
    queryFn: async (): Promise<TemplateCatalogResult> => {
      const scopes: TemplateScope[] = ["global", "personal"];
      const results = await Promise.all(
        scopes.map(async (scope) => {
          try {
            const response = await fetch(
              withQueryParams(taskTemplateListEndpoint, { scope }),
              {
                headers: { Accept: "application/json" },
              },
            );
            if (!response.ok) {
              throw new Error(
                await responseErrorMessage(response, "Failed to load presets."),
              );
            }
            const data = (await response.json()) as TaskTemplateListResponse;
            const items = (data.items || []).map((item) => ({
              ...item,
              key: templateKey(item.scope, item.slug, item.scopeRef),
            }));
            return { scope, items, failed: false };
          } catch {
            return { scope, items: [] as TemplateOption[], failed: true };
          }
        }),
      );
      return {
        items: results
          .flatMap((result) => result.items)
          .sort((left, right) => left.title.localeCompare(right.title)),
        failedScopes: results
          .filter((result) => result.failed)
          .map((result) => result.scope),
      };
    },
  });

  const templateItems = templateOptionsQuery.data?.items || [];

  useEffect(() => {
    if (!taskTemplateCatalogEnabled || templateItems.length === 0) {
      return;
    }
    const selectedStillExists = templateItems.some(
      (item) => item.key === selectedPresetKey,
    );
    if (selectedStillExists) {
      return;
    }
    const preferred = preferredTemplate(templateItems);
    if (preferred) {
      setSelectedPresetKey(preferred.key);
    }
  }, [selectedPresetKey, taskTemplateCatalogEnabled, templateItems]);

  const selectedPreset =
    templateItems.find((item) => item.key === selectedPresetKey) || null;

  const providerOptions = (providerProfilesQuery.data || []).map((profile) => ({
    id: profile.profile_id,
    label: profile.account_label || profile.profile_id,
  }));

  const attachmentValidation = useMemo(
    () => validateAttachmentFiles(selectedAttachmentFiles, attachmentPolicy),
    [attachmentPolicy, selectedAttachmentFiles],
  );

  const modelOptions = useMemo(
    () =>
      Array.from(
        new Set(
          [
            String(defaultTaskModelByRuntime[runtime] || ""),
            String(dashboardConfig.system?.defaultTaskModel || ""),
          ].filter(Boolean),
        ),
      ),
    [
      dashboardConfig.system?.defaultTaskModel,
      defaultTaskModelByRuntime,
      runtime,
    ],
  );

  const effortOptions = useMemo(
    () =>
      Array.from(
        new Set(
          [
            "low",
            "medium",
            "high",
            "xhigh",
            String(defaultTaskEffortByRuntime[runtime] || ""),
            String(dashboardConfig.system?.defaultTaskEffort || ""),
          ].filter(Boolean),
        ),
      ),
    [
      dashboardConfig.system?.defaultTaskEffort,
      defaultTaskEffortByRuntime,
      runtime,
    ],
  );

  const presetStatusText = useMemo(() => {
    if (templateMessage) {
      return templateMessage;
    }
    if (templateOptionsQuery.isLoading) {
      return "Loading presets...";
    }
    if (templateOptionsQuery.isError) {
      return "Failed to load presets.";
    }
    if (templateItems.length === 0) {
      return "No presets available for your account.";
    }
    if ((templateOptionsQuery.data?.failedScopes || []).length > 0) {
      return `Loaded ${templateItems.length} presets (missing scopes: ${(templateOptionsQuery.data?.failedScopes || []).join(", ")}).`;
    }
    return `Loaded ${templateItems.length} presets.`;
  }, [
    templateItems,
    templateMessage,
    templateOptionsQuery.data?.failedScopes,
    templateOptionsQuery.isError,
    templateOptionsQuery.isLoading,
  ]);

  function updateStep(localId: string, updates: Partial<StepState>) {
    setSteps((current) =>
      current.map((step) => {
        if (step.localId !== localId) {
          return step;
        }
        const nextStep = { ...step, ...updates };
        if (
          Object.prototype.hasOwnProperty.call(updates, "instructions") &&
          nextStep.templateStepId &&
          nextStep.id === nextStep.templateStepId &&
          nextStep.instructions !== nextStep.templateInstructions
        ) {
          nextStep.id = "";
        }
        if (
          Object.prototype.hasOwnProperty.call(updates, "skillId") &&
          !shouldShowSkillArgs(nextStep)
        ) {
          nextStep.skillArgs = "";
        }
        return nextStep;
      }),
    );
  }

  function addStep() {
    setSteps((current) => [...current, createStepStateEntry(nextStepNumber)]);
    setNextStepNumber((current) => current + 1);
  }

  function moveStep(index: number, direction: -1 | 1) {
    setSteps((current) => {
      const nextIndex = index + direction;
      if (
        index < 0 ||
        index >= current.length ||
        nextIndex < 0 ||
        nextIndex >= current.length
      ) {
        return current;
      }
      const updated = current.slice();
      const currentStep = updated[index];
      const nextStep = updated[nextIndex];
      if (!currentStep || !nextStep) {
        return current;
      }
      updated[index] = nextStep;
      updated[nextIndex] = currentStep;
      return updated;
    });
  }

  function removeStep(index: number) {
    setSteps((current) =>
      current.filter((_, currentIndex) => currentIndex !== index),
    );
  }

  function resolveTemplateInputs(inputs: TaskTemplateInputDefinition[]): {
    values: Record<string, unknown>;
    assumptions: string[];
  } {
    const values: Record<string, unknown> = {};
    const assumptions: string[] = [];
    const primaryInstructions = String(steps[0]?.instructions || "").trim();
    const explicitFeatureRequest = templateFeatureRequest.trim();
    const repositoryValue = repository.trim();

    inputs.forEach((definition) => {
      const name = String(definition.name || "").trim();
      const label = String(definition.label || name).trim() || name;
      if (!name) {
        return;
      }
      const required = Boolean(definition.required);
      const inputType = String(definition.type || "").toLowerCase();
      const options = Array.isArray(definition.options)
        ? definition.options
            .map((option) => String(option).trim())
            .filter(Boolean)
        : [];
      const key = name.toLowerCase();
      const isFeatureRequestKey = isFeatureRequestInputKey(name);

      let value: unknown = null;
      let valueSource = "";
      const remembered = templateInputMemoryRef.current[name];
      const defaultValue = definition.default;

      if (isFeatureRequestKey && explicitFeatureRequest) {
        value = explicitFeatureRequest;
        valueSource = "manual";
      } else if (
        remembered !== undefined &&
        remembered !== null &&
        String(remembered).trim() !== ""
      ) {
        value = remembered;
        valueSource = "memory";
      } else if (
        defaultValue !== undefined &&
        defaultValue !== null &&
        String(defaultValue).trim() !== ""
      ) {
        value = defaultValue;
        valueSource = "default";
      } else if (key.includes("instruction") || isFeatureRequestKey) {
        value = explicitFeatureRequest || primaryInstructions;
        valueSource = "draft";
      } else if (key.includes("repo")) {
        value = repositoryValue;
        valueSource = "draft";
      } else if (inputType === "enum") {
        value = options[0] || "";
        valueSource = "assumed";
      } else if (inputType === "boolean") {
        value = false;
        valueSource = "assumed";
      }

      const hasValue =
        value !== null && value !== undefined && String(value).trim() !== "";
      if (!hasValue && required) {
        if (inputType === "enum" && options.length > 0) {
          value = options[0];
        } else if (inputType === "boolean") {
          value = false;
        } else if (explicitFeatureRequest || primaryInstructions) {
          value = explicitFeatureRequest || primaryInstructions;
        } else {
          value = `auto-${key.replaceAll(/[^a-z0-9]+/g, "-").replaceAll(/^-+|-+$/g, "") || "value"}`;
        }
        valueSource = "assumed";
      }

      if (
        value === null ||
        value === undefined ||
        String(value).trim() === ""
      ) {
        return;
      }

      let normalized: unknown;
      if (inputType === "boolean") {
        normalized = Boolean(value);
      } else {
        normalized = String(value).trim();
      }
      if (inputType === "enum") {
        const candidate = String(normalized).trim();
        normalized =
          options.length > 0 && !options.includes(candidate)
            ? options[0]
            : candidate;
      }

      values[name] = normalized;
      templateInputMemoryRef.current[name] = normalized;
      if (valueSource === "assumed" || valueSource === "draft") {
        assumptions.push(label);
      }
    });

    return { values, assumptions };
  }

  async function handleApplyPreset() {
    if (isApplyingPreset) return;
    if (!selectedPreset) {
      setTemplateMessage("Choose a preset first.");
      return;
    }
    setIsApplyingPreset(true);
    setTemplateMessage("Applying preset...");

    try {
      const scopeParams = {
        scope: selectedPreset.scope,
        scopeRef: selectedPreset.scopeRef || undefined,
      };
      const detailResponse = await fetch(
        withQueryParams(
          interpolatePath(taskTemplateDetailEndpoint, {
            slug: selectedPreset.slug,
          }),
          scopeParams,
        ),
        {
          headers: { Accept: "application/json" },
        },
      );
      if (!detailResponse.ok) {
        throw new Error(
          await responseErrorMessage(
            detailResponse,
            "Failed to load preset details.",
          ),
        );
      }
      const detail = (await detailResponse.json()) as TaskTemplateDetail;
      const { values: inputs, assumptions } = resolveTemplateInputs(
        detail.inputs || [],
      );
      const expandResponse = await fetch(
        withQueryParams(
          interpolatePath(taskTemplateExpandEndpoint, {
            slug: selectedPreset.slug,
          }),
          scopeParams,
        ),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify({
            version:
              detail.version ||
              detail.latestVersion ||
              selectedPreset.latestVersion ||
              "1.0.0",
            inputs,
            options: { enforceStepLimit: true },
          }),
        },
      );
      if (!expandResponse.ok) {
        throw new Error(
          await responseErrorMessage(expandResponse, "Failed to apply preset."),
        );
      }
      const expanded =
        (await expandResponse.json()) as TaskTemplateExpandResponse;
      const expandedSteps = (expanded.steps || []).map((step, index) =>
        mapExpandedStepToState(nextStepNumber + index, step),
      );
      const replaceEmptyDefault =
        steps.length === 1 && isEmptyStepStateEntry(steps[0]);

      setSteps((current) => {
        if (replaceEmptyDefault) {
          return expandedSteps.length > 0
            ? expandedSteps
            : [createStepStateEntry(nextStepNumber)];
        }
        return [...current, ...expandedSteps];
      });
      setNextStepNumber(
        (current) => current + Math.max(expandedSteps.length, 1),
      );
      if (expandedSteps.length > 0) {
        const appliedTemplate = expanded.appliedTemplate || {};
        setAppliedTemplates((current) => [
          ...current,
          {
            slug: String(appliedTemplate.slug || selectedPreset.slug),
            version: String(
              appliedTemplate.version ||
                detail.version ||
                selectedPreset.latestVersion ||
                "1.0.0",
            ),
            inputs:
              appliedTemplate.inputs &&
              typeof appliedTemplate.inputs === "object"
                ? appliedTemplate.inputs
                : inputs,
            stepIds: Array.isArray(appliedTemplate.stepIds)
              ? appliedTemplate.stepIds
              : expandedSteps.map((step) => step.id).filter(Boolean),
            appliedAt:
              String(appliedTemplate.appliedAt || "").trim() ||
              new Date().toISOString(),
            capabilities: Array.isArray(expanded.capabilities)
              ? expanded.capabilities
              : [],
          },
        ]);
      }
      const autoFillSuffix =
        assumptions.length > 0
          ? ` Auto-filled ${assumptions.length} input(s): ${assumptions.join(", ")}.`
          : "";
      setTemplateMessage(
        `Applied preset '${selectedPreset.title}' (${expandedSteps.length} steps).${autoFillSuffix}`,
      );
    } catch (error) {
      const failure =
        error instanceof Error ? error : new Error("Failed to apply preset.");
      setTemplateMessage(`Failed to apply preset: ${failure.message}`);
    } finally {
      setIsApplyingPreset(false);
    }
  }

  async function handleSaveCurrentStepsAsPreset() {
    if (!taskTemplateSaveEnabled) {
      return;
    }
    const title = window.prompt("Preset title", "");
    if (title === null || !title.trim()) {
      setTemplateMessage("Preset save cancelled.");
      return;
    }
    const description =
      window.prompt("Preset description", `Saved from queue draft: ${title}`) ||
      "";

    const presetSteps: Record<string, unknown>[] = [];
    for (let index = 0; index < steps.length; index += 1) {
      const step = steps[index];
      if (!step) {
        continue;
      }
      const instructions = step.instructions.trim();
      if (!instructions) {
        continue;
      }
      const blueprint: Record<string, unknown> = { instructions };
      const skillId = step.skillId.trim();
      const caps = parseCapabilitiesCsv(step.skillRequiredCapabilities);
      const skillArgsRaw = shouldShowSkillArgs(step)
        ? step.skillArgs.trim()
        : "";
      if (skillId || skillArgsRaw || caps.length > 0) {
        let skillArgs: Record<string, unknown> = {};
        if (skillArgsRaw) {
          try {
            const parsed = JSON.parse(skillArgsRaw) as unknown;
            if (
              !parsed ||
              typeof parsed !== "object" ||
              Array.isArray(parsed)
            ) {
              throw new Error("Skill args must be an object.");
            }
            skillArgs = parsed as Record<string, unknown>;
          } catch {
            setTemplateMessage(
              `Step ${index + 1} Skill Args must be valid JSON object text.`,
            );
            return;
          }
        }
        const normalizedTool = {
          type: "skill",
          name: skillId || "auto",
          version: "1.0",
          inputs: skillArgs,
          ...(caps.length > 0 ? { requiredCapabilities: caps } : {}),
        };
        blueprint.tool = normalizedTool;
        blueprint.skill = {
          id: normalizedTool.name,
          args: skillArgs,
          ...(caps.length > 0 ? { requiredCapabilities: caps } : {}),
        };
      }
      presetSteps.push(blueprint);
    }

    if (presetSteps.length === 0) {
      setTemplateMessage(
        "Add at least one step with instructions before saving.",
      );
      return;
    }

    setTemplateMessage("Saving preset...");
    try {
      const response = await fetch(taskTemplateSaveEndpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({
          scope: "personal",
          title: title.trim(),
          description: description.trim() || title.trim(),
          steps: presetSteps,
          suggestedInputs: [],
          tags: [],
        }),
      });
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(response, "Failed to save preset."),
        );
      }
      const created = (await response.json()) as { title?: string };
      setTemplateMessage(`Saved preset '${created.title || title.trim()}'.`);
      await templateOptionsQuery.refetch();
    } catch (error) {
      const failure =
        error instanceof Error ? error : new Error("Failed to save preset.");
      setTemplateMessage(`Failed to save preset: ${failure.message}`);
    }
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }
    setSubmitMessage(null);

    const primaryStep = steps[0] || null;
    const primaryValidation = validatePrimaryStepSubmission(primaryStep);
    if (!primaryValidation.ok) {
      setSubmitMessage(primaryValidation.error);
      return;
    }

    const primaryInstructions = primaryValidation.value.instructions;
    const objectiveInstructions = resolveObjectiveInstructions(
      templateFeatureRequest,
      primaryInstructions,
      appliedTemplates,
    );
    const normalizedRepository = repository.trim() || defaultRepository;
    if (!normalizedRepository) {
      setSubmitMessage(
        "Repository is required because no system default repository is configured.",
      );
      return;
    }
    if (!isValidRepositoryInput(normalizedRepository)) {
      setSubmitMessage(
        "Repository must be owner/repo, https://<host>/<path>, or git@<host>:<path> (token-free).",
      );
      return;
    }

    const normalizedRuntime = runtime.trim().toLowerCase();
    if (!supportedTaskRuntimes.includes(normalizedRuntime)) {
      setSubmitMessage(
        `Runtime must be one of: ${supportedTaskRuntimes.join(", ")}.`,
      );
      return;
    }

    const normalizedPublishMode = publishMode.trim().toLowerCase();
    if (!["none", "branch", "pr"].includes(normalizedPublishMode)) {
      setSubmitMessage("Publish mode must be one of: none, branch, pr.");
      return;
    }

    if (!Number.isInteger(priority)) {
      setSubmitMessage("Priority must be an integer.");
      return;
    }
    if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
      setSubmitMessage("Max Attempts must be an integer >= 1.");
      return;
    }

    const primarySkillId = primaryValidation.value.skillId.trim() || "auto";
    const primarySkillArgsRaw = shouldShowSkillArgs(primaryStep)
      ? String(primaryStep?.skillArgs || "").trim()
      : "";
    const taskSkillRequiredCapabilities = parseCapabilitiesCsv(
      String(primaryStep?.skillRequiredCapabilities || ""),
    );

    let primarySkillArgs: Record<string, unknown> = {};
    if (primarySkillArgsRaw) {
      try {
        const parsed = JSON.parse(primarySkillArgsRaw) as unknown;
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          throw new Error("Skill args must be an object.");
        }
        primarySkillArgs = parsed as Record<string, unknown>;
      } catch {
        setSubmitMessage(
          'Primary step Skill Args must be valid JSON object text (for example: {"featureKey":"..."}).',
        );
        return;
      }
    }

    const primaryStepTool = {
      type: "skill",
      name: primarySkillId,
      version: "1.0",
      ...(Object.keys(primarySkillArgs).length > 0
        ? { inputs: primarySkillArgs }
        : {}),
      ...(taskSkillRequiredCapabilities.length > 0
        ? { requiredCapabilities: taskSkillRequiredCapabilities }
        : {}),
    };
    const primaryStepSkill = {
      id: primarySkillId,
      args: primarySkillArgs,
      ...(taskSkillRequiredCapabilities.length > 0
        ? { requiredCapabilities: taskSkillRequiredCapabilities }
        : {}),
    };
    const primaryStepHasSkillOverride =
      hasExplicitSkillSelection(primarySkillId) ||
      Object.keys(primarySkillArgs).length > 0 ||
      taskSkillRequiredCapabilities.length > 0;

    const additionalSteps: Array<{
      sourceIndex: number;
      payload: Record<string, unknown>;
    }> = [];
    const stepSkillRequiredCapabilities: string[] = [];
    for (let index = 1; index < steps.length; index += 1) {
      const step = steps[index];
      if (!step) {
        continue;
      }
      const stepInstructions = step.instructions.trim();
      const stepSkillId = step.skillId.trim();
      const stepSkillArgsRaw = shouldShowSkillArgs(step)
        ? step.skillArgs.trim()
        : "";
      const stepSkillCaps = parseCapabilitiesCsv(
        step.skillRequiredCapabilities,
      );
      const hasStepContent =
        Boolean(stepInstructions) ||
        Boolean(stepSkillId) ||
        Boolean(stepSkillArgsRaw) ||
        stepSkillCaps.length > 0;
      if (!hasStepContent) {
        continue;
      }

      let stepSkillArgs: Record<string, unknown> = {};
      if (stepSkillArgsRaw) {
        try {
          const parsed = JSON.parse(stepSkillArgsRaw) as unknown;
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("Step skill args must be an object.");
          }
          stepSkillArgs = parsed as Record<string, unknown>;
        } catch {
          setSubmitMessage(
            `Step ${index + 1} Skill Args must be valid JSON object text.`,
          );
          return;
        }
      }

      const stepPayload: Record<string, unknown> = {};
      if (stepInstructions) {
        stepPayload.instructions = stepInstructions;
      }
      if (step.title.trim()) {
        stepPayload.title = step.title.trim();
      }
      if (stepSkillId || stepSkillArgsRaw || stepSkillCaps.length > 0) {
        stepPayload.tool = {
          type: "skill",
          name: stepSkillId || primarySkillId,
          version: "1.0",
          inputs: stepSkillArgs,
          ...(stepSkillCaps.length > 0
            ? { requiredCapabilities: stepSkillCaps }
            : {}),
        };
        stepPayload.skill = {
          id: stepSkillId || primarySkillId,
          args: stepSkillArgs,
          ...(stepSkillCaps.length > 0
            ? { requiredCapabilities: stepSkillCaps }
            : {}),
        };
        stepSkillRequiredCapabilities.push(...stepSkillCaps);
      }
      additionalSteps.push({ sourceIndex: index, payload: stepPayload });
    }

    const additionalStepValidation = validatePrimaryStepSubmission(
      primaryStep,
      {
        additionalStepsCount: additionalSteps.length,
      },
    );
    if (!additionalStepValidation.ok) {
      setSubmitMessage(additionalStepValidation.error);
      return;
    }

    const includePrimaryStepForObjectiveOverride =
      Boolean(primaryInstructions) &&
      objectiveInstructions !== primaryInstructions;
    const hasTemplateBoundStep = steps.some((step) => Boolean(step.id.trim()));
    const includeExplicitSteps =
      additionalSteps.length > 0 ||
      includePrimaryStepForObjectiveOverride ||
      hasTemplateBoundStep;

    const normalizedSteps = includeExplicitSteps
      ? [
          {
            sourceIndex: 0,
            payload: {
              ...(primaryInstructions
                ? { instructions: primaryInstructions }
                : {}),
              ...(primaryStepHasSkillOverride
                ? { tool: primaryStepTool, skill: primaryStepSkill }
                : {}),
            },
          },
          ...additionalSteps,
        ].map((entry) => {
          const sourceStep = steps[entry.sourceIndex];
          if (!sourceStep) {
            return entry.payload;
          }
          return {
            ...(sourceStep.id.trim() ? { id: sourceStep.id.trim() } : {}),
            ...(sourceStep.title.trim()
              ? { title: sourceStep.title.trim() }
              : {}),
            ...entry.payload,
          };
        })
      : [];

    const templateCapabilities = appliedTemplates.flatMap(
      (entry) => entry.capabilities || [],
    );
    const mergedCapabilities = deriveRequiredCapabilities({
      runtimeMode: normalizedRuntime,
      publishMode: normalizedPublishMode,
      taskSkillRequiredCapabilities,
      stepSkillRequiredCapabilities,
      templateCapabilities,
    });

    const normalizedTaskTool = primaryStepTool;

    const taskPayload: Record<string, unknown> = {
      instructions: objectiveInstructions,
      tool: normalizedTaskTool,
      skill: primaryStepSkill,
      ...(hasExplicitSkillSelection(primarySkillId)
        ? { skills: [primarySkillId] }
        : {}),
      ...(Object.keys(primarySkillArgs).length > 0
        ? { inputs: primarySkillArgs }
        : {}),
      proposeTasks,
      runtime: {
        mode: normalizedRuntime,
        ...(model.trim() ? { model: model.trim() } : {}),
        ...(effort.trim() ? { effort: effort.trim() } : {}),
        ...(providerProfile ? { profileId: providerProfile } : {}),
      },
      publish: {
        mode: normalizedPublishMode,
      },
      ...(startingBranch.trim() || targetBranch.trim()
        ? {
            git: {
              ...(startingBranch.trim()
                ? { startingBranch: startingBranch.trim() }
                : {}),
              ...(targetBranch.trim()
                ? { targetBranch: targetBranch.trim() }
                : {}),
            },
          }
        : {}),
      ...(normalizedSteps.length > 0 ? { steps: normalizedSteps } : {}),
      ...(appliedTemplates.length > 0
        ? { appliedStepTemplates: appliedTemplates }
        : {}),
    };

    const requestBody: Record<string, unknown> = {
      type: "task",
      priority,
      maxAttempts,
      payload: {
        repository: normalizedRepository,
        ...(mergedCapabilities.length > 0
          ? { requiredCapabilities: mergedCapabilities }
          : {}),
        targetRuntime: normalizedRuntime,
        task: taskPayload,
      },
    };

    if (scheduleMode === "once") {
      if (!scheduledFor.trim()) {
        setSubmitMessage("Scheduled time is required for deferred scheduling.");
        return;
      }
      const scheduleDate = new Date(scheduledFor);
      if (Number.isNaN(scheduleDate.getTime()) || scheduleDate <= new Date()) {
        setSubmitMessage("Scheduled time must be a valid future date.");
        return;
      }
      (requestBody.payload as Record<string, unknown>).schedule = {
        mode: "once",
        scheduledFor: scheduleDate.toISOString(),
      };
    } else if (scheduleMode === "deferred_minutes") {
      const deferredMinutes = Number(scheduleDeferredMinutes.trim());
      if (
        !Number.isFinite(deferredMinutes) ||
        !Number.isInteger(deferredMinutes) ||
        deferredMinutes <= 0
      ) {
        setSubmitMessage(
          "A valid positive whole number of minutes is required for deferred scheduling.",
        );
        return;
      }
      if (deferredMinutes > 525600) {
        setSubmitMessage("Deferred minutes cannot exceed 525 600 (one year).");
        return;
      }
      (requestBody.payload as Record<string, unknown>).schedule = {
        mode: "once",
        scheduledFor: new Date(
          Date.now() + deferredMinutes * 60_000,
        ).toISOString(),
      };
    } else if (scheduleMode === "recurring") {
      if (!scheduleCron.trim()) {
        setSubmitMessage(
          "Cron expression is required for recurring scheduling.",
        );
        return;
      }
      (requestBody.payload as Record<string, unknown>).schedule = {
        mode: "recurring",
        cron: scheduleCron.trim(),
        timezone: scheduleTimezone.trim() || "UTC",
        name: scheduleName.trim() || "Inline schedule",
      };
    }

    if (attachmentPolicy.enabled && selectedAttachmentFiles.length > 0) {
      if (!attachmentValidation.ok) {
        setSubmitMessage(attachmentValidation.errors.join(" "));
        return;
      }
      setSubmitMessage(
        "Attachments are not supported for Temporal task submission yet. Remove attachments and retry.",
      );
      return;
    }

    setIsSubmitting(true);
    try {
      let inputArtifactRef: string | null = null;
      const taskInputArtifactBody = JSON.stringify({
        repository: normalizedRepository,
        task: taskPayload,
      });
      const taskInputArtifactBytes = utf8ByteLength(taskInputArtifactBody);
      if (taskInputArtifactBytes > INLINE_TASK_INPUT_LIMIT_BYTES) {
        const artifact = await createInputArtifact(
          artifactCreateEndpoint,
          taskInputArtifactBody,
          normalizedRepository,
        );
        inputArtifactRef = artifact.artifactId;
        (requestBody.payload as Record<string, unknown>).inputArtifactRef =
          inputArtifactRef;
        stripOversizedInlineInstructions(requestBody);
      }

      const response = await fetch(temporalCreateEndpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(requestBody),
      });
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(response, "Failed to create task."),
        );
      }
      const created = (await response.json()) as ExecutionCreateResponse;
      if (inputArtifactRef) {
        await linkInputArtifact(inputArtifactRef, created);
      }
      const redirectPath =
        String(created.redirectPath || "").trim() ||
        (created.definitionId
          ? `/tasks/schedules/${encodeURIComponent(created.definitionId)}`
          : created.workflowId
            ? `/tasks/${encodeURIComponent(created.workflowId)}?source=temporal`
            : "");
      if (!redirectPath) {
        throw new Error("Task was created but no redirect path was returned.");
      }
      navigateTo(redirectPath);
    } catch (error) {
      const failure =
        error instanceof Error ? error : new Error("Failed to create task.");
      // Detect network-level fetch failures (TypeError: "Failed to fetch")
      // and log additional diagnostics to help troubleshoot.
      if (
        failure instanceof TypeError &&
        failure.message === "Failed to fetch"
      ) {
        console.error(
          "[TaskCreate] Network-level fetch failure during task creation.",
          {
            endpoint: temporalCreateEndpoint,
            errorName: failure.name,
            errorMessage: failure.message,
            possibleCauses: [
              "API service may be unreachable or not running",
              "CORS policy is blocking the request (check browser devtools Network tab for CORS errors)",
              "Network connectivity issue or proxy misconfiguration",
              "TLS/SSL certificate error (if using HTTPS)",
            ].join("; "),
          },
        );
        setSubmitMessage(
          "Failed to reach the task creation API. " +
            `Endpoint: ${temporalCreateEndpoint}. ` +
            "This usually means the API service is unreachable, a CORS policy is blocking the request, " +
            "or there is a network connectivity issue. Check the browser console for more details.",
        );
      } else {
        setSubmitMessage(failure.message);
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="stack">
      <div>
        <h2 className="page-title">Create Task</h2>
      </div>

      <form
        id="queue-submit-form"
        className="queue-submit-form"
        onSubmit={handleSubmit}
      >
        <section className="queue-steps-section stack">
          <div id="queue-steps-list" className="stack">
            <datalist id={SKILL_OPTIONS_DATALIST_ID}>
              <option value="auto" />
              {(skillsQuery.data || []).map((skillId) => (
                <option key={skillId} value={skillId} />
              ))}
            </datalist>
            <datalist id={MODEL_OPTIONS_DATALIST_ID}>
              {modelOptions.map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>
            <datalist id={EFFORT_OPTIONS_DATALIST_ID}>
              {effortOptions.map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>

            {steps.map((step, index) => {
              const isPrimaryStep = index === 0;
              const stepLabel = isPrimaryStep ? " (Primary)" : "";
              const showSkillArgsField = shouldShowSkillArgs(step);
              return (
                <section
                  key={step.localId}
                  className="card stack queue-step-section"
                  data-step-index={index}
                >
                  <div className="queue-step-header">
                    <strong>{`Step ${index + 1}${stepLabel}`}</strong>
                    <div
                      className="queue-step-controls"
                      role="group"
                      aria-label={`Step ${index + 1} controls`}
                    >
                      <button
                        type="button"
                        className="queue-step-icon-button"
                        data-step-action="up"
                        data-step-index={index}
                        disabled={index === 0}
                        aria-label="Move step up"
                        title="Move step up"
                        onClick={() => moveStep(index, -1)}
                      >
                        <ArrowUpIcon />
                        <span className="sr-only">Move step up</span>
                      </button>
                      <button
                        type="button"
                        className="queue-step-icon-button"
                        data-step-action="down"
                        data-step-index={index}
                        disabled={index === steps.length - 1}
                        aria-label="Move step down"
                        title="Move step down"
                        onClick={() => moveStep(index, 1)}
                      >
                        <ArrowDownIcon />
                        <span className="sr-only">Move step down</span>
                      </button>
                      <button
                        type="button"
                        className="queue-step-icon-button destructive"
                        data-step-action="remove"
                        data-step-index={index}
                        aria-label="Remove step"
                        title="Remove step"
                        onClick={() => removeStep(index)}
                      >
                        <CloseIcon />
                        <span className="sr-only">Remove step</span>
                      </button>
                    </div>
                  </div>

                  <label>
                    Instructions
                    <textarea
                      className="queue-step-instructions"
                      data-step-field="instructions"
                      data-step-index={String(index)}
                      placeholder={
                        isPrimaryStep
                          ? "Describe the task to execute against the repository."
                          : "Step-specific instructions (leave blank to continue from the task objective)."
                      }
                      value={step.instructions}
                      onChange={(event) =>
                        updateStep(step.localId, {
                          instructions: event.target.value,
                        })
                      }
                    />
                  </label>

                  <div className="grid-2">
                    <label>
                      Skill (optional)
                      <input
                        data-step-field="skillId"
                        data-step-index={String(index)}
                        list={SKILL_OPTIONS_DATALIST_ID}
                        value={step.skillId}
                        placeholder={
                          isPrimaryStep
                            ? "auto (default), speckit-orchestrate, ..."
                            : "inherit primary step skill"
                        }
                        onChange={(event) =>
                          updateStep(step.localId, {
                            skillId: event.target.value,
                          })
                        }
                      />
                      <span className="small">
                        {isPrimaryStep
                          ? "Primary step must include instructions or an explicit skill."
                          : "Leave skill blank to inherit primary step defaults."}
                      </span>
                    </label>

                    <label>
                      Skill Required Capabilities (optional CSV)
                      <input
                        data-step-field="skillRequiredCapabilities"
                        data-step-index={String(index)}
                        value={step.skillRequiredCapabilities}
                        placeholder="docker,qdrant,unity"
                        onChange={(event) =>
                          updateStep(step.localId, {
                            skillRequiredCapabilities: event.target.value,
                          })
                        }
                      />
                    </label>
                  </div>

                  <label
                    className={
                      showSkillArgsField
                        ? "queue-step-skill-args-field"
                        : "queue-step-skill-args-field hidden"
                    }
                    data-skill-args-index={String(index)}
                  >
                    Skill Args (optional JSON object)
                    <textarea
                      className="queue-step-skill-args"
                      data-step-field="skillArgs"
                      data-step-index={String(index)}
                      placeholder='{"notes":"optional context"}'
                      value={step.skillArgs}
                      onChange={(event) =>
                        updateStep(step.localId, {
                          skillArgs: event.target.value,
                        })
                      }
                    />
                  </label>
                </section>
              );
            })}

            <div className="actions queue-step-add">
              <button type="button" data-step-action="add" onClick={addStep}>
                Add Step
              </button>
            </div>
          </div>
        </section>

        {taskTemplateCatalogEnabled ? (
          <div className="card stack">
            <div className="actions">
              <strong>Task Presets (optional)</strong>
            </div>
            <label>
              Preset
              <select
                id="queue-template-select"
                value={selectedPresetKey}
                onChange={(event) => {
                  setSelectedPresetKey(event.target.value);
                  setTemplateMessage(null);
                }}
              >
                <option value="">Select preset...</option>
                {templateItems.map((item) => (
                  <option key={item.key} value={item.key}>
                    {`${item.title} (${scopeLabel(item.scope)})`}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Feature Request / Initial Instructions
              <textarea
                id="queue-template-feature-request"
                placeholder="Describe the feature request this preset should execute."
                value={templateFeatureRequest}
                onChange={(event) =>
                  setTemplateFeatureRequest(event.target.value)
                }
              />
            </label>
            <div className="actions">
              <button
                type="button"
                id="queue-template-apply"
                onClick={handleApplyPreset}
                aria-disabled={isApplyingPreset}
                aria-busy={isApplyingPreset}
              >
                Apply
              </button>
              {taskTemplateSaveEnabled ? (
                <button
                  type="button"
                  id="queue-template-save-current"
                  onClick={handleSaveCurrentStepsAsPreset}
                >
                  Save Current Steps as Preset
                </button>
              ) : null}
            </div>
            <p className="small" id="queue-template-message">
              {presetStatusText}
            </p>
          </div>
        ) : null}

        <label>
          Runtime
          <select
            name="runtime"
            value={runtime}
            onChange={(event) => setRuntime(event.target.value)}
          >
            {supportedTaskRuntimes.map((runtimeOption) => (
              <option key={runtimeOption} value={runtimeOption}>
                {runtimeOption}
              </option>
            ))}
          </select>
        </label>

        <div
          id="queue-provider-profile-wrap"
          className={providerOptions.length > 0 ? "" : "hidden"}
          hidden={providerOptions.length === 0}
        >
          <label>
            Provider profile
            <select
              name="providerProfile"
              value={providerProfile}
              onChange={(event) => setProviderProfile(event.target.value)}
            >
              <option value="">Default (system chooses)</option>
              {providerOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <p className="small" id="queue-auth-profile-hint">
            {providerProfilesQuery.isError
              ? "Failed to load provider profiles."
              : ""}
          </p>
        </div>

        <div className="grid-2">
          <label>
            Model
            <input
              name="model"
              list={MODEL_OPTIONS_DATALIST_ID}
              value={model}
              placeholder="runtime default"
              onChange={(event) => {
                const next = event.target.value;
                setModel(next);
                setModelManualOverride(next !== "");
              }}
            />
          </label>
          <label>
            Effort
            <input
              name="effort"
              list={EFFORT_OPTIONS_DATALIST_ID}
              value={effort}
              placeholder="runtime default"
              onChange={(event) => setEffort(event.target.value)}
            />
          </label>
        </div>

        <label>
          GitHub Repo
          <input
            name="repository"
            value={repository}
            placeholder="owner/repo"
            onChange={(event) => setRepository(event.target.value)}
          />
          <span className="small">
            {defaultRepository
              ? `Leave blank to use default repository: ${defaultRepository}. `
              : "Set a repository in this form (no system default repository is configured). "}
            Accepted formats: owner/repo, https://&lt;host&gt;/&lt;path&gt;, or
            git@&lt;host&gt;:&lt;path&gt; (token-free).
          </span>
        </label>

        <div className="grid-2">
          <label>
            Starting Branch (optional)
            <input
              name="startingBranch"
              value={startingBranch}
              placeholder="repo default branch"
              onChange={(event) => setStartingBranch(event.target.value)}
            />
          </label>
          <label>
            Target Branch (optional)
            <input
              name="targetBranch"
              value={targetBranch}
              placeholder="auto-generated unless starting branch is non-default"
              onChange={(event) => setTargetBranch(event.target.value)}
            />
          </label>
        </div>

        <label>
          Publish Mode
          <select
            name="publishMode"
            value={publishMode}
            onChange={(event) => setPublishMode(event.target.value)}
          >
            <option value="pr">pr</option>
            <option value="branch">branch</option>
            <option value="none">none</option>
          </select>
        </label>

        {attachmentPolicy.enabled ? (
          <section className="card" data-runtime-visibility="worker">
            <label>
              Image Attachments (optional)
              <input
                type="file"
                id="queue-attachments-input"
                accept={attachmentPolicy.allowedContentTypes.join(",")}
                multiple
                onChange={(event) =>
                  setSelectedAttachmentFiles(
                    Array.from(event.currentTarget.files || []),
                  )
                }
              />
            </label>
            <p className="small" id="queue-attachments-message">
              {selectedAttachmentFiles.length === 0
                ? `Up to ${attachmentPolicy.maxCount} files, ${formatAttachmentBytes(attachmentPolicy.maxBytes)} each, ${formatAttachmentBytes(attachmentPolicy.totalBytes)} total.`
                : attachmentValidation.ok
                  ? `${selectedAttachmentFiles.length} attachment(s) selected (${formatAttachmentBytes(attachmentValidation.totalBytes)} total).`
                  : attachmentValidation.errors.join(" ")}
            </p>
            <ul className="list" id="queue-attachments-list">
              {selectedAttachmentFiles.map((file) => (
                <li
                  key={`${file.name}-${file.size}`}
                >{`${file.name} (${formatAttachmentBytes(file.size)})`}</li>
              ))}
            </ul>
          </section>
        ) : null}

        <div className="grid-2" data-runtime-visibility="worker">
          <label>
            Priority
            <div className="priority-slider-container">
              <input
                type="range"
                name="priority"
                min="-10"
                max="10"
                value={priority}
                onChange={(event) => setPriority(Number(event.target.value))}
              />
              <output>{priority}</output>
            </div>
          </label>
          <label>
            Max Attempts
            <input
              type="number"
              min="1"
              name="maxAttempts"
              value={maxAttempts}
              onChange={(event) => setMaxAttempts(Number(event.target.value))}
            />
          </label>
        </div>

        <label className="checkbox">
          <input
            type="checkbox"
            name="proposeTasks"
            checked={proposeTasks}
            onChange={(event) => setProposeTasks(event.target.checked)}
          />
          Propose Tasks
        </label>

        <details className="card" id="schedule-panel">
          <summary>
            <strong>Schedule (optional)</strong>
          </summary>
          <div className="stack" style={{ marginTop: "0.75rem" }}>
            <label>
              Schedule Mode
              <select
                name="scheduleMode"
                id="schedule-mode-select"
                value={scheduleMode}
                onChange={(event) =>
                  setScheduleMode(event.target.value as ScheduleMode)
                }
              >
                <option value="immediate">Immediate</option>
                <option value="once">
                  Deferred (run once at a specific time)
                </option>
                <option value="deferred_minutes">
                  Deferred (run in N minutes)
                </option>
                <option value="recurring">
                  Recurring (create a cron schedule)
                </option>
              </select>
            </label>

            <div
              id="schedule-once-fields"
              className={scheduleMode === "once" ? "" : "hidden"}
            >
              <label>
                Scheduled For
                <input
                  type="datetime-local"
                  name="scheduledFor"
                  id="schedule-datetime"
                  value={scheduledFor}
                  onChange={(event) => setScheduledFor(event.target.value)}
                />
              </label>
            </div>

            <div
              id="schedule-deferred-minutes-fields"
              className={scheduleMode === "deferred_minutes" ? "" : "hidden"}
            >
              <label>
                Minutes from now
                <input
                  type="number"
                  name="scheduleDeferredMinutes"
                  min="1"
                  max="525600"
                  step="1"
                  placeholder="e.g. 15"
                  value={scheduleDeferredMinutes}
                  onChange={(event) =>
                    setScheduleDeferredMinutes(event.target.value)
                  }
                />
              </label>
            </div>

            <div
              id="schedule-recurring-fields"
              className={scheduleMode === "recurring" ? "stack" : "hidden"}
            >
              <label>
                Cron Expression
                <input
                  name="scheduleCron"
                  placeholder="*/30 * * * *"
                  value={scheduleCron}
                  onChange={(event) => setScheduleCron(event.target.value)}
                />
              </label>
              <label>
                Timezone
                <input
                  name="scheduleTimezone"
                  placeholder="UTC"
                  value={scheduleTimezone}
                  onChange={(event) => setScheduleTimezone(event.target.value)}
                />
              </label>
              <label>
                Schedule Name
                <input
                  name="scheduleName"
                  placeholder="My recurring task"
                  value={scheduleName}
                  onChange={(event) => setScheduleName(event.target.value)}
                />
              </label>
            </div>
          </div>
        </details>

        <div className="actions">
          <button
            type="submit"
            className="queue-submit-primary"
            aria-disabled={isSubmitting}
            aria-busy={isSubmitting}
          >
            Create
          </button>
        </div>

        <p
          id="queue-submit-message"
          className={
            submitMessage
              ? "queue-submit-message notice error"
              : "queue-submit-message small"
          }
        >
          {submitMessage || ""}
        </p>
      </form>
    </div>
  );
}

mountPage(TaskCreatePage);
