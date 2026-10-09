(function () {
  function create(element, onChange, onTime) {
    let source = "", selection = null, composing = false, history = [], historyIndex = -1;
    const toolbar = document.getElementById("note-format-toolbar");
    const inline = (node) => {
      if (node.nodeType === Node.TEXT_NODE) return node.textContent.replace(/\u00a0/g, " ");
      if (node.nodeType !== Node.ELEMENT_NODE) return "";
      if (node.matches("[data-note-time]")) return `[${node.textContent}](time:${node.dataset.noteTime})`;
      if (node.tagName === "IMG") {
        const url = node.getAttribute("src") || "";
        return /^\/screenshots\/[\w-]+$/.test(url) ? `![${node.alt || "截图"}](${url})` : "";
      }
      const text = Array.from(node.childNodes, inline).join("");
      if (/^(STRONG|B)$/.test(node.tagName)) return text.split("\n").map(line => line ? `**${line}**` : "").join("\n");
      if (/^(EM|I)$/.test(node.tagName)) return text.split("\n").map(line => line ? `*${line}*` : "").join("\n");
      if (node.tagName === "BR") return "\n";
      return text;
    };
    const block = (node) => {
      if (node.nodeType === Node.TEXT_NODE) return node.textContent;
      if (node.nodeType !== Node.ELEMENT_NODE) return "";
      if (node.matches(".note-document-spacer") && !node.textContent && !node.querySelector("img")) return "";
      if (/^(DIV|P)$/.test(node.tagName) && Array.from(node.childNodes).every(child => child.nodeName === "BR")) return "";
      if (node.matches(".note-document-list")) {
        return `${node.firstElementChild.textContent === "•" ? "-" : node.firstElementChild.textContent} ${inline(node.lastElementChild)}`;
      }
      if (/^H[1-3]$/.test(node.tagName)) return inline(node).split("\n").map(line => `${"#".repeat(Number(node.tagName[1]))} ${line}`).join("\n");
      if (node.tagName === "BLOCKQUOTE") return inline(node).split("\n").map(line => `> ${line}`).join("\n");
      if (node.tagName === "HR") return "---";
      if (/^(UL|OL)$/.test(node.tagName)) return Array.from(node.children, (li, i) => `${node.tagName === "UL" ? "-" : `${i + (Number(node.getAttribute("start")) || 1)}.`} ${inline(li)}`).join("\n");
      if (/^(DIV|P)$/.test(node.tagName) && Array.from(node.children).some(n => /^(DIV|P|UL|OL|BLOCKQUOTE|H[1-3])$/.test(n.tagName))) {
        return Array.from(node.childNodes, block).join("\n");
      }
      return inline(node);
    };
    function decorate() {
      const nodes = Array.from(element.childNodes);
      const origins = NoteDocument.classifyLines(nodes.map(block).join("\n"));
      let index = 0;
      for (const node of nodes) {
        const count = block(node).split("\n").length;
        const kinds = origins.slice(index, index + count).filter(item => item.line.trim()).map(item => item.kind);
        if (node.nodeType === Node.ELEMENT_NODE) {
          node.dataset.noteOrigin = kinds.every(kind => kind === kinds[0]) ? (kinds[0] || "user") : "mixed";
        }
        index += count;
      }
    }
    function sync() {
      if (composing) return;
      source = Array.from(element.childNodes, block).join("\n");
      decorate();
      if (history[historyIndex]?.html !== element.innerHTML) {
        history = history.slice(0, historyIndex + 1);
        history.push({ html: element.innerHTML, source });
        if (history.length > 100) history.shift();
        historyIndex = history.length - 1;
      }
      onChange();
    }
    function remember() {
      const s = window.getSelection();
      if (s.rangeCount && element.contains(s.anchorNode) && element.contains(s.focusNode)) selection = s.getRangeAt(0).cloneRange();
      toolbar.hidden = element.hidden || element.contentEditable !== "true";
    }
    function restore() {
      element.focus();
      const s = window.getSelection();
      s.removeAllRanges();
      if (selection && element.contains(selection.startContainer)) s.addRange(selection);
      else {
        const range = document.createRange();
        range.selectNodeContents(element);
        range.collapse(false);
        s.addRange(range);
      }
    }
    function command(name, value = null) {
      if (element.contentEditable !== "true" || composing) return;
      if (name === "undo" || name === "redo") {
        const next = historyIndex + (name === "undo" ? -1 : 1);
        if (next < 0 || next >= history.length) return;
        historyIndex = next;
        element.innerHTML = history[next].html;
        source = history[next].source;
        decorate();
        selection = null;
        restore();
        onChange();
        return;
      }
      restore();
      document.execCommand(name, false, value);
      remember();
      sync();
    }
    function html(markdown) {
      return NoteDocument.renderMarkdown(markdown)
        .replace(/<div class="note-document-spacer" aria-hidden="true"><\/div>/g, "<p><br></p>")
        .replace(/<div class="note-document-list"><span>•<\/span><p>(.*?)<\/p><\/div>/g, "<ul><li>$1</li></ul>")
        .replace(/<div class="note-document-list"><span>(\d+)\.<\/span><p>(.*?)<\/p><\/div>/g, '<ol start="$1"><li>$2</li></ol>')
        .replace(/<\/ul><ul>/g, "")
        .replace(/<button class="note-time-link"/g, '<button contenteditable="false" tabindex="0" class="note-time-link"')
        .replace(/<img /g, '<img contenteditable="false" draggable="false" ');
    }
    Object.defineProperty(element, "value", {
      get: () => source,
      set: value => {
        source = String(value || "");
        element.innerHTML = source.trim() ? html(source) : "<p><br></p>";
        decorate();
        selection = null;
        history = [{ html: element.innerHTML, source }];
        historyIndex = 0;
      },
    });
    Object.defineProperty(element, "disabled", {
      get: () => element.contentEditable !== "true",
      set: disabled => {
        element.contentEditable = disabled ? "false" : "true";
        element.setAttribute("aria-disabled", String(disabled));
        toolbar.hidden = disabled;
      },
    });
    element.addEventListener("input", sync);
    element.addEventListener("compositionstart", () => { composing = true; });
    element.addEventListener("compositionend", () => { composing = false; sync(); });
    element.addEventListener("keyup", remember);
    element.addEventListener("mouseup", remember);
    element.addEventListener("keydown", event => {
      if (!(event.ctrlKey || event.metaKey) || composing) return;
      if (event.key.toLowerCase() === "z" || event.key.toLowerCase() === "y") {
        event.preventDefault();
        event.stopPropagation();
        command(event.shiftKey || event.key.toLowerCase() === "y" ? "redo" : "undo");
      }
    });
    document.addEventListener("selectionchange", remember);
    element.addEventListener("paste", event => {
      event.preventDefault();
      command("insertText", event.clipboardData.getData("text/plain"));
    });
    element.addEventListener("drop", event => event.preventDefault());
    element.addEventListener("click", event => {
      const time = event.target.closest("[data-note-time]");
      if (time) { event.preventDefault(); onTime(Number(time.dataset.noteTime)); }
    });
    toolbar.addEventListener("mousedown", event => { if (event.target.closest("button")) event.preventDefault(); });
    toolbar.querySelectorAll("[data-format]").forEach(button => {
      button.onclick = () => command(button.dataset.format, button.dataset.value || null);
    });
    document.getElementById("note-block-type").onchange = event => {
      command("formatBlock", event.target.value);
    };
    element.value = "";
    element.disabled = true;
    return {
      insert(markdown) { command("insertHTML", html(markdown) + "<p><br></p>"); },
      isComposing: () => composing,
    };
  }
  window.BlockEditor = { create };
})();
