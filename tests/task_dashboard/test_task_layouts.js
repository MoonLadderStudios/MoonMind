/* eslint-env node */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const DASHBOARD_JS = path.join(
  __dirname,
  "..",
  "..",
  "api_service",
  "static",
  "task_dashboard",
  "dashboard.js",
);
const {
  baseTaskRow,
  createTaskRow,
  createMixedRows,
} = require("./__fixtures__/task_rows");

function createVmContext() {
  const rootNode = {
    innerHTML: "",
    querySelectorAll() {
      return [];
    },
    querySelector() {
      return null;
    },
  };
  const configNode = { textContent: JSON.stringify({}) };
  const documentElement = {
    classList: { toggle() {}, add() {}, remove() {} },
    dataset: {},
  };
  const documentStub = {
    documentElement,
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    getElementById(id) {
      if (id === "task-dashboard-config") {
        return configNode;
      }
      if (id === "dashboard-content") {
        return rootNode;
      }
      return null;
    },
  };
  const windowStub = {
    document: documentStub,
    addEventListener() {},
    removeEventListener() {},
    matchMedia() {
      return {
        matches: false,
        addEventListener() {},
        removeEventListener() {},
      };
    },
    localStorage: {
      getItem() {
        return null;
      },
      setItem() {},
      removeItem() {},
    },
    __MOONMIND_DASHBOARD_TEST: { skipInitialRender: true },
    location: { pathname: "/tasks/queue" },
  };
  const context = {
    window: windowStub,
    document: documentStub,
    console,
    HTMLInputElement: function HTMLInputElement() {},
    URLSearchParams,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
  };
  windowStub.HTMLInputElement = context.HTMLInputElement;
  windowStub.URLSearchParams = URLSearchParams;
  return context;
}

function loadQueueLayoutHelpers() {
  const source = fs.readFileSync(DASHBOARD_JS, "utf8");
  const context = createVmContext();
  vm.runInNewContext(source, context, { filename: "dashboard.js" });
  const helpers = context.window.__queueLayoutTest;
  assert(helpers, "Expected queue layout helpers to be exposed via window.__queueLayoutTest");
  return helpers;
}

const helpers = loadQueueLayoutHelpers();
const {
  taskFieldDefinitions,
  renderTaskFieldValue,
  renderTaskTable,
  renderTaskCards,
  renderTaskLayouts,
  renderActivePageContent,
  renderRowsTable,
  renderProposalTable,
  renderProposalCards,
  renderProposalLayouts,
  renderProposalActionFeedback,
  filterProposalsByTag,
  sortRows,
  sortRowsByColumn,
  rowOrderKey,
  buildRowOrderIndex,
  stabilizeRowsByPreviousOrder,
  toTemporalRows,
  parseQueuePaginationFromSearch,
  applyQueuePaginationToSearch,
} = helpers;


function createProposalRow(overrides = {}) {
  return {
    id: "proposal-123",
    title: "Add coverage for proposals list",
    repository: "moonmind/moonmind",
    category: "run_quality",
    reviewPriority: "high",
    status: "open",
    createdAt: "2026-02-23T12:00:00Z",
    origin: {
      source: "queue",
      id: "job-abc-123",
    },
    tags: ["loop_detected", "ci"],
    dedupHash: "abcd1234efgh5678",
    taskPreview: {
      instructions: "Tighten validation around proposals output.",
      repository: "moonmind/moonmind",
    },
    ...overrides,
  };
}

(function testQueueFieldDefinitionsProvideSingleSourceOfTruth() {
  const keys = taskFieldDefinitions.map((definition) => definition.key);
  const expectedKeys = [
    "runtimeMode",
    "skillId",
    "scheduledFor",
    "startedAt",
    "finishedAt",
  ];
  assert.strictEqual(keys.length, expectedKeys.length);
  assert.strictEqual(keys.join(","), expectedKeys.join(","));
  const labels = taskFieldDefinitions.map((definition) => definition.label);
  assert(labels.includes("Finished"));
  const rendered = renderTaskFieldValue(
    {
      runtimeMode: "codex",
      skillId: "auto",
      createdAt: "2026-02-23T12:00:00Z",
    },
    taskFieldDefinitions.find((definition) => definition.key === "skillId"),
  );
  assert.strictEqual(rendered, "auto");
})();

(function testRenderQueueTableUsesFieldDefinitions() {
  const html = renderTaskTable([createTaskRow()]);
  assert(html.includes('data-sort-field="type"'), 'Expected sortable Type th');
  assert(html.includes('data-field="finishedAt"'));
  assert(html.includes("status-running"));
})();


(function testRenderQueueTableEscapesTitleLabel() {
  const html = renderTaskTable([
    createTaskRow({ title: '<img src=x onerror=alert(1)>' }),
  ]);
  assert(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert(!html.includes('<img src=x onerror=alert(1)>'));
})();

(function testQueueDefinitionOrderMatchesTableHeaders() {
  const html = renderTaskTable([createTaskRow()]);
  // Dynamic definition-driven headers use data-sort-field via sortableTh
  const headerOrder = Array.from(html.matchAll(/data-sort-field="([^"]+)"/g)).map(
    (match) => match[1],
  );
  // Table order: type, id, primaryFields (non-timeline), status, title, timelineFields
  const primaryFields = taskFieldDefinitions
    .filter((d) => d.tableSection !== "timeline")
    .map((d) => d.key);
  const timelineFields = taskFieldDefinitions
    .filter((d) => d.tableSection === "timeline")
    .map((d) => d.key);
  const expectedOrder = ["type", "id", ...primaryFields, "status", "title", ...timelineFields];
  assert.strictEqual(headerOrder.join(","), expectedOrder.join(","));
})();

(function testRenderRowsTableDelegatesToQueueTable() {
  const html = renderRowsTable([createTaskRow()]);
  assert(html.includes('data-sort-field="type"'), 'Expected sortable Type th');
})();

(function testRenderQueueTableWithSortStateAddsAriaSort() {
  const sortState = { field: "title", direction: "asc" };
  const html = renderTaskTable([createTaskRow()], sortState);
  assert(html.includes('aria-sort="ascending"'), 'Expected ascending aria-sort on title column');
  assert(html.includes('class="sortable-header sort-asc"'), 'Expected sort-asc class on title column');
  assert(html.includes('\u25b2'), 'Expected ascending indicator \u25b2');
  // Non-active columns should have aria-sort=none
  assert(html.includes('aria-sort="none"'), 'Expected aria-sort=none on non-active columns');
})();

(function testRenderQueueTableWithDescSortStateAddsAriaSortDescending() {
  const sortState = { field: "scheduledFor", direction: "desc" };
  const html = renderTaskTable([createTaskRow()], sortState);
  assert(html.includes('aria-sort="descending"'), 'Expected descending aria-sort on scheduledFor column');
  assert(html.includes('class="sortable-header sort-desc"'), 'Expected sort-desc class on scheduledFor column');
  assert(html.includes('\u25bc'), 'Expected descending indicator \u25bc');
})();

(function testRenderQueueTableWithoutSortStateHasNoActiveClass() {
  const html = renderTaskTable([createTaskRow()]);
  assert(!html.includes('sort-asc'), 'No sort-asc without sortState');
  assert(!html.includes('sort-desc'), 'No sort-desc without sortState');
})();

(function testSortRowsByColumnSortsTitleAscending() {
  const rows = [
    createTaskRow({ id: 'job-1', title: 'Zebra task' }),
    createTaskRow({ id: 'job-2', title: 'Apple task' }),
    createTaskRow({ id: 'job-3', title: 'Mango task' }),
  ];
  const sorted = sortRowsByColumn(rows, 'title', 'asc');
  assert.strictEqual(sorted[0].title, 'Apple task');
  assert.strictEqual(sorted[1].title, 'Mango task');
  assert.strictEqual(sorted[2].title, 'Zebra task');
})();

(function testSortRowsByColumnSortsTitleDescending() {
  const rows = [
    createTaskRow({ id: 'job-1', title: 'Zebra task' }),
    createTaskRow({ id: 'job-2', title: 'Apple task' }),
    createTaskRow({ id: 'job-3', title: 'Mango task' }),
  ];
  const sorted = sortRowsByColumn(rows, 'title', 'desc');
  assert.strictEqual(sorted[0].title, 'Zebra task');
  assert.strictEqual(sorted[1].title, 'Mango task');
  assert.strictEqual(sorted[2].title, 'Apple task');
})();

(function testSortRowsByColumnSortsCreatedAtAscending() {
  const rows = [
    createTaskRow({ id: 'job-a', createdAt: '2026-03-10T00:00:00Z' }),
    createTaskRow({ id: 'job-b', createdAt: '2026-03-08T00:00:00Z' }),
    createTaskRow({ id: 'job-c', createdAt: '2026-03-12T00:00:00Z' }),
  ];
  const sorted = sortRowsByColumn(rows, 'createdAt', 'asc');
  assert.strictEqual(sorted[0].id, 'job-b');
  assert.strictEqual(sorted[1].id, 'job-a');
  assert.strictEqual(sorted[2].id, 'job-c');
})();

(function testSortRowsByColumnSortsCreatedAtDescending() {
  const rows = [
    createTaskRow({ id: 'job-a', createdAt: '2026-03-10T00:00:00Z' }),
    createTaskRow({ id: 'job-b', createdAt: '2026-03-08T00:00:00Z' }),
    createTaskRow({ id: 'job-c', createdAt: '2026-03-12T00:00:00Z' }),
  ];
  const sorted = sortRowsByColumn(rows, 'createdAt', 'desc');
  assert.strictEqual(sorted[0].id, 'job-c');
  assert.strictEqual(sorted[1].id, 'job-a');
  assert.strictEqual(sorted[2].id, 'job-b');
})();

(function testSortRowsByColumnSortsByStatus() {
  const rows = [
    createTaskRow({ id: 'job-1', rawStatus: 'completed' }),
    createTaskRow({ id: 'job-2', rawStatus: 'failed' }),
    createTaskRow({ id: 'job-3', rawStatus: 'running' }),
  ];
  const sorted = sortRowsByColumn(rows, 'status', 'asc');
  assert.strictEqual(sorted[0].rawStatus, 'completed');
  assert.strictEqual(sorted[1].rawStatus, 'failed');
  assert.strictEqual(sorted[2].rawStatus, 'running');
})();

(function testSortRowsByColumnScheduledForFallsBackToCreatedAt() {
  const rows = [
    createTaskRow({ id: 'job-a', scheduledFor: null, createdAt: '2026-03-10T00:00:00Z' }),
    createTaskRow({ id: 'job-b', scheduledFor: null, createdAt: '2026-03-12T00:00:00Z' }),
    createTaskRow({ id: 'job-c', scheduledFor: '2026-03-15T00:00:00Z', createdAt: '2026-03-08T00:00:00Z' }),
  ];
  const sorted = sortRowsByColumn(rows, 'scheduledFor', 'desc');
  assert.strictEqual(sorted[0].id, 'job-c', 'Explicit scheduledFor should sort first when newest');
  assert.strictEqual(sorted[1].id, 'job-b', 'Null scheduledFor should fall back to createdAt');
  assert.strictEqual(sorted[2].id, 'job-a', 'Oldest createdAt should sort last');
})();

(function testSortRowsByColumnDoesNotMutateOriginalArray() {
  const rows = [
    createTaskRow({ id: 'job-1', title: 'Zebra task' }),
    createTaskRow({ id: 'job-2', title: 'Apple task' }),
  ];
  const originalOrder = rows.map((r) => r.id).join(',');
  sortRowsByColumn(rows, 'title', 'asc');
  assert.strictEqual(rows.map((r) => r.id).join(','), originalOrder, 'Original array should not be mutated');
})();

(function testToTemporalRowsNormalizesExecutionPayload() {
  const rows = toTemporalRows([
    {
      workflowId: "mm:workflow-123",
      runId: "run-456",
      namespace: "moonmind",
      workflowType: "MoonMind.Run",
      state: "awaiting_external",
      temporalStatus: "running",
      closeStatus: null,
      searchAttributes: {
        mm_entry: "run",
        mm_owner_id: "user-123",
        mm_updated_at: "2026-03-06T11:00:00Z",
      },
      memo: {
        title: "Temporal task",
        summary: "Execution paused.",
      },
      startedAt: "2026-03-06T10:00:00Z",
      updatedAt: "2026-03-06T11:00:00Z",
      closedAt: null,
      attentionRequired: true,
    },
  ]);

  assert.strictEqual(rows.length, 1);
  assert.strictEqual(rows[0].source, "temporal");
  assert.strictEqual(rows[0].sourceLabel, "Temporal");
  assert.strictEqual(rows[0].id, "mm:workflow-123");
  assert.strictEqual(rows[0].taskId, "mm:workflow-123");
  assert.strictEqual(rows[0].temporalRunId, "run-456");
  assert.strictEqual(rows[0].entry, "run");
  assert.strictEqual(rows[0].ownerId, "user-123");
  assert.strictEqual(rows[0].waitingReason, "Execution paused.");
  assert.strictEqual(rows[0].attentionRequired, true);
  assert.strictEqual(rows[0].link, "/tasks/mm:workflow-123?source=temporal");
})();





(function testRowOrderKeyUsesNormalizedSourceAndId() {
  assert.strictEqual(
    rowOrderKey({ source: " Queue ", id: "job-1" }),
    "queue:job-1",
  );
  assert.strictEqual(
    rowOrderKey({ source: "", id: "" }),
    "unknown:",
  );
})();

(function testStableOrderKeepsExistingRowsInPreviousSequence() {
  const previousRows = [
    createTaskRow({ id: "job-a", createdAt: "2026-03-10T00:00:00Z" }),
    createTaskRow({ id: "job-b", createdAt: "2026-03-09T00:00:00Z" }),
    createTaskRow({ id: "job-c", createdAt: "2026-03-08T00:00:00Z" }),
  ];
  const previousIndex = buildRowOrderIndex(previousRows);
  const refreshedRows = [
    createTaskRow({ id: "job-a", createdAt: "2026-03-08T00:00:00Z" }),
    createTaskRow({ id: "job-b", createdAt: "2026-03-11T00:00:00Z" }),
    createTaskRow({ id: "job-c", createdAt: "2026-03-07T00:00:00Z" }),
  ];

  const stabilized = stabilizeRowsByPreviousOrder(refreshedRows, previousIndex);
  assert.strictEqual(
    stabilized.map((row) => row.id).join(","),
    previousRows.map((row) => row.id).join(","),
  );
})();

(function testStableOrderSurfacesNewRowsBeforeExistingRows() {
  const previousRows = [
    createTaskRow({ id: "job-a", createdAt: "2026-03-10T00:00:00Z" }),
    createTaskRow({ id: "job-b", createdAt: "2026-03-09T00:00:00Z" }),
  ];
  const previousIndex = buildRowOrderIndex(previousRows);
  const refreshedRows = [
    createTaskRow({ id: "job-a", createdAt: "2026-03-12T00:00:00Z" }),
    createTaskRow({ id: "job-new", createdAt: "2026-03-11T00:00:00Z" }),
    createTaskRow({ id: "job-b", createdAt: "2026-03-08T00:00:00Z" }),
  ];

  const stabilized = stabilizeRowsByPreviousOrder(refreshedRows, previousIndex);
  assert.strictEqual(stabilized[0].id, "job-new");
  assert.strictEqual(
    stabilized.slice(1).map((row) => row.id).join(","),
    "job-a,job-b",
  );
})();

(function testStableOrderFallsBackToDefaultSortWithoutHistory() {
  const rows = [
    createTaskRow({ id: "job-a", createdAt: "2026-03-10T00:00:00Z" }),
    createTaskRow({ id: "job-b", createdAt: "2026-03-12T00:00:00Z" }),
  ];
  const stabilized = stabilizeRowsByPreviousOrder(rows, new Map());
  const expected = sortRows(rows);
  assert.strictEqual(
    stabilized.map((row) => row.id).join(","),
    expected.map((row) => row.id).join(","),
  );
})();

(function testRenderQueueCardsRendersAllRows() {
  const rows = [
    createTaskRow(),
    createTaskRow({ id: "job-456", source: "manifests", sourceLabel: "Manifests" }),
  ];
  const cardsHtml = renderTaskCards(rows);
  assert(cardsHtml.includes("queue-card"));
  assert(cardsHtml.includes(baseTaskRow.id));
  assert(cardsHtml.includes("job-456"), "Cards should contain non-queue rows");
  taskFieldDefinitions.forEach((definition) => {
    assert(cardsHtml.includes(`<dt>${definition.label}</dt>`), `${definition.label} missing in card`);
  });
})();

(function testRenderQueueLayoutsCombinesTableAndCards() {
  const rows = [
    createTaskRow(),
  ];
  const html = renderTaskLayouts(rows);
  assert(html.includes("queue-table-wrapper"));
  assert(html.includes("queue-card-list"));
})();

(function testRenderQueueLayoutsEmptyState() {
  const html = renderTaskLayouts([]);
  assert.strictEqual(html.trim(), "<p class='small'>No rows available.</p>");
})();

(function testActivePageContentKeepsTablesForMixedSources() {
  const rows = createMixedRows();
  const html = renderActivePageContent(rows, ["queue-running"]);
  assert(html.includes("queue-card-list"));
  assert(html.includes(rows[0].id));
  const cardSectionMatch = html.match(/<ul class="queue-card-list"[^>]*>([\s\S]*?)<\/ul>/);
  assert(cardSectionMatch, "Expected queue-card-list markup");
  assert(cardSectionMatch[1].includes(rows[2].id), "Non-queue rows should be in cards");
  assert(html.includes("Unable to load queue-running data source."));
})();

(function testExtendingFieldDefinitionsUpdatesBothLayouts() {
  const definition = {
    key: "publishMode",
    label: "Publish Mode",
    render: () => "branch",
    tableSection: "primary",
  };
  taskFieldDefinitions.push(definition);
  try {
    const cardsHtml = renderTaskCards([createTaskRow()]);
    const tableHtml = renderTaskTable([createTaskRow()]);
    assert(cardsHtml.includes(definition.label));
    assert(cardsHtml.includes("branch"));
    assert(tableHtml.includes('data-field="publishMode"'));
  } finally {
    taskFieldDefinitions.pop();
  }
})();

(function testRenderProposalLayoutsIncludeDesktopTableAndMobileCards() {
  const rows = [createProposalRow()];
  const html = renderProposalLayouts(rows);
  assert(html.includes('data-layout="table"'));
  assert(html.includes('data-layout="card"'));
  assert(html.includes('queue-table-wrapper'));
  assert(html.includes('queue-card-list'));
})();

(function testRenderProposalActionFeedbackTargetsStatusRegion() {
  const html = renderProposalActionFeedback({
    message: "Proposal fa862809 dismissed.",
    statusFilter: "dismissed",
  });
  assert(html.includes("proposal-action-feedback"));
  assert(html.includes("Proposal fa862809 dismissed."));
  assert(html.includes("/tasks/proposals?status=dismissed"));
})();

(function testProposalCardsExposeStableFieldsAndActions() {
  const rows = [
    createProposalRow({
      id: "proposal-456",
      title: "Improve CI validation",
      dedupHash: "1234abcd5678efgh",
    }),
  ];
  const html = renderProposalCards(rows);
  assert(html.includes('data-proposal-id="proposal-456"'));
  assert(html.includes('data-proposal-title'));
  assert(html.includes('data-proposal-repo'));
  assert(html.includes('data-field="id"'));
  assert(html.includes('data-field="category"'));
  assert(html.includes('data-field="priority"'));
  assert(html.includes('data-proposal-action="promote"'));
  assert(html.includes('data-proposal-action="dismiss"'));
})();

(function testProposalTableUsesStableRowAndActionSelectors() {
  const rows = [createProposalRow({ id: "proposal-789" })];
  const html = renderProposalTable(rows);
  const tagged = filterProposalsByTag(rows, "loop_detected");
  const none = filterProposalsByTag(rows, "missing-tag");
  assert.strictEqual(tagged.length, 1);
  assert.strictEqual(none.length, 0);
  assert(html.includes('data-proposal-id="proposal-789"'));
  assert(html.includes('data-proposal-action="promote"'));
  assert(html.includes('data-proposal-action="dismiss"'));
})();
