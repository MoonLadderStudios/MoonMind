import { responseError } from './api/client';
import { newRequestId } from './requestId';

export const ASSESSMENT_PROJECTS_KEY = 'security.assessment_projects';
export interface AssessmentEnvironment {
  id: string;
  name: string;
  assets: string[];
  exclusions: string[];
  repositories: Array<{ repository: string; connectionRef?: string | null }>;
  artifactRefs: string[];
  originCandidates: string[];
  cloudAccountCandidates: Array<{ provider: 'aws'; account: string; regions: string[]; credentialRef?: string | null }>;
  connectionRefs: string[];
  credentialRefs: string[];
  dataClassification: 'public' | 'internal' | 'confidential' | 'restricted';
  frameworks: Array<{ id: string; edition: string }>;
  jurisdictions: string[];
  period: { start: string; end: string } | null;
  budget: { baseSeconds: number; maxSeconds: number; progressStallSeconds: number };
  retentionDays: number | null;
}
export interface AssessmentProject {
  id: string;
  name: string;
  purpose: string;
  boundaries: string;
  environments: AssessmentEnvironment[];
}
export interface AssessmentProjects { projects: AssessmentProject[] }
export interface AssessmentProjectSnapshot {
  schemaVersion: 'assessment-project/v1';
  projectId: string;
  projectName: string;
  environmentId: string;
  environmentName: string;
  sourceVersion: number;
  purpose: string;
  boundaries: string;
  configuration: AssessmentEnvironment;
  digest: string;
}
export interface AssessmentReferenceGap { field: string; code: string; message: string }
export interface AssessmentResolution { snapshot: AssessmentProjectSnapshot; gaps: AssessmentReferenceGap[] }

function environmentDefaults(): Omit<AssessmentEnvironment, 'id' | 'name'> {
  return {
    assets: [], exclusions: [], repositories: [], artifactRefs: [], originCandidates: [],
    cloudAccountCandidates: [], connectionRefs: [], credentialRefs: [], dataClassification: 'internal',
    frameworks: [], jurisdictions: [], period: null,
    budget: { baseSeconds: 60, maxSeconds: 120, progressStallSeconds: 60 }, retentionDays: null,
  };
}
export function createAssessmentEnvironment(): AssessmentEnvironment {
  return { ...environmentDefaults(), id: `environment-${newRequestId()}`, name: 'New environment' };
}
export function createAssessmentProject(): AssessmentProject {
  return { id: `system-${newRequestId()}`, name: 'New system', purpose: '', boundaries: '', environments: [createAssessmentEnvironment()] };
}
export function readAssessmentProjects(value: unknown): AssessmentProjects {
  const record = asRecord(value);
  return { projects: Array.isArray(record.projects) ? (record.projects as AssessmentProject[]).map((project) => ({
    ...project,
    environments: (project.environments || []).map((environment) => ({ ...environmentDefaults(), ...environment })),
  })) : [] };
}
export function copyAssessmentSnapshot(value: unknown): AssessmentProjectSnapshot | null {
  const record = asRecord(value);
  // Historical snapshots are copied verbatim: do not inject today's defaults
  // into a retained digest or re-resolve them against mutable Settings.
  return record.schemaVersion === 'assessment-project/v1'
    ? JSON.parse(JSON.stringify(record)) as AssessmentProjectSnapshot
    : null;
}
export async function resolveAssessmentProject(input: {
  projectId: string; environmentId: string; expectedVersion: number;
  overrides?: Partial<AssessmentEnvironment>;
}): Promise<AssessmentResolution> {
  const response = await fetch('/api/v1/settings/assessment-projects/resolve', {
    method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(input),
  });
  if (!response.ok) throw await responseError(response, 'Could not load assessment context');
  return response.json();
}
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

/** Opt-in schema metadata keeps context defaults in the ordinary tool editor. */
export function supportsAssessmentDefaults(schema: Record<string, unknown> | undefined): boolean {
  return schema?.['x-assessment-project-defaults'] === true;
}
const ASSESSMENT_REFERENCE_ALIASES = {
  reportRefs: 'report_refs', credentialRefs: 'credential_refs', networkRefs: 'network_refs',
} as const;

/** Canonicalize one authored layer, never choose between duplicate aliases. */
export function assessmentInputAliases(
  schema: Record<string, unknown> | undefined,
  values: Record<string, unknown>,
): { values: Record<string, unknown>; errors: Record<string, string> } {
  if (!supportsAssessmentDefaults(schema)) return { values, errors: {} };
  const normalized = { ...values };
  const errors: Record<string, string> = {};
  const properties = asRecord(schema?.properties);
  for (const [camel, snake] of Object.entries(ASSESSMENT_REFERENCE_ALIASES)) {
    if (!(camel in properties) || !Object.hasOwn(values, snake)) continue;
    if (Object.hasOwn(values, camel)) {
      errors[camel] = `Specify only one of ${camel} and ${snake} in Tool input JSON.`;
    } else {
      normalized[camel] = values[snake];
      delete normalized[snake];
    }
  }
  return { values: normalized, errors };
}

export function applyAssessmentInputDefaults(
  schema: Record<string, unknown> | undefined,
  values: Record<string, unknown>,
  snapshot: AssessmentProjectSnapshot,
): Record<string, unknown> {
  if (!supportsAssessmentDefaults(schema)) return values;
  const aliases = assessmentInputAliases(schema, values);
  if (Object.keys(aliases.errors).length) return values;
  const config = snapshot.configuration;
  const result = aliases.values;
  const defaults: Record<string, unknown> = {
    scope: { assets: config.assets, exclusions: config.exclusions }, budget: config.budget,
    reportRefs: config.artifactRefs, credentialRefs: [], networkRefs: [],
  };
  const properties = asRecord(schema?.properties);
  for (const [key, value] of Object.entries(defaults)) {
    if (key in properties && !Object.hasOwn(result, key)) result[key] = JSON.parse(JSON.stringify(value));
  }
  if ('tools' in properties && !Object.hasOwn(result, 'tools')) {
    result.tools = Array.isArray(result.reportRefs) && result.reportRefs.length ? ['retained-report'] : [];
  }
  if ('assessmentProject' in properties) {
    delete result.assessment_project;
    result.assessmentProject = copyAssessmentSnapshot(snapshot);
  }
  return result;
}

/** Resolve local schema references only; never fetch a schema URL. */
export function resolveSchemaProperty(schema: unknown, root: Record<string, unknown>, seen: Set<string> = new Set()): Record<string, unknown> {
  const property = asRecord(schema);
  const ref = property.$ref;
  if (typeof ref !== 'string' || !ref.startsWith('#/') || seen.has(ref)) return property;
  const target = ref.slice(2).split('/').reduce<unknown>((node, part) => asRecord(node)[part.replaceAll('~1', '/').replaceAll('~0', '~')], root);
  return { ...resolveSchemaProperty(target, root, new Set([...seen, ref])), ...property };
}
