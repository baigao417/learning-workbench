const test = require("node:test");
const assert = require("node:assert/strict");

const { findActiveTranscriptIndex } = require("../web/transcript-sync.js");

const segments = [
  { start: 0, end: 3 },
  { start: 3, end: 8 },
  { start: 8, end: 12 },
];

test("逐字稿索引随播放时间自动推进", () => {
  assert.equal(findActiveTranscriptIndex(segments, 0), 0);
  assert.equal(findActiveTranscriptIndex(segments, 3.1), 1);
  assert.equal(findActiveTranscriptIndex(segments, 9), 2);
});

test("空逐字稿不产生高亮", () => {
  assert.equal(findActiveTranscriptIndex([], 5), -1);
});
