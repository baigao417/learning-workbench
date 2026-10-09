const test = require("node:test");
const assert = require("node:assert/strict");

const { insertBlock, renderMarkdown } = require("../web/note-document.js");
const { classifyLines, userContent } = require("../web/note-document.js");

test("来源识别只读兼容旧语法，混合行不丢个人文字", () => {
  const source = "我的理解\n- [00:10](time:10)\n![截图](/screenshots/a)\n[回到](time:10) 我的感想\n## 早期学习记录\n旧文本\n### 细节\n旧细节\n## 新想法\n新文本\n## AI 作业\n机器内容";
  assert.deepEqual(classifyLines(source).map(item => item.kind), ["user", "automatic", "automatic", "mixed", "legacy", "legacy", "legacy", "legacy", "user", "user", "ai", "ai"]);
  assert.equal(userContent(source), "我的理解\n\n\n 我的感想\n## 新想法\n新文本");
  const mine = renderMarkdown(source, { onlyMine: true });
  assert.match(mine, /我的感想/);
  assert.doesNotMatch(mine, /data-note-time|<img|旧文本|机器内容/);
  const all = renderMarkdown(source, { provenance: true });
  assert.match(all, /data-note-origin="automatic"/);
  assert.match(all, /data-note-origin="legacy"/);
  assert.match(all, /data-note-origin="ai"/);
  assert.match(all, /data-note-time="10"/);
});

test("普通标题、用户引用和未知图片仍属于用户内容", () => {
  const source = "## 想法\n> 我的判断\n![图片](not-a-screenshot)";
  assert.equal(userContent(source), source);
  assert.ok(classifyLines(source).every(item => item.kind === "user"));
});

test("格式重载保留加粗斜体且转义不可信 HTML", () => {
  const html = renderMarkdown("**重点** 与 *想法* <img src=x onerror=alert(1)>");
  assert.ok(html.includes("<strong>重点</strong>"));
  assert.ok(html.includes("<em>想法</em>"));
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img"));
});

test("连续文档在光标位置插入时间块并保留前后内容", () => {
  const result = insertBlock("前文\n\n后文", 4, 4, "[00:28](time:28.234)");
  assert.equal(result.content, "前文\n\n[00:28](time:28.234)\n\n后文");
  assert.equal(result.cursor, 24);
});

test("快捷键按物理按键识别并提供编辑器内备用组合", () => {
  const { shortcutAction } = require("../web/note-document.js");
  assert.equal(shortcutAction({ altKey: true, code: "KeyA" }), "time");
  assert.equal(shortcutAction({ altKey: true, code: "KeyS" }), "screenshot");
  assert.equal(shortcutAction({ ctrlKey: true, code: "Enter" }), "time");
  assert.equal(shortcutAction({ ctrlKey: true, shiftKey: true, code: "Enter" }), "screenshot");
  assert.equal(shortcutAction({ altKey: true, code: "KeyA", repeat: true }), null);
});

test("阅读模式只渲染允许的截图和视频时间链接", () => {
  const html = renderMarkdown([
    "# 本课判断",
    "- [00:28](time:28.234) 回看",
    "![截图](/screenshots/abc123)",
    "![外部](https://example.com/image.png)",
    "<script>alert(1)</script>",
  ].join("\n"));
  assert.match(html, /data-note-time="28.234"/);
  assert.match(html, /src="\/screenshots\/abc123"/);
  assert.doesNotMatch(html, /src="https:\/\/example.com/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
});
