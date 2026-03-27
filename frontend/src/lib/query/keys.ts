// query key conventions
export const queryKeys = {
  tasks: {
    all: ['tasks'] as const,
    list: (filters?: Record<string, unknown>) => ['tasks', 'list', filters] as const,
    detail: (taskId: string) => ['tasks', 'detail', taskId] as const,
  },
  proposals: {
    all: ['proposals'] as const,
    list: (filters?: Record<string, unknown>) => ['proposals', 'list', filters] as const,
  },
  schedules: {
    all: ['schedules'] as const,
    detail: (scheduleId: string) => ['schedules', 'detail', scheduleId] as const,
  },
  settings: {
    profile: ['settings', 'profile'] as const,
    workerPauseSnapshot: ['settings', 'workerPause', 'snapshot'] as const,
  }
};
