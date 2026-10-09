const test = require("node:test");
const assert = require("node:assert/strict");

const { buildMindMapLayout, calculateMindMapFitScale } = require("../web/mind-map-layout.js");

test("XMind 布局生成中心主题、主分支和子节点三列", () => {
  const layout = buildMindMapLayout([
    { label: "分支一", children: [{ label: "节点一" }, { label: "节点二" }] },
    { label: "分支二", children: [{ label: "节点三" }] },
  ]);
  assert.equal(layout.root.x, 108);
  assert.equal(layout.branches.length, 2);
  assert.equal(layout.branches[0].x, 355);
  assert.equal(layout.branches[0].children[0].x, 650);
  assert.ok(layout.canvasHeight >= 430);
});

test("空导图仍保留可见画布", () => {
  const layout = buildMindMapLayout([]);
  assert.equal(layout.canvasHeight, 430);
  assert.equal(layout.branches.length, 0);
});

test("大导图按视口缩放并保留下限", () => {
  assert.equal(calculateMindMapFitScale(1000, 700, 770, 1200), 0.5533333333333333);
  assert.equal(calculateMindMapFitScale(320, 240, 1200, 2200), 0.45);
  assert.equal(calculateMindMapFitScale(1600, 1000, 770, 430), 1.25);
});
