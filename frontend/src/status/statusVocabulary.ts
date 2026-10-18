/**
 * Label and pill-tone vocabularies for statuses that are neither workflow
 * lifecycle nor step ledger states: checkpoint branches and their turns,
 * dependency gate outcomes, and step checks. Keys mirror what the API emits
 * (`moonmind/statuses/checkpoint_branch.py`, the dependency gate in
 * `MoonMind.Run`, and step-ledger check results). An unlisted value still
 * renders readably, humanized on a neutral pill, and warns once per value.
 */
type StatusEntry = readonly [label: string, className: string];

export type StatusVocabulary = Readonly<{
  label: (status: string | null | undefined, fallback?: string) => string;
  pillProps: (status: string | null | undefined) => Readonly<{ className: string }>;
}>;

function normalizedStatusKey(status: string | null | undefined): string {
  return String(status || '').toLowerCase().trim().replace(/[\s-]+/g, '_');
}

function humanizeStatus(key: string): string {
  const words = key.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function defineStatusVocabulary(domain: string, entries: Record<string, StatusEntry>): StatusVocabulary {
  const warned = new Set<string>();
  const entryFor = (status: string | null | undefined): StatusEntry | null => {
    const key = normalizedStatusKey(status);
    if (Object.prototype.hasOwnProperty.call(entries, key)) return entries[key]!;
    if (key && !warned.has(key)) {
      warned.add(key);
      console.warn(`Unknown ${domain} status: ${key}`);
    }
    return null;
  };
  return {
    label: (status, fallback = 'Unknown') => {
      const key = normalizedStatusKey(status);
      if (!key) return fallback;
      return entryFor(status)?.[0] ?? humanizeStatus(key);
    },
    pillProps: (status) => ({
      className: (normalizedStatusKey(status) && entryFor(status)?.[1]) || 'status status-neutral',
    }),
  };
}

// Tones follow docs/UI/WorkflowStatusColorSemantics.md: live work uses the
// executing hue, preparation the near-execution hue, operator or dependency
// waits yellow, and archived or superseded records stay neutral.
const QUEUED = 'status status-scheduled';
const SETUP = 'status status-initializing';
const RUNNING = 'status status-running';
const AWAITING_ACTION = 'status status-awaiting-action';
const AWAITING_DEPENDENCIES = 'status status-awaiting-dependencies';
const OK = 'status status-completed';
const FAILED = 'status status-failed';
const CANCELED = 'status status-canceled';
const NEUTRAL = 'status status-neutral';

export const CHECKPOINT_BRANCH_STATUS = defineStatusVocabulary('checkpoint branch', {
  created: ['Created', QUEUED],
  preparing: ['Preparing', SETUP],
  active: ['Active', RUNNING],
  blocked: ['Blocked', AWAITING_ACTION],
  failed: ['Failed', FAILED],
  succeeded: ['Succeeded', OK],
  promotable: ['Promotable', AWAITING_ACTION],
  promoted: ['Promoted', OK],
  archived: ['Archived', NEUTRAL],
  superseded: ['Superseded', NEUTRAL],
  unknown: ['Unknown', NEUTRAL],
});

export const CHECKPOINT_BRANCH_TURN_STATUS = defineStatusVocabulary('checkpoint branch turn', {
  created: ['Created', QUEUED],
  preparing: ['Preparing', SETUP],
  running: ['Running', RUNNING],
  checking: ['Checking', RUNNING],
  succeeded: ['Succeeded', OK],
  failed: ['Failed', FAILED],
  blocked: ['Blocked', AWAITING_ACTION],
  canceled: ['Canceled', CANCELED],
  superseded: ['Superseded', NEUTRAL],
  unknown: ['Unknown', NEUTRAL],
});

/**
 * Dependency outcomes that are not workflow lifecycle states. Prerequisite and
 * dependent rows otherwise carry the other workflow's lifecycle state.
 */
export const DEPENDENCY_OUTCOME_STATUS = defineStatusVocabulary('dependency outcome', {
  terminated: ['Terminated', FAILED],
  timed_out: ['Timed out', FAILED],
  bypassed: ['Bypassed', NEUTRAL],
  waiting_for_successful_rerun: ['Waiting for rerun', AWAITING_DEPENDENCIES],
  unknown: ['Unknown', NEUTRAL],
});

export const STEP_CHECK_STATUS = defineStatusVocabulary('step check', {
  pending: ['Pending', RUNNING],
  passed: ['Passed', OK],
  failed: ['Failed', FAILED],
  inconclusive: ['Inconclusive', NEUTRAL],
});