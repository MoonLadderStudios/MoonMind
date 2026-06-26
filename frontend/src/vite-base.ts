export function dashboardViteBase(command: string): string {
  return command === "build" ? "/static/workflow_console/dist/" : "/";
}
