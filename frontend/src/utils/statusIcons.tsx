import {
  Ban,
  CalendarClock,
  Check,
  Hand,
  Hourglass,
  Lightbulb,
  Link,
  Map as MapIcon,
  PackageCheck,
  Play,
  Power,
  X,
  type LucideIcon,
} from 'lucide-react';
import { executionStatusPillProps } from './executionStatusPillClasses';
import { formatStatusLabel } from './formatters';

export const WORKFLOW_STATUS_ICONS = {
  scheduled: CalendarClock,
  initializing: Power,
  waiting_on_dependencies: Link,
  planning: MapIcon,
  awaiting_slot: Hourglass,
  executing: Play,
  proposals: Lightbulb,
  awaiting_external: Hand,
  finalizing: PackageCheck,
  no_commit: Check,
  no_changes: Check,
  completed: Check,
  failed: X,
  canceled: Ban,
} as const satisfies Record<string, LucideIcon>;

export type WorkflowStatusIconKey = keyof typeof WORKFLOW_STATUS_ICONS;
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
  reviewing: 'proposals',
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
  className,
  title,
  'data-testid': testId,
}: {
  status: string | null | undefined;
  domain: StatusIconDomain;
  className?: string;
  title?: string;
  'data-testid'?: string;
}) {
  const label = title ?? defaultStatusIconLabel(status);
  const Icon = WORKFLOW_STATUS_ICONS[statusIconKey(status, domain)];
  const pillProps = executionStatusPillProps(status, { enableMotion: false });

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
