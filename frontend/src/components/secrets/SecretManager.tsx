import { useState, FormEvent } from 'react';
import { useMutation, QueryClient } from '@tanstack/react-query';

interface SecretMetadata {
  slug: string;
  status: string;
  details: Record<string, unknown>;
  createdAt: string;
  updatedAt?: string;
}

interface SecretManagerProps {
  secrets: SecretMetadata[];
  onNotice: (notice: { level: 'ok' | 'error', text: string } | null) => void;
  queryClient: QueryClient;
}

export function SecretManager({ secrets, onNotice, queryClient }: SecretManagerProps) {
  const [slug, setSlug] = useState('');
  const [plaintext, setPlaintext] = useState('');
  const [isEditing, setIsEditing] = useState(false);

  // Rotation modal state
  const [rotatePromptOpen, setRotatePromptOpen] = useState(false);
  const [rotatePromptSlug, setRotatePromptSlug] = useState('');
  const [rotatePromptVal, setRotatePromptVal] = useState('');

  const createOp = useMutation({
    mutationFn: async ({ slug, plaintext }: { slug: string, plaintext: string }) => {
      const resp = await fetch('/api/v1/secrets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slug, plaintext, details: {} })
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error(errData.detail || 'Failed to create secret');
      }
      return resp.json();
    },
    onSuccess: () => {
      onNotice({ level: 'ok', text: 'Secret saved successfully.' });
      setSlug('');
      setPlaintext('');
      setIsEditing(false);
      queryClient.invalidateQueries({ queryKey: ['secrets'] });
    },
    onError: (err: Error) => onNotice({ level: 'error', text: err.message })
  });

  const updateOp = useMutation({
    mutationFn: async ({ slug, plaintext }: { slug: string, plaintext: string }) => {
      const resp = await fetch(`/api/v1/secrets/${slug}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plaintext })
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error(errData.detail || 'Failed to update secret');
      }
      return resp.json();
    },
    onSuccess: () => {
      onNotice({ level: 'ok', text: 'Secret updated successfully.' });
      setSlug('');
      setPlaintext('');
      setIsEditing(false);
      queryClient.invalidateQueries({ queryKey: ['secrets'] });
    },
    onError: (err: Error) => onNotice({ level: 'error', text: err.message })
  });
  
  const rotateOp = useMutation({
    mutationFn: async ({ slug, plaintext }: { slug: string, plaintext: string }) => {
      const resp = await fetch(`/api/v1/secrets/${slug}/rotate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plaintext })
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error(errData.detail || 'Failed to rotate secret');
      }
      return resp.json();
    },
    onSuccess: () => {
      onNotice({ level: 'ok', text: 'Secret rotated successfully.' });
      setSlug('');
      setPlaintext('');
      setIsEditing(false);
      queryClient.invalidateQueries({ queryKey: ['secrets'] });
    },
    onError: (err: Error) => onNotice({ level: 'error', text: err.message })
  });

  const deleteOp = useMutation({
    mutationFn: async (delSlug: string) => {
      const resp = await fetch(`/api/v1/secrets/${delSlug}`, { method: 'DELETE' });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error(errData.detail || 'Failed to delete secret');
      }
    },
    onSuccess: () => {
      onNotice({ level: 'ok', text: 'Secret deleted successfully.' });
      queryClient.invalidateQueries({ queryKey: ['secrets'] });
    },
    onError: (err: Error) => onNotice({ level: 'error', text: err.message })
  });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!slug) {
      onNotice({ level: 'error', text: 'Key Name (slug) is required.' });
      return;
    }
    
    if (isEditing) {
      // update
      if (!plaintext) {
        onNotice({ level: 'error', text: 'Provide a new secure value to update the secret.' });
        return;
      }
      updateOp.mutate({ slug, plaintext });
    } else {
      // create
      if (!plaintext) {
        onNotice({ level: 'error', text: 'Provide a secure value for the new secret.' });
        return;
      }
      createOp.mutate({ slug, plaintext });
    }
  };

  const handleEdit = (secSlug: string) => {
    setSlug(secSlug);
    setPlaintext('');
    setIsEditing(true);
  };
  
  const handleRotate = (secSlug: string) => {
    setRotatePromptSlug(secSlug);
    setRotatePromptVal('');
    setRotatePromptOpen(true);
  };

  const submitRotate = (e: FormEvent) => {
    e.preventDefault();
    if (rotatePromptVal) {
      rotateOp.mutate({slug: rotatePromptSlug, plaintext: rotatePromptVal});
      setRotatePromptOpen(false);
    }
  };

  const handleDelete = (secSlug: string) => {
    if (window.confirm(`Are you sure you want to completely delete ${secSlug}?`)) {
      deleteOp.mutate(secSlug);
    }
  };

  // Grouping status badges safely
  const renderStatus = (s: string) => {
     if (s === 'active') return <span className="badge badge-success">Active</span>;
     if (s === 'disabled') return <span className="badge badge-warning">Disabled</span>;
     if (s === 'rotated') return <span className="badge badge-neutral">Rotated</span>;
     return <span className="badge badge-error">{s}</span>;
  };

  return (
    <div className="system-settings-grid">
      <section className="card">
        <div className="card-header">
           <h3>Assigned Target Secrets</h3>
        </div>
        <div className="card-body">
           <table className="table" style={{width: '100%', textAlign: 'left', borderCollapse: 'collapse'}}>
             <thead>
               <tr style={{borderBottom: '1px solid #ddd'}}>
                  <th style={{padding: '8px'}}>Stored Ref Key</th>
                  <th style={{padding: '8px'}}>Status</th>
                  <th style={{padding: '8px'}}>Updated</th>
                  <th style={{padding: '8px'}}>Actions</th>
               </tr>
             </thead>
             <tbody>
               {secrets.length === 0 ? (
                 <tr>
                    <td colSpan={4} style={{padding: '8px', color: '#666', fontStyle: 'italic'}}>No secrets currently stored.</td>
                 </tr>
               ) : secrets.map(s => (
                 <tr key={s.slug} style={{borderBottom: '1px solid #eee'}}>
                    <td style={{padding: '8px', fontFamily: 'monospace', fontWeight: 'bold'}}>{s.slug}</td>
                    <td style={{padding: '8px'}}>{renderStatus(s.status)}</td>
                    <td style={{padding: '8px', fontSize: '0.85em', color: '#666'}}>
                       {s.updatedAt ? new Date(s.updatedAt).toLocaleString() : new Date(s.createdAt).toLocaleString()}
                    </td>
                    <td style={{padding: '8px'}}>
                       <div style={{display: 'flex', gap: '8px'}}>
                          <button type="button" onClick={() => handleEdit(s.slug)} className="btn btn-sm btn-outline">Edit</button>
                          <button type="button" onClick={() => handleRotate(s.slug)} className="btn btn-sm btn-outline">Rotate</button>
                          <button type="button" onClick={() => handleDelete(s.slug)} className="btn btn-sm btn-error">Delete</button>
                       </div>
                    </td>
                 </tr>
               ))}
             </tbody>
           </table>
        </div>
      </section>

      <section className="card system-settings-forms">
        <div className="card-header">
           <h3>{isEditing ? 'Update Secret Value' : 'Add New Secret'}</h3>
        </div>
        <div className="card-body">
           <form className="stack" onSubmit={handleSubmit}>
              <div className="field">
                 <label htmlFor="secSlug">Stored Ref Key (slug)</label>
                 <input 
                   id="secSlug" 
                   type="text" 
                   placeholder="e.g. ANTHROPIC_API_KEY"
                   value={slug}
                   onChange={e => setSlug(e.target.value)}
                   disabled={isEditing || createOp.isPending || updateOp.isPending}
                 />
                 <div className="field-hint">The unique locator used by the 'db://' resolver.</div>
              </div>
              
              <div className="field">
                 <label htmlFor="secPlaintext">Secure Value</label>
                 <input 
                   id="secPlaintext" 
                   type="password" 
                   placeholder={isEditing ? '••••••••' : 'Enter raw secret string'}
                   value={plaintext}
                   onChange={e => setPlaintext(e.target.value)}
                   disabled={createOp.isPending || updateOp.isPending}
                 />
                 <div className="field-hint">Stored strictly as ciphertext. Will never be rendered back.</div>
              </div>

              <div style={{display: 'flex', gap: '8px', marginTop: '16px'}}>
                 <button 
                   type="submit" 
                   className="settings-submit-btn" 
                   disabled={createOp.isPending || updateOp.isPending}
                 >
                   {isEditing ? 'Save New Value' : 'Encrypt & Store Secret'}
                 </button>
                 
                 {isEditing && (
                   <button 
                     type="button" 
                     className="btn btn-outline" 
                     onClick={() => {
                        setIsEditing(false);
                        setSlug('');
                        setPlaintext('');
                     }}
                   >
                     Cancel
                   </button>
                 )}
              </div>
           </form>
        </div>
      </section>

      {rotatePromptOpen && (
        <div style={{ position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000}}>
          <div className="card" style={{ padding: '20px', maxWidth: '400px', width: '100%', background: 'var(--mm-bg-card, #fff)' }}>
             <h3>Rotate Secret: {rotatePromptSlug}</h3>
             <form onSubmit={submitRotate} className="stack" style={{ marginTop: '16px' }}>
                <div className="field">
                   <label>New Secure Value</label>
                   <input type="password" value={rotatePromptVal} onChange={e => setRotatePromptVal(e.target.value)} autoFocus required />
                </div>
                <div style={{ display: 'flex', gap: '8px', marginTop: '16px', justifyContent: 'flex-end' }}>
                   <button type="button" className="btn btn-outline" onClick={() => setRotatePromptOpen(false)}>Cancel</button>
                   <button type="submit" className="settings-submit-btn">Rotate Now</button>
                </div>
             </form>
          </div>
        </div>
      )}
    </div>
  );
}
