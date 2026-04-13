export type TaskSubmitPageMode = 'create' | 'edit' | 'rerun';

export type TaskSubmitPageModeResolution = {
  mode: TaskSubmitPageMode;
  executionId: string | null;
};

export type TemporalTaskEditingActions = {
  canUpdateInputs?: boolean;
  canRerun?: boolean;
  disabledReasons?: Record<string, string>;
};

export type TemporalTaskEditingExecutionContract = {
  workflowId: string;
  workflowType?: string | null;
  state?: string | null;
  rawState?: string | null;
  temporalStatus?: string | null;
  inputParameters?: Record<string, unknown>;
  inputArtifactRef?: string | null;
  planArtifactRef?: string | null;
  targetRuntime?: string | null;
  profileId?: string | null;
  model?: string | null;
  requestedModel?: string | null;
  resolvedModel?: string | null;
  effort?: string | null;
  repository?: string | null;
  startingBranch?: string | null;
  targetBranch?: string | null;
  publishMode?: string | null;
  targetSkill?: string | null;
  taskSkills?: string[] | null;
  actions?: TemporalTaskEditingActions;
};

export type TemporalSubmissionDraft = {
  runtime: string | null;
  providerProfile: string | null;
  model: string | null;
  effort: string | null;
  repository: string | null;
  startingBranch: string | null;
  targetBranch: string | null;
  publishMode: string | null;
  taskInstructions: string;
  primarySkill: string | null;
  appliedTemplates: Array<{
    slug: string;
    version: string;
    inputs: Record<string, unknown>;
    stepIds: string[];
    appliedAt: string;
    capabilities: string[];
  }>;
};

export function taskCreateHref(): string {
  return '/tasks/new';
}

export function taskEditHref(workflowId: string): string {
  return `${taskCreateHref()}?editExecutionId=${encodeURIComponent(workflowId)}`;
}

export function taskRerunHref(workflowId: string): string {
  return `${taskCreateHref()}?rerunExecutionId=${encodeURIComponent(workflowId)}`;
}

export function resolveTaskSubmitPageMode(
  search: string | URLSearchParams,
): TaskSubmitPageModeResolution {
  const params =
    typeof search === 'string' ? new URLSearchParams(search) : search;
  const rerunExecutionId = String(params.get('rerunExecutionId') || '').trim();
  if (rerunExecutionId) {
    return { mode: 'rerun', executionId: rerunExecutionId };
  }
  const editExecutionId = String(params.get('editExecutionId') || '').trim();
  if (editExecutionId) {
    return { mode: 'edit', executionId: editExecutionId };
  }
  return { mode: 'create', executionId: null };
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function stringValue(...values: unknown[]): string {
  for (const value of values) {
    const normalized = String(value ?? '').trim();
    if (normalized) {
      return normalized;
    }
  }
  return '';
}

function stepInstructions(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((entry) => objectValue(entry))
    .map((entry) => stringValue(entry.instructions))
    .filter(Boolean);
}

function taskInstructionsFrom(...tasks: Record<string, unknown>[]): string {
  for (const task of tasks) {
    const instructions = [
      stringValue(task.instructions),
      ...stepInstructions(task.steps),
    ].filter(Boolean);
    if (instructions.length > 0) {
      return instructions.join('\n\n');
    }
  }
  return '';
}

function nullableStringValue(...values: unknown[]): string | null {
  return stringValue(...values) || null;
}

function normalizeAppliedTemplates(
  value: unknown,
): TemporalSubmissionDraft['appliedTemplates'] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((entry) => objectValue(entry))
    .map((entry) => ({
      slug: stringValue(entry.slug),
      version: stringValue(entry.version),
      inputs: objectValue(entry.inputs),
      stepIds: Array.isArray(entry.stepIds)
        ? entry.stepIds.map((item) => String(item || '').trim()).filter(Boolean)
        : [],
      appliedAt: stringValue(entry.appliedAt),
      capabilities: Array.isArray(entry.capabilities)
        ? entry.capabilities
            .map((item) => String(item || '').trim())
            .filter(Boolean)
        : [],
    }))
    .filter((entry) => entry.slug);
}

export function buildTemporalSubmissionDraftFromExecution(
  execution: TemporalTaskEditingExecutionContract,
  artifactInput?: unknown,
): TemporalSubmissionDraft {
  const params = objectValue(execution.inputParameters);
  const artifactParams = objectValue(artifactInput);
  const task = objectValue(params.task);
  const artifactTask = objectValue(artifactParams.task);
  const runtime = objectValue(task.runtime);
  const artifactRuntime = objectValue(artifactTask.runtime);
  const git = objectValue(task.git);
  const artifactGit = objectValue(artifactTask.git);
  const publish = objectValue(task.publish);
  const artifactPublish = objectValue(artifactTask.publish);
  const tool = objectValue(task.tool);
  const skill = objectValue(task.skill);
  const artifactTool = objectValue(artifactTask.tool);
  const artifactSkill = objectValue(artifactTask.skill);

  const artifactRepository = stringValue(artifactParams.repository);
  const taskSkills = Array.isArray(task.skills) ? task.skills : execution.taskSkills;
  const artifactTaskSkills = Array.isArray(artifactTask.skills)
    ? artifactTask.skills
    : [];

  const draft: TemporalSubmissionDraft = {
    runtime: nullableStringValue(
      execution.targetRuntime,
      params.targetRuntime,
      runtime.mode,
      artifactRuntime.mode,
      artifactParams.targetRuntime,
    ),
    providerProfile: nullableStringValue(
      execution.profileId,
      params.profileId,
      runtime.profileId,
      artifactRuntime.profileId,
    ),
    model: nullableStringValue(
      execution.model,
      execution.requestedModel,
      execution.resolvedModel,
      params.model,
      runtime.model,
      artifactRuntime.model,
    ),
    effort: nullableStringValue(
      execution.effort,
      params.effort,
      runtime.effort,
      artifactRuntime.effort,
    ),
    repository: nullableStringValue(
      execution.repository,
      params.repository,
      task.repository,
      git.repository,
      artifactRepository,
      artifactTask.repository,
      artifactGit.repository,
    ),
    startingBranch: nullableStringValue(
      execution.startingBranch,
      task.startingBranch,
      git.startingBranch,
      params.startingBranch,
      artifactTask.startingBranch,
      artifactGit.startingBranch,
    ),
    targetBranch: nullableStringValue(
      execution.targetBranch,
      task.targetBranch,
      git.targetBranch,
      params.targetBranch,
      artifactTask.targetBranch,
      artifactGit.targetBranch,
    ),
    publishMode: nullableStringValue(
      execution.publishMode,
      params.publishMode,
      publish.mode,
      artifactPublish.mode,
    ),
    taskInstructions: taskInstructionsFrom(task, artifactTask),
    primarySkill: nullableStringValue(
      execution.targetSkill,
      tool.name,
      skill.id,
      skill.name,
      taskSkills?.[0],
      artifactTool.name,
      artifactSkill.id,
      artifactSkill.name,
      artifactTaskSkills[0],
    ),
    appliedTemplates: normalizeAppliedTemplates(
      task.appliedStepTemplates || artifactTask.appliedStepTemplates,
    ),
  };

  if (!draft.taskInstructions) {
    throw new Error(
      'Task instructions could not be reconstructed from this execution.',
    );
  }

  return draft;
}
