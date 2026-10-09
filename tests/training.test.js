const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const app = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const controller = app.slice(app.indexOf('$("training-button").onclick ='), app.indexOf('$("notes-all").onclick ='));

function harness() {
  const elements = new Map();
  const requests = [];
  const context = {
    state: { selected: { id: "lesson-a" }, training: [], trainingPending: false },
    $: id => {
      if (!elements.has(id)) elements.set(id, {});
      return elements.get(id);
    },
    json: (url, options) => {
      if (url === "/api/training") return Promise.resolve(context.state.training);
      return new Promise((resolve, reject) => requests.push({ url, body: JSON.parse(options.body), resolve, reject }));
    },
    flushNoteDocument: async () => {},
    setNotesCollapsed: () => {}, renderTraining: () => {}, renderSessionProgress: () => {},
    document: { querySelector: () => ({ open: false }) },
    compactViewport: { matches: false },
  };
  vm.createContext(context);
  vm.runInContext(controller, context);
  return { context, requests, click: () => context.$("training-button").onclick() };
}

test("今日作业等待态禁用重复点击，成功后恢复入口并保留课次归属", async () => {
  const h = harness();
  const pending = h.click();
  await new Promise(setImmediate);
  assert.equal(h.context.$("training-button").disabled, true);
  assert.match(h.context.$("training-status").textContent, /正在布置/);
  await h.click();
  assert.equal(h.requests.length, 1);
  assert.equal(JSON.stringify(h.requests[0].body), '{"lesson_id":"lesson-a"}');
  h.context.state.selected = { id: "lesson-b" };
  h.requests[0].resolve({ tasks: [{ lesson_id: "lesson-a", record_id: "one" }] });
  await pending;
  assert.equal(h.context.state.training[0].lesson_id, "lesson-a");
  assert.equal(h.context.$("training-button").disabled, false);
  assert.match(h.context.$("training-status").textContent, /上一课/);
});

test("今日作业失败后可重试，不插入固定模板", async () => {
  const h = harness();
  const pending = h.click();
  await new Promise(setImmediate);
  h.requests[0].reject(new Error("Codex CLI unavailable"));
  await pending;
  assert.match(h.context.$("training-status").textContent, /Codex CLI unavailable/);
  assert.equal(h.context.state.training.length, 0);
  assert.equal(h.context.$("training-button").disabled, false);
  const retry = h.click();
  await new Promise(setImmediate);
  assert.equal(h.requests.length, 2);
  h.requests[1].resolve({ tasks: [] });
  await retry;
  assert.doesNotMatch(controller, /saveRecord/);
});
