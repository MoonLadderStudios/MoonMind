export function missionControlViteBase(command: string): string {
  return command === "build" ? "/static/workflow_console/dist/" : "/";
}
