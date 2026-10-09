(function exposeTranscriptSync(root) {
  function findActiveTranscriptIndex(segments, currentTime) {
    if (!Array.isArray(segments) || segments.length === 0) return -1;
    let low = 0;
    let high = segments.length - 1;
    let active = 0;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      if (Number(segments[middle].start) <= Number(currentTime) + 0.05) {
        active = middle;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    return active;
  }

  root.findActiveTranscriptIndex = findActiveTranscriptIndex;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { findActiveTranscriptIndex };
  }
}(typeof window !== "undefined" ? window : globalThis));
