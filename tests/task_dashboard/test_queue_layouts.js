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
  baseQueueRow,
  createQueueRow,
  createMixedRows,
  createOrchestratorRow,
} = require("./__fixtures__/queue_rows");

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
  queueFieldDefinitions,
  renderQueueFieldValue,
  renderQueueTable,
  renderQueueCards,
  renderQueueLayouts,
  renderActivePageContent,
  renderRowsTable,
  renderProposalTable,
  renderProposalCards,
  renderProposalLayouts,
  renderProposalActionFeedback,
  filterProposalsByTag,
  parseQueuePaginationFromSearch,
  applyQueuePaginationToSearch,
  resetQueuePaginationState,
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
  const keys = queueFieldDefinitions.map((definition) => definition.key);
  const expectedKeys = [
    "finishOutcome",
    "runtimeMode",
    "skillId",
    "createdAt",
    "startedAt",
    "finishedAt",
  ];
  assert.strictEqual(keys.length, expectedKeys.length);
  assert.strictEqual(keys.join(","), expectedKeys.join(","));
  const labels = queueFieldDefinitions.map((definition) => definition.label);
  assert(labels.includes("Outcome"));
  assert(labels.includes("Finished"));
  const rendered = renderQueueFieldValue(
    {
      runtimeMode: "codex",
      skillId: "auto",
      createdAt: "2026-02-23T12:00:00Z",
    },
    queueFieldDefinitions.find((definition) => definition.key === "skillId"),
  );
  assert.strictEqual(rendered, "auto");
})();

(function testRenderQueueTableUsesFieldDefinitions() {
  const html = renderQueueTable([createQueueRow()]);
  assert(html.includes("<th>Type</th>"));
  assert(html.includes('data-field="finishedAt"'));
  assert(html.includes("status-running"));
})();


(function testRenderQueueTableEscapesTitleLabel() {
  const html = renderQueueTable([
    createQueueRow({ title: '<img src=x onerror=alert(1)>' }),
  ]);
  assert(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert(!html.includes('<img src=x onerror=alert(1)>'));
})();

(function testQueueDefinitionOrderMatchesTableHeaders() {
  const html = renderQueueTable([createQueueRow()]);
  const headerOrder = Array.from(html.matchAll(/<th data-field="([^"]+)"/g)).map(
    (match) => match[1],
  );
  const expectedHeaderOrder = queueFieldDefinitions.map((definition) => definition.key);
  assert.strictEqual(headerOrder.join(","), expectedHeaderOrder.join(","));
})();

(function testRenderRowsTableDelegatesToQueueTable() {
  const html = renderRowsTable([createQueueRow()]);
  assert(html.includes("<th>Type</th>"));
})();

(function testQueuePaginationParsesLimitAndCursorFromUrlQuery() {
  const parsed = parseQueuePaginationFromSearch("?source=queue&limit=100&cursor=next-token");
  assert.strictEqual(parsed.limit, 100);
  assert.strictEqual(parsed.cursor, "next-token");

  const fallback = parseQueuePaginationFromSearch("?limit=999&cursor=   ");
  assert.strictEqual(fallback.limit, 50);
  assert.strictEqual(fallback.cursor, null);
})();

(function testQueuePaginationQuerySyncUpdatesLimitAndCursor() {
  const nextQuery = applyQueuePaginationToSearch(
    "source=queue&filterRuntime=codex&cursor=stale",
    25,
    "cursor-2",
  );
  const nextParams = new URLSearchParams(nextQuery);
  assert.strictEqual(nextParams.get("source"), "queue");
  assert.strictEqual(nextParams.get("filterRuntime"), "codex");
  assert.strictEqual(nextParams.get("limit"), "25");
  assert.strictEqual(nextParams.get("cursor"), "cursor-2");

  const firstPageQuery = applyQueuePaginationToSearch(nextQuery, 50, null);
  const firstPageParams = new URLSearchParams(firstPageQuery);
  assert.strictEqual(firstPageParams.get("limit"), "50");
  assert.strictEqual(firstPageParams.get("cursor"), null);
})();

(function testQueuePaginationResetClearsCursorStackForFilterChanges() {
  const paginationState = {
    limit: 100,
    cursor: "cursor-3",
    cursorStack: ["", "cursor-1", "cursor-2"],
    nextCursor: "cursor-4",
    hasMore: true,
    pageStart: 201,
    pageEnd: 300,
  };

  resetQueuePaginationState(paginationState);
  assert.strictEqual(paginationState.limit, 100);
  assert.strictEqual(paginationState.cursor, null);
  assert.strictEqual(Array.isArray(paginationState.cursorStack), true);
  assert.strictEqual(paginationState.cursorStack.length, 0);
  assert.strictEqual(paginationState.nextCursor, null);
  assert.strictEqual(paginationState.hasMore, false);
  assert.strictEqual(paginationState.pageStart, 0);
  assert.strictEqual(paginationState.pageEnd, 0);
})();

(function testRenderQueueCardsRendersAllRows() {
  const rows = [
    createQueueRow(),
    createQueueRow({ id: "job-456", source: "manifests", sourceLabel: "Manifests" }),
  ];
  const cardsHtml = renderQueueCards(rows);
  assert(cardsHtml.includes("queue-card"));
  assert(cardsHtml.includes(baseQueueRow.id));
  assert(cardsHtml.includes("job-456"), "Cards should contain non-queue rows");
  queueFieldDefinitions.forEach((definition) => {
    assert(cardsHtml.includes(`<dt>${definition.label}</dt>`), `${definition.label} missing in card`);
  });
})();

(function testRenderQueueLayoutsCombinesTableAndCards() {
  const rows = [
    createQueueRow(),
    createOrchestratorRow(),
  ];
  const html = renderQueueLayouts(rows);
  assert(html.includes("queue-table-wrapper"));
  assert(html.includes("queue-card-list"));
})();

(function testRenderQueueLayoutsEmptyState() {
  const html = renderQueueLayouts([]);
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
  queueFieldDefinitions.push(definition);
  try {
    const cardsHtml = renderQueueCards([createQueueRow()]);
    const tableHtml = renderQueueTable([createQueueRow()]);
    assert(cardsHtml.includes(definition.label));
    assert(cardsHtml.includes("branch"));
    assert(tableHtml.includes('data-field="publishMode"'));
  } finally {
    queueFieldDefinitions.pop();
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
