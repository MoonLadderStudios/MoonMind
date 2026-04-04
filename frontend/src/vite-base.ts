export function missionControlViteBase(command: string): string {
  return command === "build" ? "/static/task_dashboard/dist/" : "/";
}
