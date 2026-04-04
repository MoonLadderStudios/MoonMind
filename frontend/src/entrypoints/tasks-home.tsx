import { BootPayload } from '../boot/parseBootPayload';

export function TasksHomePage({ payload }: { payload: BootPayload }) {
  return (
    <div className="p-4 border rounded shadow-sm bg-white">
      <h1 className="text-xl font-bold mb-2">Hello from Tasks Home!</h1>
      <p>This is a test of the TypeScript frontend system.</p>
      <pre className="bg-gray-100 p-2 mt-2 text-sm">{JSON.stringify(payload, null, 2)}</pre>
    </div>
  );
}
export default TasksHomePage;
