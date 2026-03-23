"use strict";

const baseTaskRow = Object.freeze({
  source: "temporal",
  sourceLabel: "Temporal",
  id: "mm:workflow-123",
  taskId: "mm:workflow-123",
  workflowId: "mm:workflow-123",
  temporalRunId: "run-456",
  namespace: "moonmind",
  workflowType: "MoonMind.Run",
  entry: "run",
  queueName: "-",
  runtimeMode: "codex",
  skillId: "auto",
  rawStatus: "running",
  rawState: "running",
  temporalStatus: "running",
  closeStatus: null,
  title: "Temporal Task",
  createdAt: "2026-02-23T12:00:00Z",
  startedAt: "2026-02-23T12:05:00Z",
  finishedAt: null,
  updatedAt: "2026-02-23T12:05:00Z",
  closedAt: null,
  link: "/tasks/mm:workflow-123?source=temporal",
});

function createTaskRow(overrides = {}) {
  return {
    ...baseTaskRow,
    ...overrides,
  };
}

function createMixedRows() {
  return [
    createTaskRow(),
    createTaskRow({ id: "mm:workflow-456", taskId: "mm:workflow-456", workflowId: "mm:workflow-456", skillId: "spec", link: "/tasks/mm:workflow-456?source=temporal" }),
    createTaskRow({ id: "mm:workflow-789", taskId: "mm:workflow-789", workflowId: "mm:workflow-789", rawStatus: "queued", rawState: "queued", link: "/tasks/mm:workflow-789?source=temporal" }),
  ];
}

// Legacy aliases – keep exports backward-compatible during transition
const baseQueueRow = baseTaskRow;
const createQueueRow = createTaskRow;

module.exports = {
  baseTaskRow,
  baseQueueRow,
  createTaskRow,
  createQueueRow,
  createMixedRows,
};
