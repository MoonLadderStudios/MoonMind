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

export function statusIconKey(
  status: string | null | undefined,
  domain: StatusIconDomain,
): WorkflowStatusIconKey {
  const key = normalizedStatusKey(status);
  if (domain === 'step') {
    if (isCanonicalStepStatus(key)) {
      return STEP_STATUS_ICON_KEYS[key];
    }
    return 'executing';
  }
  if (isWorkflowStatusIconKey(key)) {
    return key;
  }
  if (key === 'succeeded') return 'completed';
  if (key === 'running') return 'executing';
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
  const label = title ?? formatStatusLabel(status);
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
