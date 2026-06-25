import { BootPayload } from '../boot/parseBootPayload';

export function WorkflowsHomePage({ payload: _payload }: { payload: BootPayload }) {
  return (
    <main className="space-y-6" aria-label="MoonMind dashboard">
      <header>
        <p className="eyebrow">MoonMind</p>
        <h1>Workflows</h1>
        <p className="subhead">
          Review workflow executions, inspect status, and open run evidence from the workflow list.
        </p>
      </header>

      <section className="space-y-4" aria-label="Workflow navigation">
        <a href="/workflows" className="button">
          Open workflows
        </a>
      </section>
    </main>
  );
}

export default WorkflowsHomePage;
