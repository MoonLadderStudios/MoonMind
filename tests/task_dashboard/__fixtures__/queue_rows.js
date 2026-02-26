"use strict";

const baseQueueRow = Object.freeze({
  source: "queue",
  sourceLabel: "Queue",
  id: "job-123",
  queueName: "moonmind.jobs",
  runtimeMode: "codex",
  skillId: "auto",
  rawStatus: "running",
  title: "Queue Job",
  createdAt: "2026-02-23T12:00:00Z",
  startedAt: "2026-02-23T12:05:00Z",
  finishedAt: null,
  link: "/tasks/job-123?source=queue",
});

const baseOrchestratorRow = Object.freeze({
  source: "orchestrator",
  sourceLabel: "Orchestrator",
  id: "run-789",
  queueName: "moonmind.jobs",
  runtimeMode: "codex",
  skillId: "auto",
  rawStatus: "queued",
  title: "Orchestrator Run",
  createdAt: "2026-02-23T12:00:00Z",
  startedAt: null,
  finishedAt: null,
  link: "/tasks/run-789?source=orchestrator",
});

function createQueueRow(overrides = {}) {
  return {
    ...baseQueueRow,
    ...overrides,
  };
}

function createOrchestratorRow(overrides = {}) {
  return {
    ...baseOrchestratorRow,
    ...overrides,
  };
}

function createMixedRows() {
  return [
    createQueueRow(),
    createQueueRow({ id: "job-456", skillId: "spec" }),
    createOrchestratorRow(),
  ];
}

module.exports = {
  baseQueueRow,
  baseOrchestratorRow,
  createQueueRow,
  createOrchestratorRow,
  createMixedRows,
};
