type SkillRuntime = {
  resolvedSkillsetRef?: string | null;
  selectedSkills?: string[];
  selectedVersions?: Array<{
    name: string;
    version?: string | null;
    sourceKind?: string | null;
    sourcePath?: string | null;
    contentRef?: string | null;
    contentDigest?: string | null;
  }>;
  sourceProvenance?: Array<{
    name: string;
    sourceKind?: string | null;
    sourcePath?: string | null;
  }>;
  materializationMode?: string | null;
  visiblePath?: string | null;
  backingPath?: string | null;
  readOnly?: boolean | null;
  manifestRef?: string | null;
  promptIndexRef?: string | null;
  activationSummaryRef?: string | null;
  diagnostics?: {
    path?: string | null;
    objectKind?: string | null;
    attemptedAction?: string | null;
    remediation?: string | null;
    cause?: string | null;
  } | null;
  lifecycleIntent?: {
    source: string;
    selectors?: string[];
    resolvedSkillsetRef?: string | null;
    resolutionMode: string;
    explanation: string;
  } | null;
};

type SkillProvenanceBadgeProps = {
  resolvedSkillsetRef?: string | null | undefined;
  taskSkills?: string[] | null | undefined;
  targetSkill?: string | null | undefined;
  skillRuntime?: SkillRuntime | null | undefined;
};

export function SkillProvenanceBadge({
  resolvedSkillsetRef,
  taskSkills,
  targetSkill,
  skillRuntime,
}: SkillProvenanceBadgeProps) {
  const hasExplicitSkills = Array.isArray(taskSkills) && taskSkills.length > 0;
  const runtimeRef = skillRuntime?.resolvedSkillsetRef || resolvedSkillsetRef;
  const selectedVersions = skillRuntime?.selectedVersions ?? [];
  const provenance = skillRuntime?.sourceProvenance ?? [];
  const diagnostics = skillRuntime?.diagnostics;
  const lifecycleIntent = skillRuntime?.lifecycleIntent;
  const selectedVersionText = selectedVersions
    .map((entry) => `${entry.name}${entry.version ? `@${entry.version}` : ''}`)
    .join(', ');
  const provenanceText = provenance
    .map((entry) => [entry.name, entry.sourceKind, entry.sourcePath].filter(Boolean).join(' · '))
    .join(', ');
  const diagnosticText = diagnostics
    ? [
        diagnostics.path,
        diagnostics.objectKind,
        diagnostics.attemptedAction,
        diagnostics.remediation,
        diagnostics.cause,
      ]
        .filter(Boolean)
        .join(' · ')
    : '';
  
  return (
    <div className="card mt-4 bg-gray-50 border border-gray-200 p-4 rounded-lg">
      <h3 className="mt-0 text-base">Agent Skill Provenance</h3>
      <dl className="queue-card-fields mb-0">
        <div>
          <dt>Explicit Selection</dt>
          <dd>{hasExplicitSkills ? taskSkills.join(', ') : 'None'}</dd>
        </div>
        <div>
          <dt>Delegated Skill</dt>
          <dd>{targetSkill || '—'}</dd>
        </div>
        <div>
          <dt>Resolved Snapshot Ref</dt>
          <dd>
            {runtimeRef ? (
              <code className="small break-all">{runtimeRef}</code>
            ) : (
              <span className="small text-muted">No snapshot bound for this run.</span>
            )}
          </dd>
        </div>
        {selectedVersionText ? (
          <div>
            <dt>Selected Versions</dt>
            <dd>{selectedVersionText}</dd>
          </div>
        ) : null}
        {provenanceText ? (
          <div>
            <dt>Source Provenance</dt>
            <dd>{provenanceText}</dd>
          </div>
        ) : null}
        {skillRuntime?.materializationMode ? (
          <div>
            <dt>Materialization</dt>
            <dd>{skillRuntime.materializationMode}</dd>
          </div>
        ) : null}
        {skillRuntime?.visiblePath ? (
          <div>
            <dt>Visible Path</dt>
            <dd>
              <code className="small break-all">{skillRuntime.visiblePath}</code>
            </dd>
          </div>
        ) : null}
        {skillRuntime?.backingPath ? (
          <div>
            <dt>Backing Path</dt>
            <dd>
              <code className="small break-all">{skillRuntime.backingPath}</code>
            </dd>
          </div>
        ) : null}
        {typeof skillRuntime?.readOnly === 'boolean' ? (
          <div>
            <dt>Read Only</dt>
            <dd>{skillRuntime.readOnly ? 'Yes' : 'No'}</dd>
          </div>
        ) : null}
        {skillRuntime?.manifestRef ? (
          <div>
            <dt>Manifest Ref</dt>
            <dd>
              <code className="small break-all">{skillRuntime.manifestRef}</code>
            </dd>
          </div>
        ) : null}
        {skillRuntime?.promptIndexRef ? (
          <div>
            <dt>Prompt Index Ref</dt>
            <dd>
              <code className="small break-all">{skillRuntime.promptIndexRef}</code>
            </dd>
          </div>
        ) : null}
        {skillRuntime?.activationSummaryRef ? (
          <div>
            <dt>Activation Summary Ref</dt>
            <dd>
              <code className="small break-all">{skillRuntime.activationSummaryRef}</code>
            </dd>
          </div>
        ) : null}
        {lifecycleIntent ? (
          <div>
            <dt>Lifecycle Intent</dt>
            <dd>
              {[lifecycleIntent.source, lifecycleIntent.resolutionMode, lifecycleIntent.explanation]
                .filter(Boolean)
                .join(' · ')}
            </dd>
          </div>
        ) : null}
        {diagnosticText ? (
          <div>
            <dt>Projection Diagnostic</dt>
            <dd>{diagnosticText}</dd>
          </div>
        ) : null}
      </dl>
      {!runtimeRef && (
        <p className="small text-muted mt-2 mb-0">
          * Without a snapshot ref, the worker invokes standard legacy tools.
        </p>
      )}
    </div>
  );
}
