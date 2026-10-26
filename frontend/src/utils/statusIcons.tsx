import {
  Ban,
  Check,
  CircleQuestionMark,
  Forward,
  Hand,
  Hourglass,
  Link,
  Map as MapIcon,
  Minus,
  Moon,
  PackageCheck,
  Play,
  Power,
  X,
  type LucideIcon,
} from 'lucide-react';
import { executionStatusPillProps } from './executionStatusPillClasses';
import { formatStatusLabel } from './formatters';
import { stepStatusPillProps } from '../status/stepStatus';
import {
  type WorkflowOutcomeOverride,
  workflowOutcomeOverride,
  workflowOutcomeOverridePresentation,
} from '../status/workflowStatus';

export const WORKFLOW_STATUS_ICONS = {
  scheduled: Moon,
  initializing: Power,
  waiting_on_dependencies: Link,
  planning: MapIcon,
  awaiting_slot: Hourglass,
  executing: Play,
  awaiting_external: Hand,
  finalizing: PackageCheck,
  no_commit: Minus,
  completed: Check,
  failed: X,
  canceled: Ban,
} as const satisfies Record<string, LucideIcon>;

export type WorkflowStatusIconKey = keyof typeof WORKFLOW_STATUS_ICONS;

const WORKFLOW_OUTCOME_ICONS = {
  handed_off: Forward,
  outcome_unknown: CircleQuestionMark,
  idle: Minus,
  objective_failed: X,
  verification_blocked: X,
  objective_cancelled: Ban,
} as const satisfies Record<WorkflowOutcomeOverride, LucideIcon>;
export type StatusIconDomain = 'workflow' | 'step';

export const CANONICAL_STEP_STATUSES = [
  'pending',
  'ready',
  'executing',
  'awaiting_external',
  'reviewing',
  'completed',
  'failed',
  'skipped',
  'canceled',
] as const;

export type CanonicalStepStatus = (typeof CANONICAL_STEP_STATUSES)[number];

const STEP_STATUS_ICON_KEYS = {
  pending: 'waiting_on_dependencies',
  ready: 'awaiting_slot',
  executing: 'executing',
  awaiting_external: 'awaiting_external',
  reviewing: 'finalizing',
  completed: 'completed',
  failed: 'failed',
  skipped: 'canceled',
  canceled: 'canceled',
} as const satisfies Record<CanonicalStepStatus, WorkflowStatusIconKey>;

const STATUS_ICON_LABEL_OVERRIDES: Record<string, string> = {
  waiting_on_dependencies: 'Awaiting dependencies',
};

function normalizedStatusKey(status: string | null | undefined): string {
  return String(status || '')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, '_');
}

function isWorkflowStatusIconKey(key: string): key is WorkflowStatusIconKey {
  return Object.prototype.hasOwnProperty.call(WORKFLOW_STATUS_ICONS, key);
}

function isCanonicalStepStatus(key: string): key is CanonicalStepStatus {
  return (CANONICAL_STEP_STATUSES as readonly string[]).includes(key);
}

function defaultStatusIconLabel(status: string | null | undefined): string {
  const key = normalizedStatusKey(status);
  const override = Object.prototype.hasOwnProperty.call(STATUS_ICON_LABEL_OVERRIDES, key)
    ? STATUS_ICON_LABEL_OVERRIDES[key]
    : undefined;
  if (override) return override;
  const label = formatStatusLabel(status);
  const knownStatus =
    isWorkflowStatusIconKey(key) ||
    isCanonicalStepStatus(key) ||
    key === 'running' ||
    key === 'succeeded' ||
    key === 'awaiting_action';
  return label === '—' || !label || !knownStatus
    ? label
    : `${label.charAt(0).toUpperCase()}${label.slice(1)}`;
}

export function statusIconKey(
  status: string | null | undefined,
  domain: StatusIconDomain,
): WorkflowStatusIconKey {
  let key = normalizedStatusKey(status);
  if (key === 'succeeded') {
    key = 'completed';
  } else if (key === 'running') {
    key = 'executing';
  }
  if (domain === 'step') {
    if (isCanonicalStepStatus(key)) {
      return STEP_STATUS_ICON_KEYS[key];
    }
    return 'executing';
  }
  if (isWorkflowStatusIconKey(key)) {
    return key;
  }
  if (key === 'awaiting_action') return 'awaiting_external';
  return 'executing';
}

export function StatusIcon({
  status,
  domain,
  completionDisposition,
  objectiveOutcome,
  className,
  title,
  'data-testid': testId,
}: {
  status: string | null | undefined;
  domain: StatusIconDomain;
  completionDisposition?: string | null | undefined;
  objectiveOutcome?: string | null | undefined;
  className?: string;
  title?: string;
  'data-testid'?: string;
}) {
  const override = domain === 'workflow'
    ? workflowOutcomeOverride(status, completionDisposition, objectiveOutcome)
    : null;
  const overridePresentation = override ? workflowOutcomeOverridePresentation(override) : null;
  const label = title ?? overridePresentation?.label ?? defaultStatusIconLabel(status);
  const Icon = override ? WORKFLOW_OUTCOME_ICONS[override] : WORKFLOW_STATUS_ICONS[statusIconKey(status, domain)];
  const pillProps = overridePresentation
    ? { className: overridePresentation.className }
    : domain === 'step'
      ? stepStatusPillProps(status)
      : executionStatusPillProps(status, { enableMotion: false });

  return (
    <span
      {...pillProps}
      className={`${pillProps.className}${className ? ` ${className}` : ''}`}
      aria-label={`Status: ${label}`}
      title={label}
      data-testid={testId}
    >
      <Icon aria-hidden="true" focusable="false" />
    </span>
  );
}
