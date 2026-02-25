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
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
  };
  windowStub.HTMLInputElement = context.HTMLInputElement;
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
} = helpers;

(function testQueueFieldDefinitionsProvideSingleSourceOfTruth() {
  const keys = queueFieldDefinitions.map((definition) => definition.key);
  const expectedKeys = [
    "queueName",
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
  assert(labels.includes("Queue"));
  assert(labels.includes("Outcome"));
  assert(labels.includes("Finished"));
  const rendered = renderQueueFieldValue(
    {
      queueName: "ops",
      runtimeMode: "codex",
      skillId: "auto",
      createdAt: "2026-02-23T12:00:00Z",
    },
    queueFieldDefinitions[0],
  );
  assert.strictEqual(rendered, "ops");
})();

(function testRenderQueueTableUsesFieldDefinitions() {
  const html = renderQueueTable([createQueueRow()]);
  assert(html.includes('data-field="queueName"'));
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
  assert(html.includes('data-field="queueName"'));
})();

(function testRenderQueueCardsRendersOnlyQueueRows() {
  const rows = [
    createQueueRow(),
    createQueueRow({ id: "job-456", source: "manifests", sourceLabel: "Manifests" }),
  ];
  const cardsHtml = renderQueueCards(rows);
  assert(cardsHtml.includes("queue-card"));
  assert(cardsHtml.includes(baseQueueRow.id));
  assert(!cardsHtml.includes("job-456"));
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
  assert(html.includes('data-sticky-table="true"'));
})();

(function testRenderQueueLayoutsEmptyState() {
  const html = renderQueueLayouts([]);
  assert.strictEqual(html.trim(), "<p class='small'>No rows available.</p>");
})();

(function testActivePageContentKeepsTablesForMixedSources() {
  const rows = createMixedRows();
  const html = renderActivePageContent(rows, ["queue-running"]);
  assert(html.includes('data-sticky-table="true"'));
  assert(html.includes("queue-card-list"));
  assert(html.includes(rows[0].id));
  const cardSectionMatch = html.match(/<ul class="queue-card-list"[^>]*>([\s\S]*?)<\/ul>/);
  assert(cardSectionMatch, "Expected queue-card-list markup");
  assert(!cardSectionMatch[1].includes(rows[2].id), "Non-queue rows must stay out of cards");
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
