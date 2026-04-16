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
  taskInputSnapshot?: {
    available?: boolean;
    artifactRef?: string | null;
    snapshotVersion?: number | null;
    sourceKind?: string | null;
    reconstructionMode?: string | null;
    disabledReasons?: Record<string, string>;
    fallbackEvidenceRefs?: string[];
  } | null;
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
  steps: Array<{
    id: string;
    title: string;
    instructions: string;
    skillId: string;
    skillArgs: Record<string, unknown>;
    skillRequiredCapabilities: string[];
    templateStepId: string;
    templateInstructions: string;
  }>;
  appliedTemplates: Array<{
    slug: string;
    version: string;
    inputs: Record<string, unknown>;
    stepIds: string[];
    appliedAt: string;
    capabilities: string[];
  }>;
};

export type TemporalTaskEditUpdateName = 'UpdateInputs' | 'RequestRerun';

export type TemporalTaskEditingTelemetryEvent =
  | 'detail_edit_click'
  | 'detail_rerun_click'
  | 'draft_reconstruction_success'
  | 'draft_reconstruction_failure'
  | 'update_submit_attempt'
  | 'update_submit_result'
  | 'rerun_submit_attempt'
  | 'rerun_submit_result';

export type TemporalTaskEditingTelemetryPayload = {
  event: TemporalTaskEditingTelemetryEvent;
  mode?: TaskSubmitPageMode | 'detail';
  workflowId?: string | null;
  updateName?: TemporalTaskEditUpdateName;
  result?: 'success' | 'failure';
  reason?: string | null;
  applied?: string | null;
};

export type TemporalArtifactEditUpdatePayload = {
  updateName: TemporalTaskEditUpdateName;
  inputArtifactRef?: string;
  parametersPatch: Record<string, unknown>;
};

export function buildTemporalArtifactEditUpdatePayload({
  updateName,
  inputArtifactRef,
  parametersPatch,
}: {
  updateName: TemporalTaskEditUpdateName;
  inputArtifactRef?: string | null;
  parametersPatch: Record<string, unknown>;
}): TemporalArtifactEditUpdatePayload {
  const normalizedArtifactRef = String(inputArtifactRef || '').trim();
  return {
    updateName,
    ...(normalizedArtifactRef
      ? { inputArtifactRef: normalizedArtifactRef }
      : {}),
    parametersPatch,
  };
}

export function recordTemporalTaskEditingClientEvent(
  payload: TemporalTaskEditingTelemetryPayload,
): void {
  const boundedPayload = {
    event: payload.event,
    ...(payload.mode ? { mode: payload.mode } : {}),
    ...(payload.workflowId ? { workflowId: payload.workflowId } : {}),
    ...(payload.updateName ? { updateName: payload.updateName } : {}),
    ...(payload.result ? { result: payload.result } : {}),
    ...(payload.reason ? { reason: payload.reason.slice(0, 120) } : {}),
    ...(payload.applied ? { applied: payload.applied.slice(0, 80) } : {}),
  };

  try {
    window.dispatchEvent(
      new CustomEvent('moonmind:temporal-task-editing', {
        detail: boundedPayload,
      }),
    );
  } catch {
    // Telemetry must never affect task editing behavior.
  }

  try {
    console.info('moonmind.temporal_task_editing', boundedPayload);
  } catch {
    // Console availability varies in embedded browser contexts.
  }
}

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

function stringArrayValue(...values: unknown[]): string[] {
  for (const value of values) {
    if (!Array.isArray(value)) {
      continue;
    }
    const normalized = value
      .map((item) => String(item ?? '').trim())
      .filter(Boolean);
    if (normalized.length > 0) {
      return normalized;
    }
  }
  return [];
}

function firstObjectValue(...values: unknown[]): Record<string, unknown> {
  for (const value of values) {
    const normalized = objectValue(value);
    if (Object.keys(normalized).length > 0) {
      return normalized;
    }
  }
  return {};
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

function draftStepFrom(value: unknown): TemporalSubmissionDraft['steps'][number] | null {
  const step = objectValue(value);
  if (Object.keys(step).length === 0) {
    return null;
  }

  const tool = objectValue(step.tool);
  const skill = objectValue(step.skill);
  const instructions = stringValue(step.instructions);
  const id = stringValue(step.id);
  const templateStepId = stringValue(
    step.templateStepId,
    step.template_step_id,
    id.startsWith('tpl:') ? id : '',
  );
  const result = {
    id,
    title: stringValue(step.title),
    instructions,
    skillId: stringValue(tool.name, tool.id, skill.id, skill.name),
    skillArgs: firstObjectValue(tool.inputs, tool.args, skill.inputs, skill.args),
    skillRequiredCapabilities: stringArrayValue(
      tool.requiredCapabilities,
      skill.requiredCapabilities,
    ),
    templateStepId,
    templateInstructions: stringValue(
      step.templateInstructions,
      step.template_instructions,
      templateStepId ? instructions : '',
    ),
  };

  const hasContent =
    result.id ||
    result.title ||
    result.instructions ||
    result.skillId ||
    Object.keys(result.skillArgs).length > 0 ||
    result.skillRequiredCapabilities.length > 0 ||
    result.templateStepId ||
    result.templateInstructions;
  return hasContent ? result : null;
}

function draftStepsFromTask(
  task: Record<string, unknown>,
): TemporalSubmissionDraft['steps'] {
  const rawSteps = Array.isArray(task.steps) ? task.steps : [];
  const steps = rawSteps
    .map((entry) => draftStepFrom(entry))
    .filter((entry): entry is TemporalSubmissionDraft['steps'][number] =>
      Boolean(entry),
    );
  if (steps.length === 0) {
    return [];
  }

  return steps;
}

function selectDraftSteps(
  taskSteps: TemporalSubmissionDraft['steps'],
  artifactTaskSteps: TemporalSubmissionDraft['steps'],
): TemporalSubmissionDraft['steps'] {
  if (artifactTaskSteps.length > taskSteps.length) {
    return artifactTaskSteps;
  }
  return taskSteps.length > 0 ? taskSteps : artifactTaskSteps;
}

function nullableStringValue(...values: unknown[]): string | null {
  return stringValue(...values) || null;
}

function skillSelectorNames(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => stringValue(item)).filter(Boolean);
  }

  const selectors = objectValue(value);
  const include = selectors.include;
  if (!Array.isArray(include)) {
    return [];
  }
  return include
    .map((entry) => stringValue(objectValue(entry).name))
    .filter(Boolean);
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

function snapshotDraftTask(
  snapshotDraft: Record<string, unknown>,
): Record<string, unknown> {
  const nestedTask = objectValue(snapshotDraft.task);
  if (Object.keys(nestedTask).length > 0) {
    return nestedTask;
  }
  const primarySkill = objectValue(snapshotDraft.primarySkill);
  const publish = objectValue(snapshotDraft.publish);
  const task: Record<string, unknown> = {
    ...(stringValue(snapshotDraft.instructions)
      ? { instructions: stringValue(snapshotDraft.instructions) }
      : {}),
    ...(Object.keys(primarySkill).length > 0
      ? {
          tool: {
            type: 'skill',
            name: stringValue(primarySkill.name),
            version: stringValue(primarySkill.version) || '1.0',
            inputs: objectValue(primarySkill.inputs),
          },
          skill: {
            id: stringValue(primarySkill.name),
            args: objectValue(primarySkill.inputs),
          },
          inputs: objectValue(primarySkill.inputs),
        }
      : {}),
    ...(Object.keys(publish).length > 0 ? { publish } : {}),
    ...(Array.isArray(snapshotDraft.appliedTemplates)
      ? { appliedStepTemplates: snapshotDraft.appliedTemplates }
      : {}),
    ...(Array.isArray(snapshotDraft.steps) ? { steps: snapshotDraft.steps } : {}),
  };
  const startingBranch = stringValue(snapshotDraft.startingBranch);
  const targetBranch = stringValue(snapshotDraft.targetBranch);
  if (startingBranch || targetBranch) {
    task.git = {
      ...(startingBranch ? { startingBranch } : {}),
      ...(targetBranch ? { targetBranch } : {}),
    };
  }
  const runtime = stringValue(snapshotDraft.runtime);
  const model = stringValue(snapshotDraft.model);
  const effort = stringValue(snapshotDraft.effort);
  const providerProfile = stringValue(snapshotDraft.providerProfile);
  if (runtime || model || effort || providerProfile) {
    task.runtime = {
      ...(runtime ? { mode: runtime } : {}),
      ...(model ? { model } : {}),
      ...(effort ? { effort } : {}),
      ...(providerProfile ? { profileId: providerProfile } : {}),
    };
  }
  return task;
}

export function buildTemporalSubmissionDraftFromExecution(
  execution: TemporalTaskEditingExecutionContract,
  artifactInput?: unknown,
): TemporalSubmissionDraft {
  const params = objectValue(execution.inputParameters);
  const artifactParams = objectValue(artifactInput);
  const snapshotDraft = objectValue(artifactParams.draft);
  const task = objectValue(params.task);
  const artifactTask = Object.keys(snapshotDraft).length > 0
    ? snapshotDraftTask(snapshotDraft)
    : objectValue(artifactParams.task);
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
  const taskSteps = draftStepsFromTask(task);
  const artifactTaskSteps = draftStepsFromTask(artifactTask);

  const artifactRepository = stringValue(artifactParams.repository);
  const taskSkills = skillSelectorNames(task.skills);
  const executionTaskSkills = Array.isArray(execution.taskSkills)
    ? execution.taskSkills
    : [];
  const artifactTaskSkills = skillSelectorNames(artifactTask.skills);

  const draft: TemporalSubmissionDraft = {
    runtime: nullableStringValue(
      snapshotDraft.runtime,
      execution.targetRuntime,
      params.targetRuntime,
      runtime.mode,
      artifactRuntime.mode,
      artifactParams.targetRuntime,
    ),
    providerProfile: nullableStringValue(
      snapshotDraft.providerProfile,
      execution.profileId,
      params.profileId,
      runtime.profileId,
      artifactRuntime.profileId,
    ),
    model: nullableStringValue(
      snapshotDraft.model,
      execution.model,
      execution.requestedModel,
      execution.resolvedModel,
      params.model,
      runtime.model,
      artifactRuntime.model,
    ),
    effort: nullableStringValue(
      snapshotDraft.effort,
      execution.effort,
      params.effort,
      runtime.effort,
      artifactRuntime.effort,
    ),
    repository: nullableStringValue(
      snapshotDraft.repository,
      execution.repository,
      params.repository,
      task.repository,
      git.repository,
      artifactRepository,
      artifactTask.repository,
      artifactGit.repository,
    ),
    startingBranch: nullableStringValue(
      snapshotDraft.startingBranch,
      execution.startingBranch,
      task.startingBranch,
      git.startingBranch,
      params.startingBranch,
      artifactTask.startingBranch,
      artifactGit.startingBranch,
    ),
    targetBranch: nullableStringValue(
      snapshotDraft.targetBranch,
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
      executionTaskSkills[0],
      artifactTool.name,
      artifactSkill.id,
      artifactSkill.name,
      artifactTaskSkills[0],
    ),
    steps: selectDraftSteps(taskSteps, artifactTaskSteps),
    appliedTemplates: normalizeAppliedTemplates(
      task.appliedStepTemplates || artifactTask.appliedStepTemplates,
    ),
  };

  const hasStepContent = draft.steps.some(
    (step) => step.instructions || step.skillId,
  );
  if (!draft.taskInstructions && !draft.primarySkill && !hasStepContent) {
    throw new Error(
      'Task instructions could not be reconstructed from this execution.',
    );
  }

  return draft;
}
