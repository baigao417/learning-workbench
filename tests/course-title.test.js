const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const app = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "../web/styles.css"), "utf8");
const block = app.slice(app.indexOf("function courseTitle("), app.indexOf("function insertDocumentBlock("));

function harness() {
  const el = { textContent: "" };
  const context = { $: () => el, document: { title: "" } };
  vm.createContext(context);
  vm.runInContext(block, context);
  return { context, el };
}

test("页头课程名来自 manifest，而不是写死某门课", () => {
  assert.match(html, /<strong id="course-title">学习台<\/strong>/);
  const h = harness();
  h.context.renderCourseTitle({ source_root: "D:\\courses\\学习方法课\\" });
  assert.equal(h.el.textContent, "学习方法课学习台");
  h.context.renderCourseTitle({ course_title: "Python 入门", source_root: "D:/x/y" });
  assert.equal(h.el.textContent, "Python 入门学习台");
  h.context.renderCourseTitle({});
  assert.equal(h.el.textContent, "学习台");
  assert.equal(h.context.document.title, "一体化学习工作台");
});

test("手机布局的文档入口不再固定在顶部工具栏位置", () => {
  const mobile = css.slice(css.indexOf("@media (max-width: 900px)"));
  const rule = mobile.match(/\.notes-rail \{[^}]*\}/)[0];
  assert.match(rule, /bottom:/);
  assert.match(rule, /top: auto/);
  assert.doesNotMatch(rule, /top: 122px/);
});
