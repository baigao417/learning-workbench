(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.NoteDocument = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  }[char]));

  function renderText(value) {
    return escapeHtml(value)
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "<em>$1</em>");
  }

  function renderInline(value) {
    const source = String(value ?? "");
    const pattern = /!?\[([^\]]*)\]\(([^)]+)\)/g;
    let cursor = 0;
    let html = "";
    for (const match of source.matchAll(pattern)) {
      html += renderText(source.slice(cursor, match.index));
      const label = escapeHtml(match[1]);
      const target = match[2].trim();
      const isImage = match[0].startsWith("!");
      if (isImage && /^\/screenshots\/[a-zA-Z0-9_-]+$/.test(target)) {
        html += `<img class="note-document-image" src="${escapeHtml(target)}" alt="${label}" loading="lazy" />`;
      } else if (!isImage && /^time:\d+(?:\.\d+)?$/.test(target)) {
        const seconds = Number(target.slice(5));
        html += `<button class="note-time-link" type="button" data-note-time="${seconds}">${label || "回到视频"}</button>`;
      } else {
        html += escapeHtml(match[0]);
      }
      cursor = Number(match.index) + match[0].length;
    }
    return html + renderText(source.slice(cursor));
  }

  const automaticToken = /!\[[^\]]*\]\(\/screenshots\/[\w-]+\)|(?<!!)\[[^\]]*\]\(time:\d+(?:\.\d+)?\)/g;

  function classifyLines(value) {
    let section = null;
    return String(value ?? "").replace(/\r\n?/g, "\n").split("\n").map((line) => {
      const heading = line.match(/^(#{1,3})\s+(.+)$/);
      if (heading) {
        const level = heading[1].length;
        if (section && level <= section.level) section = null;
        if (heading[2] === "早期学习记录") section = { level, kind: "legacy" };
        else if (/^AI(?:\s|生成|简介|作业|笔记|内容)/.test(heading[2])) section = { level, kind: "ai" };
      }
      const mine = line.replace(automaticToken, "");
      const hasAutomatic = mine !== line;
      const text = mine.replace(/^\s*(?:#{1,3}\s+|[-*]\s+|\d+\.\s+|>\s?)/, "").trim();
      const kind = section?.kind || (hasAutomatic ? (text ? "mixed" : "automatic") : "user");
      return { line, kind, mine: section ? "" : (hasAutomatic && !text ? "" : mine) };
    });
  }

  function userContent(value) {
    return classifyLines(value).filter(item => !["legacy", "ai"].includes(item.kind)).map(item => item.mine).join("\n").trim();
  }

  function renderMarkdown(value, options = {}) {
    if (options.onlyMine) value = userContent(value);
    const lines = String(value ?? "").replace(/\r\n?/g, "\n").split("\n");
    if (!lines.some((line) => line.trim())) return '<p class="note-document-empty">还没有内容，直接开始写即可。</p>';
    const renderLine = (line) => {
      const heading = line.match(/^(#{1,3})\s+(.+)$/);
      if (heading) return `<h${heading[1].length}>${renderInline(heading[2])}</h${heading[1].length}>`;
      const unordered = line.match(/^\s*[-*]\s+(.+)$/);
      if (unordered) return `<div class="note-document-list"><span>•</span><p>${renderInline(unordered[1])}</p></div>`;
      const ordered = line.match(/^\s*(\d+)\.\s+(.+)$/);
      if (ordered) return `<div class="note-document-list"><span>${escapeHtml(ordered[1])}.</span><p>${renderInline(ordered[2])}</p></div>`;
      const quote = line.match(/^>\s?(.*)$/);
      if (quote) return `<blockquote>${renderInline(quote[1])}</blockquote>`;
      if (/^\s*---+\s*$/.test(line)) return "<hr />";
      if (!line.trim()) return '<div class="note-document-spacer" aria-hidden="true"></div>';
      return `<p>${renderInline(line)}</p>`;
    };
    const origins = classifyLines(value);
    return lines.map((line, index) => {
      const html = renderLine(line);
      return options.provenance && line.trim() ? html.replace(/^<(\w+)/, `<$1 data-note-origin="${origins[index].kind}"`) : html;
    }).join("");
  }

  function insertBlock(value, selectionStart, selectionEnd, block) {
    const content = String(value ?? "");
    const start = Math.max(0, Math.min(content.length, Number(selectionStart) || 0));
    const end = Math.max(start, Math.min(content.length, Number(selectionEnd) || start));
    const before = content.slice(0, start);
    const after = content.slice(end);
    const prefix = before && !before.endsWith("\n\n") ? (before.endsWith("\n") ? "\n" : "\n\n") : "";
    const suffix = after && !after.startsWith("\n\n") ? (after.startsWith("\n") ? "\n" : "\n\n") : "";
    const inserted = `${prefix}${String(block ?? "").trim()}${suffix}`;
    return {
      content: `${before}${inserted}${after}`,
      cursor: before.length + inserted.length - suffix.length,
    };
  }

  function shortcutAction(event) {
    if (!event || event.repeat || event.metaKey || event.getModifierState?.("AltGraph")) return null;
    const code = event.code || ({ a: "KeyA", c: "KeyC", l: "KeyL", m: "KeyM", n: "KeyN", s: "KeyS" }[String(event.key || "").toLowerCase()]);
    if (event.altKey && !event.ctrlKey && !event.shiftKey) {
      return ({ KeyA: "time", KeyC: "catalog", KeyL: "learn", KeyM: "map", KeyN: "notes", KeyS: "screenshot" })[code] || null;
    }
    if (event.ctrlKey && code === "Enter") return event.shiftKey ? "screenshot" : "time";
    return null;
  }

  return { escapeHtml, insertBlock, renderMarkdown, shortcutAction, classifyLines, userContent };
});
