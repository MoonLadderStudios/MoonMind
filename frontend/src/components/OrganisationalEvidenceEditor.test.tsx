import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithClient, screen, waitFor } from '../utils/test-utils';
import { OrganisationalEvidenceEditor } from './OrganisationalEvidenceEditor';

function Editor({ initial }: { initial: Record<string, unknown> }) {
  const [values, setValues] = useState(initial);
  return <><OrganisationalEvidenceEditor values={values} onChange={(name, value) => setValues({ ...values, [name]: value })} /><output data-testid="inputs">{JSON.stringify(values)}</output></>;
}
describe('Organisational evidence Tool inputs', () => {
  it('adds scoped requests without overwriting other requirements or retained revisions', () => {
    const prior = { id: 'evidence-2', revisions: [{ artifactRef: 'art_prior', review: { reason: 'Original review' } }] };
    renderWithClient(<Editor initial={{ controlAssessment: { requirements: [{ id: 'policy', organisationalEvidence: [prior] }, { id: 'training', organisationalEvidence: [] }] } }} />);
    fireEvent.click(screen.getByText('Organisational evidence requests and review'));
    fireEvent.change(screen.getByLabelText('Evidence requirement'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add evidence request' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add evidence request' }));
    const rows = JSON.parse(screen.getByTestId('inputs').textContent!).controlAssessment.requirements;
    expect(rows[0].organisationalEvidence).toHaveLength(3);
    expect(rows[0].organisationalEvidence[0]).toEqual(prior);
    expect(new Set(rows[0].organisationalEvidence.map((item: {id: string}) => item.id)).size).toBe(3);
    expect(rows[1].organisationalEvidence).toEqual([]);
    fireEvent.change(screen.getByLabelText('Evidence requirement'), { target: { value: '1' } });
    expect(screen.getByText(/0 requests linked/)).toBeTruthy();
    expect(screen.getByText(/Assertions and generated drafts cannot/)).toBeTruthy();
  });
  it('edits declared pack choices and preserves pack identity', () => {
    renderWithClient(<Editor initial={{ frameworkPack: { artifactRef: 'art_pack', requirementChoices: { A: { applicability: 'applicable' } } } }} />);
    fireEvent.click(screen.getByText('Organisational evidence requests and review'));
    fireEvent.change(screen.getByLabelText('Evidence requirement'), { target: { value: 'A' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add evidence request' }));
    const pack = JSON.parse(screen.getByTestId('inputs').textContent!).frameworkPack;
    expect(pack.artifactRef).toBe('art_pack');
    expect(pack.requirementChoices.A.applicability).toBe('applicable');
    expect(pack.requirementChoices.A.organisationalEvidence[0].revisions).toEqual([]);
  });
  it('explains missing requirements rather than creating an invalid plan', () => {
    renderWithClient(<Editor initial={{}} />);
    fireEvent.click(screen.getByText('Organisational evidence requests and review'));
    expect(screen.getByText(/First declare controlAssessment/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Add evidence request' })).toBeNull();
  });
});

it('preserves a failed import for retry, coalesces clicks and exposes the completed reference', async () => {
  const upload = vi.fn().mockRejectedValueOnce(new Error('denied')).mockResolvedValue('art_document');
  renderWithClient(<OrganisationalEvidenceEditor values={{}} onChange={() => undefined} onImport={upload} />);
  fireEvent.click(screen.getByText('Organisational evidence requests and review'));
  const file = new File(['controlled fixture'], 'policy.txt', { type: 'text/plain' });
  fireEvent.change(screen.getByLabelText('Import organisational evidence'), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('button', { name: 'Upload evidence' }));
  expect((await screen.findByRole('alert')).textContent).toContain('selected file is preserved');
  fireEvent.click(screen.getByRole('button', { name: 'Upload evidence' }));
  await waitFor(() => expect(screen.getByText('art_document')).toBeTruthy());
  expect(upload).toHaveBeenCalledTimes(2);
  expect(upload).toHaveBeenLastCalledWith(file);
});
