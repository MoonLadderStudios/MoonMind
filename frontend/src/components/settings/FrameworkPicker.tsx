import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchFrameworkPacks, type CatalogFramework, type DeclaredFramework, type SavedFramework } from '../../lib/assessmentProjects';
import { Button } from '../Button';

const blankRegistration: DeclaredFramework = { packId: '', edition: '', revision: '', artifactRef: '', sha256: '' };
const identifier = /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/;
const labels: Record<keyof DeclaredFramework, string> = {
  packId: 'Pack ID', edition: 'Edition', revision: 'Revision', artifactRef: 'Artifact reference', sha256: 'SHA-256',
};

function selection(pack: CatalogFramework): SavedFramework | undefined {
  if (!pack.edition) return;
  if (pack.source !== 'declared') return { id: pack.id, edition: pack.edition };
  if (pack.artifactRef && pack.revision && pack.sha256) {
    return { packId: pack.id, edition: pack.edition, artifactRef: pack.artifactRef, revision: pack.revision, sha256: pack.sha256 };
  }
}

const choiceLabels: Record<string, string> = { 'general-best-practices': 'General best practices for applications' };

function label(pack: CatalogFramework): string {
  const choice = pack.choice && choiceLabels[pack.choice];
  return choice ? `${choice}: ${pack.title}` : pack.title;
}

function identity(value: SavedFramework): string {
  return JSON.stringify('id' in value ? [value.id, value.edition] : [value.packId, value.edition, value.artifactRef, value.revision, value.sha256]);
}

export function FrameworkPicker({ frameworks, onChange, allowRegistration }: {
  frameworks: SavedFramework[]; onChange: (values: SavedFramework[]) => void; allowRegistration: boolean;
}) {
  const catalog = useQuery({ queryKey: ['framework-packs'], queryFn: fetchFrameworkPacks });
  const [draft, setDraft] = useState<DeclaredFramework>(blankRegistration);
  const entries = catalog.data ?? [];
  const selected = new Set(frameworks.map(identity));
  const canRegister = [draft.packId, draft.edition, draft.revision].every(value => identifier.test(value)) &&
    /^art_[A-Za-z0-9_-]+$/.test(draft.artifactRef) && /^[0-9a-f]{64}$/.test(draft.sha256) &&
    !selected.has(identity(draft)) && frameworks.length < 16;
  return <fieldset className="space-y-2">
    <legend className="font-medium">Frameworks</legend>
    {frameworks.map((framework, index) => {
      const match = entries.find(pack => {
        const value = selection(pack);
        return value && identity(value) === identity(framework);
      });
      return <div key={index} className="space-x-2">
        <span>{match ? label(match) : ('id' in framework ? framework.id : framework.packId)} · {framework.edition}</span>
        {'artifactRef' in framework && <small className="break-all">{framework.artifactRef} · {framework.revision} · {framework.sha256}</small>}
        {!!match?.companionChecks?.length && <small className="block">Run with: {match.companionChecks.map(check => check.title).join('; ')}</small>}
        {match?.reason && <small>{match.reason}</small>}
        {!match && catalog.isSuccess && <small>Saved entry outside the current catalog</small>}
        <Button variant="ghost" onClick={() => onChange(frameworks.filter((_, i) => i !== index))}>Remove framework</Button>
      </div>;
    })}
    <label className="block text-sm">Add standard
      <select className="block w-full rounded border p-2" value="" disabled={catalog.isPending || catalog.isError || frameworks.length >= 16}
        onChange={event => {
          const pack = entries[Number(event.target.value)];
          const value = pack && selection(pack);
          if (pack?.available && value && !selected.has(identity(value))) onChange([...frameworks, value]);
        }}>
        <option value="">Choose a standard</option>
        {entries.map((pack, index) => {
          const value = selection(pack);
          return <option key={index} value={index} disabled={!pack.available || !value || selected.has(identity(value))}>
            {label(pack)} · {pack.edition}{pack.revision && ` · rev ${pack.revision}`}{pack.artifactRef && ` · ${pack.artifactRef}`}{pack.reason && ` · ${pack.reason}`}
          </option>;
        })}
      </select>
    </label>
    {catalog.isError && <p role="status">Standards could not be loaded. <Button variant="ghost" onClick={() => catalog.refetch()}>Retry</Button></p>}
    {allowRegistration && <details>
      <summary>Register a retained pack</summary>
      <div className="grid gap-2 sm:grid-cols-2">
        {(Object.keys(labels) as (keyof DeclaredFramework)[]).map(key => <label key={key} className="text-sm">{labels[key]}
          <input className="block w-full rounded border p-2" value={draft[key]} onChange={event => setDraft({ ...draft, [key]: event.target.value.trim() })} />
        </label>)}
      </div>
      <Button variant="ghost" disabled={!canRegister} onClick={() => {
        onChange([...frameworks, draft]);
        setDraft(blankRegistration);
      }}>Add registration to environment</Button>
    </details>}
  </fieldset>;
}
