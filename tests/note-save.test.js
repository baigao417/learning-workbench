const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// Run the real browser controller with controllable HTTP latency and a minimal UI.
const app = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const saving = app.slice(app.indexOf("function scheduleNoteSave()"), app.indexOf("async function loadNoteDocument("));
const switching = app.slice(app.indexOf("async function selectLesson("), app.indexOf("function renderTranscript("));
const tick = () => new Promise(setImmediate);

function harness() {
  const pending = [];
  const elements = new Map();
  const state = {
    manifest: { lessons: [{ id: "a" }, { id: "b" }, { id: "c" }] },
    selected: { id: "a" },
    noteDocument: { lesson_id: "a", content: "initial", dirty: false, legacy_annotation_ids: ["old-note"] },
    noteSavePromise: Promise.resolve(), noteSaveTimer: null,
    noteCapturePending: false, lessonSelectionToken: 0,
  };
  let composing = false;
  const context = {
    state,
    $: id => {
      if (!elements.has(id)) elements.set(id, { value: "", textContent: "", disabled: false });
      return elements.get(id);
    },
    noteEditor: { isComposing: () => composing },
    json: (url, options) => new Promise((resolve, reject) => {
      pending.push({ url, body: JSON.parse(options.body), resolve, reject });
    }),
    setTimeout: () => 1, clearTimeout: () => {},
    setNoteSaveStatus: () => {}, renderSessionProgress: () => {},
    renderLessons: () => {}, renderTraining: () => {},
    loadTranscript: async () => {}, loadMedia: async () => {}, loadNoteDocument: async () => {},
    lessonTitle: lesson => lesson.id, formatTime: String,
    localStorage: { setItem() {} }, compactViewport: { matches: false },
    player: { readyState: 4 }, console: { error() {} },
  };
  vm.createContext(context);
  vm.runInContext(saving + "\n" + switching, context);
  return {
    state, pending, context,
    type(value) { context.$("note-input").value = value; context.scheduleNoteSave(); },
    compose(value) { composing = value; },
    async succeed() { assert.ok(pending.length); pending.shift().resolve({ updated_at: "saved" }); await tick(); },
  };
}

test("course switch waits for text entered during an outstanding save", async () => {
  const h = harness();
  h.type("first version");
  const switching = h.context.selectLesson("b");
  await tick();
  assert.equal(h.pending[0].body.content, "first version");
  h.type("newest version");
  await h.succeed();
  assert.equal(h.state.selected.id, "a");
  assert.equal(h.pending[0].body.content, "newest version");
  assert.deepEqual(Array.from(h.pending[0].body.legacy_annotation_ids), ["old-note"]);
  await h.succeed();
  await switching;
  assert.equal(h.state.selected.id, "b");
});

test("failed latest save cancels switching and retains unsaved text", async () => {
  const h = harness();
  h.type("first version");
  const switching = h.context.selectLesson("b");
  await tick();
  h.type("must not disappear");
  await h.succeed();
  assert.equal(h.state.selected.id, "a");
  h.pending.shift().reject(new Error("disk full"));
  await switching;
  assert.equal(h.state.selected.id, "a");
  assert.equal(h.state.noteDocument.content, "must not disappear");
  assert.equal(h.state.noteDocument.dirty, true);
  assert.equal(h.context.$("note-input").disabled, false);
});

test("composition beginning during save prevents the course transition", async () => {
  const h = harness();
  h.type("before composition");
  const switching = h.context.selectLesson("b");
  await tick();
  h.compose(true);
  await h.succeed();
  await switching;
  assert.equal(h.state.selected.id, "a");
});

test("an old response cannot mark an undo revision clean with a newer save queued", async () => {
  const h = harness();
  h.type("A");
  const first = h.context.saveNoteDocument();
  await tick();
  h.type("B");
  const second = h.context.saveNoteDocument();
  h.type("A");
  const switching = h.context.selectLesson("b");
  await h.succeed();
  assert.equal(h.state.noteDocument.dirty, true);
  assert.equal(h.state.selected.id, "a");
  assert.equal(h.pending[0].body.content, "B");
  await h.succeed();
  assert.equal(h.state.selected.id, "a");
  assert.equal(h.pending[0].body.content, "A");
  await h.succeed();
  await Promise.all([first, second, switching]);
  assert.equal(h.state.selected.id, "b");
});

test("rapid course clicks use only the last requested destination", async () => {
  const h = harness();
  h.type("keep me");
  const first = h.context.selectLesson("b");
  const last = h.context.selectLesson("c");
  await tick();
  await h.succeed();
  await h.succeed();
  await Promise.all([first, last]);
  assert.equal(h.state.selected.id, "c");
});
