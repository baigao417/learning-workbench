const state = {
  manifest: null,
  selected: null,
  training: [],
  feedback: [],
  transcriptSegments: [],
  activeSegmentIndex: -1,
  mediaRequestToken: 0,
  mindMap: [],
  mindMapZoom: 1,
  mindMapNeedsFit: false,
  workspaceView: "learn",
  noteDocument: { lesson_id: null, content: "", updated_at: null, dirty: false },
  noteSaveTimer: null,
  noteSavePromise: Promise.resolve(),
  noteCapturePending: false,
  lessonSelectionToken: 0,
  generationJobs: {},
  mediaLessonId: null,
  lastPlaybackSave: 0,
  feedbackFilter: "open",
  trainingPending: false,
  noteSourceFilter: "all",
};

const $ = (id) => document.getElementById(id);
const player = $("player");
const noteEditor = BlockEditor.create($("note-input"), scheduleNoteSave, seekWithoutPlaying);
const compactViewport = window.matchMedia("(max-width: 900px)");
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
const lessonTitle = (lesson) => lesson?.display_title || lesson?.title || lesson?.source_name || "未命名课程";

async function json(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = "";
    try { detail = (await response.json()).message || ""; } catch (_) { /* no JSON body */ }
    throw new Error(`${response.status} ${url}${detail ? `：${detail}` : ""}`);
  }
  return response.json();
}

function formatTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60).toString().padStart(2, "0");
  const s = (total % 60).toString().padStart(2, "0");
  return h ? `${h}:${m}:${s}` : `${m}:${s}`;
}

function storedBoolean(key, fallback) {
  const value = localStorage.getItem(key);
  return value == null ? fallback : value === "true";
}

function setOverlay(title, detail, showReload = false) {
  $("media-overlay-title").textContent = title;
  $("media-overlay-detail").textContent = detail;
  $("reload-media").hidden = !showReload;
  $("media-overlay").classList.remove("hidden");
}

function hideOverlay() {
  $("media-overlay").classList.add("hidden");
}

function setPlayerEnabled(enabled) {
  for (const id of ["play-toggle", "backward-button", "forward-button", "seek-bar", "playback-rate", "mute-toggle", "fullscreen-button"]) {
    $(id).disabled = !enabled;
  }
  $("screenshot-button").disabled = !enabled;
  $("document-screenshot-button").disabled = !enabled;
}

function updateCatalogToggle() {
  const mobileOpen = document.body.classList.contains("mobile-catalog-open");
  const desktopCollapsed = document.body.classList.contains("catalog-collapsed");
  const isOpen = compactViewport.matches ? mobileOpen : !desktopCollapsed;
  $("catalog-toggle").textContent = compactViewport.matches ? "☰" : (isOpen ? "◀" : "▶");
  $("catalog-toggle").title = `${isOpen ? "收起" : "展开"}课程目录 (Alt+C)`;
  $("catalog-toggle").setAttribute("aria-label", `${isOpen ? "收起" : "展开"}课程目录`);
  $("catalog-toggle").setAttribute("aria-expanded", String(isOpen));
  $("mobile-backdrop").hidden = !mobileOpen;
}

function toggleCatalog(force) {
  if (compactViewport.matches) {
    const open = force ?? !document.body.classList.contains("mobile-catalog-open");
    document.body.classList.toggle("mobile-catalog-open", open);
  } else {
    const collapsed = force == null ? !document.body.classList.contains("catalog-collapsed") : !force;
    document.body.classList.toggle("catalog-collapsed", collapsed);
    localStorage.setItem("workbench.catalogCollapsed", String(collapsed));
  }
  updateCatalogToggle();
}

function setNotesCollapsed(collapsed, persist = true) {
  document.body.classList.toggle("notes-collapsed", collapsed);
  $("notes-toggle").setAttribute("aria-expanded", String(!collapsed));
  if (persist) localStorage.setItem("workbench.notesCollapsed", String(collapsed));
}

function focusNoteComposer() {
  setNotesCollapsed(false);
  requestAnimationFrame(() => {
    $("note-input").focus();
    if (compactViewport.matches) $("notes-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function setWorkspaceView(name, persist = true) {
  state.workspaceView = name === "map" ? "map" : "learn";
  const showMap = state.workspaceView === "map";
  $("learn-view").hidden = showMap;
  $("map-view").hidden = !showMap;
  $("learn-view-tab").classList.toggle("active", !showMap);
  $("map-view-tab").classList.toggle("active", showMap);
  $("learn-view-tab").setAttribute("aria-selected", String(!showMap));
  $("map-view-tab").setAttribute("aria-selected", String(showMap));
  if (persist) localStorage.setItem("workbench.workspaceView", state.workspaceView);
  if (showMap && state.mindMap.length) {
    renderMindMap(state.mindMap);
    requestAnimationFrame(() => fitMindMap());
  }
}

function setNoteSaveStatus(message, tone = "") {
  const status = $("note-save-status");
  status.textContent = message;
  status.dataset.tone = tone;
}

function scheduleNoteSave() {
  if (!state.noteDocument.lesson_id) return;
  state.noteDocument.content = $("note-input").value;
  state.noteDocument.revision = (state.noteDocument.revision || 0) + 1;
  state.noteDocument.dirty = true;
  setNoteSaveStatus("正在记录…", "saving");
  clearTimeout(state.noteSaveTimer);
  state.noteSaveTimer = setTimeout(() => saveNoteDocument().catch(() => undefined), 650);
}

async function saveNoteDocument() {
  clearTimeout(state.noteSaveTimer);
  state.noteSaveTimer = null;
  if (!state.noteDocument.lesson_id || !state.noteDocument.dirty) return state.noteSavePromise;
  const document = state.noteDocument;
  const lessonId = document.lesson_id;
  const content = document.content;
  const revision = document.revision || 0;
  const legacyIds = document.legacy_annotation_ids || [];
  state.noteSavePromise = state.noteSavePromise.catch(() => undefined).then(async () => {
    try {
      const saved = await json(`/api/note-documents/${encodeURIComponent(lessonId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, preserve_screenshots: true, legacy_annotation_ids: legacyIds }),
      });
      if (state.noteDocument === document && (document.revision || 0) === revision) {
        state.noteDocument.updated_at = saved.updated_at;
        state.noteDocument.dirty = false;
        const removedCount = saved.removed_screenshot_ids?.length || 0;
        setNoteSaveStatus(removedCount ? `已保存，并删除 ${removedCount} 张移除的截图` : "已自动保存", "saved");
        renderSessionProgress();
      }
      return saved;
    } catch (error) {
      if (state.noteDocument === document) {
        state.noteDocument.dirty = true;
        setNoteSaveStatus("保存失败，稍后重试", "error");
      }
      console.error(error);
      throw error;
    }
  });
  return state.noteSavePromise;
}

async function flushNoteDocument() {
  do {
    if (state.noteCapturePending) throw new Error("截图正在保存，请稍后切换课次");
    if (noteEditor.isComposing()) throw new Error("请先完成当前输入");
    await saveNoteDocument();
  } while (state.noteDocument.dirty || state.noteCapturePending || noteEditor.isComposing());
}

async function loadNoteDocument(lessonId, selectionToken) {
  $("note-input").disabled = true;
  setNoteSaveStatus("正在读取本课文档…", "saving");
  const document = await json(`/api/note-documents/${encodeURIComponent(lessonId)}`);
  if (selectionToken !== state.lessonSelectionToken || state.selected?.id !== lessonId) return;
  state.noteDocument = { ...document, dirty: false };
  $("note-input").value = document.content || "";
  $("note-input").disabled = false;
  renderNoteSourceFilter();
  setNoteSaveStatus(document.updated_at ? "已读取本课文档" : "输入会自动保存", document.updated_at ? "saved" : "");
  renderSessionProgress();
}

function courseTitle(manifest) {
  const explicit = String(manifest?.course_title || "").trim();
  if (explicit) return explicit;
  const root = String(manifest?.source_root || "").replace(/[\\/]+$/, "");
  return root.split(/[\\/]/).pop() || "";
}

function renderCourseTitle(manifest) {
  const name = courseTitle(manifest);
  $("course-title").textContent = name ? `${name}学习台` : "学习台";
  document.title = name ? `${name} · 学习工作台` : "一体化学习工作台";
}

function insertDocumentBlock(block) {
  if (state.noteSourceFilter === "mine") setNoteSourceFilter("all");
  if (!state.noteDocument.lesson_id || $("note-input").disabled) return;
  noteEditor.insert(block);
}

function renderNoteSourceFilter() {
  const mine = state.noteSourceFilter === "mine";
  $("note-input").hidden = mine;
  $("note-mine-preview").hidden = !mine;
  $("note-format-toolbar").hidden = mine || $("note-input").disabled;
  $("notes-all").setAttribute("aria-pressed", String(!mine));
  $("notes-mine").setAttribute("aria-pressed", String(mine));
  if (mine) $("note-mine-preview").innerHTML = NoteDocument.renderMarkdown($("note-input").value, { onlyMine: true });
}

function setNoteSourceFilter(filter) {
  if (noteEditor.isComposing()) return;
  state.noteSourceFilter = filter;
  renderNoteSourceFilter();
}

function rememberGenerationJobs(jobs) {
  state.generationJobs = {};
  for (const job of jobs || []) {
    if (!state.generationJobs[job.lesson_id]) state.generationJobs[job.lesson_id] = job;
  }
}

function lessonMaterialLabel(lesson, job) {
  if (job?.status === "queued") return "等待 Codex CLI 启动";
  if (job?.status === "running") return "Codex 正在生成本课材料";
  if (job?.status === "failed") return "生成失败，点击重试";
  if (lesson.materials?.ready) return "逐字稿与思维导图已就绪";
  if (lesson.materials?.transcript_ready) return "逐字稿已就绪，待生成思维导图";
  return "等待生成课程材料";
}

async function pollGenerationJob(jobId, lessonId) {
  while (true) {
    await new Promise((resolve) => window.setTimeout(resolve, 1200));
    const job = await json(`/api/generation-jobs/${encodeURIComponent(jobId)}`);
    state.generationJobs[lessonId] = job;
    renderLessons();
    if (job.status === "queued" || job.status === "running") continue;
    if (job.status === "failed") {
      $("status").textContent = `本课材料生成失败：${job.message || "请检查 Codex CLI"}`;
      return;
    }
    state.manifest = await json("/api/manifest");
    renderLessons();
    $("status").textContent = "本课逐字稿与思维导图已生成";
    if (state.selected?.id === lessonId) await selectLesson(lessonId);
    return;
  }
}

async function startLessonGeneration(lesson) {
  const button = document.querySelector(`.lesson-generate[data-lesson-id="${CSS.escape(lesson.id)}"]`);
  if (button) button.disabled = true;
  $("status").textContent = `正在启动 Codex：${lessonTitle(lesson)}`;
  try {
    const job = await json("/api/generation-jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lesson_id: lesson.id, action: "materials" }),
    });
    state.generationJobs[lesson.id] = job;
    renderLessons();
    if (job.status === "succeeded") {
      state.manifest = await json("/api/manifest");
      renderLessons();
      $("status").textContent = "本课材料已经就绪";
      return;
    }
    await pollGenerationJob(job.job_id, lesson.id);
  } catch (error) {
    state.generationJobs[lesson.id] = {
      ...(state.generationJobs[lesson.id] || {}),
      lesson_id: lesson.id,
      status: "failed",
      message: error.message,
    };
    $("status").textContent = `无法启动材料生成：${error.message}`;
    renderLessons();
    console.error(error);
  }
}

function renderLessons() {
  const list = $("lesson-list");
  list.innerHTML = "";
  state.manifest.lessons.forEach((lesson, index) => {
    const row = document.createElement("div");
    row.className = "lesson-row";
    const button = document.createElement("button");
    button.className = `lesson-item ${state.selected?.id === lesson.id ? "selected" : ""}`;
    const job = state.generationJobs[lesson.id];
    button.innerHTML = `<span class="lesson-index">${String(index + 1).padStart(2, "0")}</span><span class="lesson-copy"><strong>${escapeHtml(lessonTitle(lesson))}</strong><span>${escapeHtml(lessonMaterialLabel(lesson, job))}</span></span>`;
    button.onclick = () => selectLesson(lesson.id);
    const generate = document.createElement("button");
    generate.className = "lesson-generate";
    generate.dataset.lessonId = lesson.id;
    generate.type = "button";
    generate.textContent = lesson.materials?.ready ? "完成" : (job?.status === "failed" ? "重试" : "生成");
    generate.title = job?.status === "failed" && job.message
      ? `上次失败：${job.message}`
      : "调用 Codex CLI 生成本课逐字稿与思维导图";
    generate.setAttribute("aria-label", `${generate.textContent}${lessonTitle(lesson)}的课程材料`);
    generate.disabled = Boolean(lesson.materials?.ready || ["queued", "running"].includes(job?.status));
    generate.onclick = () => startLessonGeneration(lesson);
    row.append(button, generate);
    list.appendChild(row);
  });
  $("lesson-count").textContent = `${state.manifest.lesson_count} 节`;
}

function playFrom(seconds) {
  if (Number.isFinite(Number(seconds))) player.currentTime = Number(seconds);
  player.play().catch(() => {
    $("status").textContent = "浏览器阻止了自动播放，请点播放按钮";
  });
}

function seekWithoutPlaying(seconds) {
  if (!Number.isFinite(Number(seconds))) return;
  player.currentTime = Number(seconds);
  updatePlayerTime();
}

function renderTraining() {
  const tasks = state.training.filter((item) => item.lesson_id === state.selected?.id);
  const list = $("training-list");
  list.classList.toggle("empty", tasks.length === 0);
  list.innerHTML = tasks.length ? "" : "还没有训练记录";
  for (const item of tasks) {
    const card = document.createElement("article");
    card.className = "training-card";
    card.innerHTML = `<strong>${escapeHtml(item.task)}</strong><span>${item.completed ? "已完成" : "待完成"} · ${new Date(item.created_at).toLocaleString("zh-CN")}</span>${item.completed ? `<p class="training-result">结果：${escapeHtml(item.result || "未填写")}</p>` : "<input class=\"training-result-input\" placeholder=\"写一句结果或证据（可选）\" /><button class=\"complete-training\" type=\"button\">完成训练</button>"}`;
    const metadata = document.createElement("div");
    metadata.className = "training-metadata";
    const ai = item.source?.kind === "ai_generated";
    const validType = ["回忆", "应用", "输出"].includes(item.type);
    metadata.innerHTML = `${validType ? `<span class="training-type">${escapeHtml(item.type)}</span>` : ""}${ai ? '<span class="training-source">AI 布置</span>' : ""}`;
    if (typeof item.anchor_seconds === "number" && Number.isFinite(item.anchor_seconds) && item.anchor_seconds >= 0) {
      const anchor = document.createElement("button");
      anchor.type = "button";
      anchor.className = "note-time-link";
      anchor.textContent = `${formatTime(item.anchor_seconds)} · ${item.anchor_label || "回到视频"}`;
      anchor.onclick = () => playFrom(item.anchor_seconds);
      metadata.appendChild(anchor);
    }
    card.prepend(metadata);
    const complete = card.querySelector(".complete-training");
    if (complete) complete.onclick = async () => {
      const result = card.querySelector(".training-result-input")?.value.trim() || "";
      const updated = await json(`/api/training/${item.record_id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ completed: true, result }) });
      state.training = state.training.map((entry) => entry.record_id === updated.record_id ? updated : entry);
      renderTraining();
      renderSessionProgress();
      $("status").textContent = "训练结果已保存";
    };
    list.appendChild(card);
  }
}

function renderFeedback() {
  const list = $("feedback-list");
  const openCount = state.feedback.filter((item) => item.status === "open").length;
  const resolvedCount = state.feedback.length - openCount;
  $("feedback-open-count").textContent = String(openCount);
  $("feedback-resolved-count").textContent = String(resolvedCount);
  const resolved = state.feedbackFilter === "resolved";
  $("feedback-open-tab").classList.toggle("active", !resolved);
  $("feedback-resolved-tab").classList.toggle("active", resolved);
  $("feedback-open-tab").setAttribute("aria-selected", String(!resolved));
  $("feedback-resolved-tab").setAttribute("aria-selected", String(resolved));
  const rows = state.feedback
    .filter((item) => resolved ? item.status !== "open" : item.status === "open")
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  list.classList.toggle("empty", rows.length === 0);
  list.innerHTML = rows.length ? "" : (resolved ? "还没有已完成意见" : "没有待处理意见");
  for (const item of rows) {
    const row = document.createElement("article");
    row.className = "feedback-item";
    row.innerHTML = `<div class="feedback-heading"><strong>${escapeHtml({ friction: "使用摩擦", idea: "优化想法", bug: "问题反馈" }[item.kind] || "意见")}</strong><div class="feedback-actions"><button class="feedback-status icon-button" type="button" title="${resolved ? "恢复为待处理" : "标记为已完成"}" aria-label="${resolved ? "恢复为待处理" : "标记为已完成"}">${resolved ? "↺" : "✓"}</button><button class="feedback-delete icon-button" type="button" title="删除这条意见" aria-label="删除这条意见">×</button></div></div><span>${escapeHtml(item.text)}</span><small>${resolved ? "已完成" : "待处理"} · ${item.lesson_id ? `关联：${escapeHtml(lessonTitle(state.manifest.lessons.find((lesson) => lesson.id === item.lesson_id)))}` : "工作台整体"}</small>`;
    row.querySelector(".feedback-status").onclick = async () => {
      try {
        const updated = await json(`/api/feedback/${encodeURIComponent(item.record_id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: resolved ? "open" : "resolved" }),
        });
        state.feedback = state.feedback.map((entry) => entry.record_id === updated.record_id ? updated : entry);
        renderFeedback();
        $("status").textContent = resolved ? "意见已恢复为待处理" : "意见已标记为完成";
      } catch (error) {
        $("status").textContent = error.message || "意见状态更新失败";
      }
    };
    row.querySelector(".feedback-delete").onclick = async () => {
      if (!window.confirm("确定删除这条本机意见吗？此操作无法撤销。")) return;
      try {
        await json(`/api/feedback/${encodeURIComponent(item.record_id)}`, { method: "DELETE" });
        state.feedback = state.feedback.filter((entry) => entry.record_id !== item.record_id);
        renderFeedback();
        $("status").textContent = "意见已从本机删除";
      } catch (error) {
        $("status").textContent = error.message || "意见删除失败";
      }
    };
    list.appendChild(row);
  }
}

function renderSessionProgress() {
  if (!state.selected) return;
  const tasks = state.training.filter((item) => item.lesson_id === state.selected.id);
  const done = tasks.filter((item) => item.completed).length;
  const documentState = state.noteDocument.updated_at ? "文档已保存" : "文档待开始";
  $("session-progress").textContent = `来源已就绪 · ${documentState} · 训练 ${done}/${tasks.length}`;
}

async function jumpToResult(lessonId, start) {
  if (state.selected?.id !== lessonId) await selectLesson(lessonId);
  if (start == null) focusNoteComposer(); else playFrom(start);
}

async function runSearch() {
  const query = $("search-input").value.trim();
  const list = $("search-results");
  if (!query) {
    list.className = "search-results empty";
    list.textContent = "输入关键词开始搜索";
    $("search-count").textContent = "";
    return;
  }
  try {
    const payload = await json(`/api/search?q=${encodeURIComponent(query)}`);
    const kindLabels = { transcript: "逐字稿", note_document: "学习文档" };
    $("search-count").textContent = `${payload.total} 条`;
    list.className = `search-results ${payload.results.length ? "" : "empty"}`;
    list.innerHTML = payload.results.length ? "" : "没有命中来源或备注";
    for (const item of payload.results) {
      const row = document.createElement("button");
      row.className = "search-result";
      row.innerHTML = `<strong>${escapeHtml(item.title)}</strong><span>${[kindLabels[item.kind] || "学习记录", item.start == null ? "" : formatTime(item.start), escapeHtml(String(item.text).replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1"))].filter(Boolean).join(" · ")}</span>`;
      row.onclick = () => jumpToResult(item.lesson_id, item.start);
      list.appendChild(row);
    }
    list.closest("details").open = true;
  } catch (error) {
    list.className = "search-results empty";
    list.textContent = "搜索失败，请检查本地工作区。";
    console.error(error);
  }
}

function savePlaybackPosition() {
  if (!state.mediaLessonId || player.readyState < 1) return;
  try {
    localStorage.setItem(`workbench.playback.${state.mediaLessonId}`, JSON.stringify({
      seconds: player.ended ? 0 : player.currentTime,
      rate: player.playbackRate,
      muted: player.muted,
    }));
  } catch (_) { /* Storage unavailable: playback remains usable. */ }
}

async function loadMedia(lesson) {
  savePlaybackPosition();
  state.mediaLessonId = null;
  const requestToken = ++state.mediaRequestToken;
  player.pause();
  player.removeAttribute("src");
  player.load();
  setPlayerEnabled(false);
  $("current-time").textContent = "00:00";
  $("note-time").textContent = "00:00";
  $("duration-time").textContent = formatTime(lesson.duration_seconds);
  $("seek-bar").value = "0";
  setOverlay("正在检查本地视频", "确认文件、编码和可播放状态……");
  $("media-status").textContent = "正在检查本地视频";

  try {
    let health = await json(`/api/media/${lesson.id}/health`);
    if (requestToken !== state.mediaRequestToken) return;
    if (!health.playable) {
      setOverlay("正在准备可播放版本", `原视频编码为 ${String(health.video_codec || "未知").toUpperCase()}，正在本机转换为浏览器兼容格式。首次需要稍等，之后直接使用缓存。`);
      $("media-status").textContent = "正在本机转换兼容版本，不会修改或上传原视频";
      health = await json(`/api/media/${lesson.id}/prepare`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (requestToken !== state.mediaRequestToken) return;
    }
    state.mediaLessonId = lesson.id;
    player.src = `/media/${lesson.id}?v=${Date.now()}`;
    player.load();
    $("media-status").textContent = health.proxy_ready ? "正在加载本机兼容版本" : "正在加载本地原视频";
  } catch (error) {
    if (requestToken !== state.mediaRequestToken) return;
    setOverlay("视频准备失败", "请确认原视频仍在本地、ffmpeg 可用，然后重新加载。", true);
    $("media-status").textContent = "视频不可用";
    $("status").textContent = "视频准备失败，已显示重新加载入口";
    console.error(error);
  }
}

async function selectLesson(id) {
  const lesson = state.manifest.lessons.find((item) => item.id === id);
  if (!lesson) return;
  const selectionToken = ++state.lessonSelectionToken;
  try {
    await flushNoteDocument();
  } catch (_) {
    $("status").textContent = "上一课文档尚未保存，请稍后再切换";
    return;
  }
  if (selectionToken !== state.lessonSelectionToken) return;
  state.selected = lesson;
  state.noteDocument = { lesson_id: lesson.id, content: "", updated_at: null, dirty: false };
  state.transcriptSegments = [];
  state.activeSegmentIndex = -1;
  localStorage.setItem("workbench.selectedLesson", lesson.id);
  $("lesson-title").textContent = lessonTitle(lesson);
  const duration = lesson.duration_seconds ? formatTime(lesson.duration_seconds) : "时长待探查";
  $("lesson-meta").textContent = `${lesson.source_name} · ${duration}`;
  $("anchor-button").disabled = false;
  $("focus-note-button").disabled = false;
  $("training-button").disabled = Boolean(state.trainingPending);
  $("document-time-button").disabled = false;
  $("document-screenshot-button").disabled = player.readyState < 2;
  $("note-input").value = "";
  $("note-input").disabled = true;
  $("transcript-status").textContent = lesson.transcript_status === "ready" ? "已就绪" : "待转录";
  $("transcript-preview").textContent = lesson.transcript_status === "ready" ? "逐字稿会随视频时间自动高亮，也可以点击任意一句回到对应画面。" : "这节课还没有本地逐字稿；可以先学习并记录时间锚点。";
  renderLessons();
  renderTraining();
  renderSessionProgress();
  if (compactViewport.matches) toggleCatalog(false);
  await Promise.all([loadTranscript(lesson), loadMedia(lesson), loadNoteDocument(lesson.id, selectionToken)]);
}

function renderTranscript(segments) {
  const body = $("transcript-body");
  state.transcriptSegments = segments;
  state.activeSegmentIndex = -1;
  body.classList.toggle("empty", segments.length === 0);
  body.innerHTML = segments.length ? "" : "还没有逐字稿分段";
  segments.forEach((segment, index) => {
    const row = document.createElement("button");
    row.className = "transcript-segment";
    row.dataset.index = String(index);
    row.dataset.start = String(segment.start || 0);
    row.dataset.end = String(segment.end || segment.start || 0);
    row.innerHTML = `<span>${formatTime(segment.start)}</span><span>${escapeHtml(segment.text)}</span>`;
    row.onclick = () => playFrom(segment.start);
    body.appendChild(row);
  });
}

function syncTranscript(currentTime, force = false) {
  const segments = state.transcriptSegments;
  if (!segments.length) return;
  const active = findActiveTranscriptIndex(segments, currentTime);
  if (!force && active === state.activeSegmentIndex) return;
  const previous = $("transcript-body").querySelector(".transcript-segment.active");
  if (previous) previous.classList.remove("active");
  const row = $("transcript-body").querySelector(`[data-index="${active}"]`);
  if (!row) return;
  row.classList.add("active");
  state.activeSegmentIndex = active;
  $("active-transcript-hint").textContent = `正在伴读：${formatTime(segments[active].start)}`;
  if ($("transcript-follow").checked) {
    const body = $("transcript-body");
    const targetTop = row.offsetTop - body.offsetTop - body.clientHeight / 2 + row.clientHeight / 2;
    body.scrollTo({ top: Math.max(0, targetTop), behavior: player.paused ? "auto" : "smooth" });
  }
}

function setMindMapZoom(value) {
  state.mindMapZoom = Math.max(0.45, Math.min(1.8, Number(value) || 1));
  $("mind-map-canvas").style.zoom = String(state.mindMapZoom);
}

function clearMindMapFocus() {
  const nodes = $("mind-map-nodes");
  nodes.classList.remove("has-focus");
  for (const node of nodes.querySelectorAll(".map-node")) node.classList.remove("focused", "related");
}

function centerMindMap() {
  const viewport = $("mind-map-viewport");
  const root = $("mind-map-nodes").querySelector(".map-node.root");
  if (!root) return;
  const x = root.offsetLeft * state.mindMapZoom - viewport.clientWidth / 2;
  const y = root.offsetTop * state.mindMapZoom - viewport.clientHeight / 2;
  viewport.scrollTo({ left: Math.max(0, x), top: Math.max(0, y), behavior: "smooth" });
}

function fitMindMap() {
  const viewport = $("mind-map-viewport");
  const canvas = $("mind-map-canvas");
  const width = Number(canvas.dataset.width || 820);
  const height = Number(canvas.dataset.height || 430);
  if (!viewport.clientWidth || !viewport.clientHeight) {
    state.mindMapNeedsFit = true;
    return;
  }
  clearMindMapFocus();
  setMindMapZoom(calculateMindMapFitScale(viewport.clientWidth, viewport.clientHeight, width, height));
  state.mindMapNeedsFit = false;
  requestAnimationFrame(centerMindMap);
}

function focusMindMapNode(button, time, branchIndex) {
  clearMindMapFocus();
  const nodes = $("mind-map-nodes");
  nodes.classList.add("has-focus");
  button.classList.add("focused");
  if (branchIndex != null) {
    for (const node of nodes.querySelectorAll(`[data-branch="${branchIndex}"]`)) node.classList.add("related");
  }
  seekWithoutPlaying(time);
  const viewport = $("mind-map-viewport");
  const left = button.offsetLeft * state.mindMapZoom - viewport.clientWidth / 2;
  const top = button.offsetTop * state.mindMapZoom - viewport.clientHeight / 2;
  viewport.scrollTo({ left: Math.max(0, left), top: Math.max(0, top), behavior: "smooth" });
  $("status").textContent = `已定位到 ${formatTime(time)}，返回学习现场即可继续播放`;
}

function resetMindMap(message = "暂无结构化思维导图") {
  state.mindMap = [];
  const canvas = $("mind-map-canvas");
  const nodes = $("mind-map-nodes");
  $("mind-map-lines").innerHTML = "";
  nodes.className = "mind-map-nodes empty";
  nodes.textContent = message;
  canvas.style.width = "820px";
  canvas.style.height = "430px";
  canvas.dataset.width = "820";
  canvas.dataset.height = "430";
  $("mind-map-status").textContent = message;
}

function addMindMapPath(svg, startX, startY, endX, endY, color, width = 2) {
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  const bend = Math.max(70, (endX - startX) * 0.48);
  path.setAttribute("d", `M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", color);
  path.setAttribute("stroke-width", String(width));
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("opacity", "0.88");
  svg.appendChild(path);
}

function createMindMapNode({ className, x, y, label, time, color, title, branchIndex }) {
  const button = document.createElement("button");
  button.className = `map-node ${className}`;
  button.style.left = `${x}px`;
  button.style.top = `${y}px`;
  if (color) button.style.setProperty("--branch-color", color);
  if (branchIndex != null) button.dataset.branch = String(branchIndex);
  button.innerHTML = `<strong>${escapeHtml(label)}</strong>${time == null ? "" : `<small>${formatTime(time)}</small>`}`;
  button.title = title || label;
  button.onclick = () => focusMindMapNode(button, Number(time) || 0, branchIndex);
  return button;
}

function renderMindMap(mindMap) {
  state.mindMap = Array.isArray(mindMap) ? mindMap : [];
  if (!state.mindMap.length) {
    resetMindMap("这节课还没有生成语义思维导图");
    return;
  }
  const colors = ["#57c7f5", "#9b8aff", "#78c95e", "#f6b85f", "#f07ea5", "#77a9ff", "#c490ff"];
  const { canvasWidth, canvasHeight, root, branches } = buildMindMapLayout(state.mindMap);
  const canvas = $("mind-map-canvas");
  const svg = $("mind-map-lines");
  const nodesLayer = $("mind-map-nodes");
  canvas.style.width = `${canvasWidth}px`;
  canvas.style.height = `${canvasHeight}px`;
  canvas.dataset.width = String(canvasWidth);
  canvas.dataset.height = String(canvasHeight);
  svg.setAttribute("viewBox", `0 0 ${canvasWidth} ${canvasHeight}`);
  svg.innerHTML = "";
  nodesLayer.className = "mind-map-nodes";
  nodesLayer.innerHTML = "";
  nodesLayer.appendChild(createMindMapNode({
    className: "root",
    x: root.x,
    y: root.y,
    label: lessonTitle(state.selected),
    time: 0,
    title: "课程中心主题",
  }));

  branches.forEach((branch, branchIndex) => {
    const { node } = branch;
    const color = colors[branchIndex % colors.length];
    addMindMapPath(svg, root.x + 87, root.y, branch.x - 83, branch.y, color, 3);
    nodesLayer.appendChild(createMindMapNode({
      className: "topic",
      x: branch.x,
      y: branch.y,
      label: node.label,
      time: node.start,
      color,
      title: node.summary || node.label,
      branchIndex,
    }));
    branch.children.forEach((childLayout) => {
      const child = childLayout.node;
      addMindMapPath(svg, branch.x + 83, branch.y, childLayout.x - 89, childLayout.y, color, 2);
      nodesLayer.appendChild(createMindMapNode({
        className: "child",
        x: childLayout.x,
        y: childLayout.y,
        label: child.label,
        time: child.start,
        color,
        title: child.text || child.label,
        branchIndex,
      }));
    });
  });
  $("mind-map-status").textContent = `${state.mindMap.length} 个主分支 · 点击节点聚焦并定位视频`;
  state.mindMapNeedsFit = true;
  if (state.workspaceView === "map") requestAnimationFrame(fitMindMap);
}

async function loadTranscript(lesson) {
  renderTranscript([]);
  resetMindMap("正在读取结构化思维导图……");
  if (lesson.transcript_status !== "ready") return;
  try {
    const [view, transcript] = await Promise.all([
      json(`/api/view/${lesson.id}`),
      json(`/api/transcript/${lesson.id}`),
    ]);
    if (state.selected?.id !== lesson.id) return;
    $("lesson-preview").textContent = view.introduction || view.preview || "暂无内容简介";
    $("lesson-intro-source").textContent = view.introduction_source ? `简介来源：本机 ${view.introduction_source.model}，可通过逐字稿和时间节点核对` : "当前为来源摘录，尚未生成内容摘要";
    renderMindMap(view.mind_map || []);
    renderTranscript(transcript.segments || []);
    syncTranscript(player.currentTime, true);
  } catch (error) {
    $("lesson-preview").textContent = "暂无内容简介";
    $("lesson-intro-source").textContent = "";
    resetMindMap("结构化思维导图读取失败");
    renderTranscript([]);
    $("transcript-body").textContent = "逐字稿读取失败，请检查本地转录产物。";
    console.error(error);
  }
}

async function saveRecord(endpoint, payload) {
  const saved = await json(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  state.training.push(saved);
  renderTraining();
  renderSessionProgress();
  return saved;
}

function updatePlayerTime() {
  $("current-time").textContent = formatTime(player.currentTime);
  $("note-time").textContent = formatTime(player.currentTime);
  $("duration-time").textContent = formatTime(player.duration || state.selected?.duration_seconds);
  if (Number.isFinite(player.duration) && player.duration > 0) {
    $("seek-bar").value = String(Math.round((player.currentTime / player.duration) * 1000));
  }
  syncTranscript(player.currentTime);
  if (Date.now() - state.lastPlaybackSave > 1500) {
    savePlaybackPosition();
    state.lastPlaybackSave = Date.now();
  }
}

function captureCanvasDataUrl() {
  if (!player.videoWidth || !player.videoHeight || player.readyState < 2) throw new Error("视频画面尚未就绪");
  const canvas = document.createElement("canvas");
  canvas.width = player.videoWidth;
  canvas.height = player.videoHeight;
  const context = canvas.getContext("2d");
  context.drawImage(player, 0, 0, canvas.width, canvas.height);
  return { imageData: canvas.toDataURL("image/png"), width: canvas.width, height: canvas.height };
}

async function captureScreenshot() {
  if (!state.selected) return;
  if (state.noteCapturePending) return;
  if (noteEditor.isComposing()) {
    $("status").textContent = "请先完成当前输入，再插入截图";
    return;
  }
  if ($("note-input").disabled) {
    $("status").textContent = "学习文档正在读取，请稍后截图";
    return;
  }
  try {
    state.noteCapturePending = true;
    $("screenshot-button").disabled = true;
    $("document-screenshot-button").disabled = true;
    $("status").textContent = "正在截取当前视频画面…";
    const { imageData, width, height } = captureCanvasDataUrl();
    const saved = await json("/api/screenshots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lesson_id: state.selected.id, time_seconds: player.currentTime, image_data: imageData, width, height }),
    });
    insertDocumentBlock(`![截图 ${formatTime(saved.time_seconds)}](${saved.image_url})\n\n[回到 ${formatTime(saved.time_seconds)}](time:${Number(saved.time_seconds).toFixed(3)})`);
    await saveNoteDocument();
    renderSessionProgress();
    setNotesCollapsed(false);
    $("status").textContent = "截图已保存 · 可在笔记中直接回看";
  } catch (error) {
    $("status").textContent = error.message || "截图失败，请确认视频画面已就绪";
    console.error(error);
  } finally {
    state.noteCapturePending = false;
    $("screenshot-button").disabled = !state.selected || player.readyState < 2;
    $("document-screenshot-button").disabled = !state.selected || player.readyState < 2;
  }
}

player.addEventListener("loadedmetadata", () => {
  setPlayerEnabled(true);
  $("duration-time").textContent = formatTime(player.duration);
  let restored = false;
  try {
    const saved = JSON.parse(localStorage.getItem(`workbench.playback.${state.mediaLessonId}`) || "null");
    if (saved) {
      const seconds = Number(saved.seconds);
      if (Number.isFinite(seconds) && seconds > 0 && seconds < player.duration - 1) {
        player.currentTime = seconds;
        restored = true;
      }
      player.playbackRate = [0.75, 1, 1.25, 1.5, 2].includes(saved.rate) ? saved.rate : 1;
      player.muted = saved.muted === true;
    }
  } catch (_) { /* Invalid stored state must not block the course. */ }
  $("playback-rate").value = String(player.playbackRate);
  $("mute-toggle").textContent = player.muted ? "取消静音" : "静音";
  $("media-status").textContent = restored ? `已恢复至 ${formatTime(player.currentTime)} · 点击播放继续` : "本地视频已就绪";
  updatePlayerTime();
  hideOverlay();
});
player.addEventListener("canplay", hideOverlay);
player.addEventListener("timeupdate", updatePlayerTime);
player.addEventListener("playing", () => { $("play-toggle").textContent = "暂停"; $("media-status").textContent = "正在播放本地视频"; });
player.addEventListener("pause", () => { $("play-toggle").textContent = "播放"; $("media-status").textContent = "已暂停"; savePlaybackPosition(); });
player.addEventListener("ratechange", savePlaybackPosition);
player.addEventListener("volumechange", savePlaybackPosition);
window.addEventListener("pagehide", savePlaybackPosition);
player.addEventListener("waiting", () => { $("media-status").textContent = "正在读取本地视频……"; });
player.addEventListener("ended", () => { $("play-toggle").textContent = "重播"; $("media-status").textContent = "本节视频已播放完"; });
player.addEventListener("error", () => {
  const messages = { 1: "播放已中止", 2: "本地视频读取失败", 3: "视频解码失败", 4: "当前格式无法播放" };
  setOverlay("视频播放失败", messages[player.error?.code] || "未知播放错误", true);
  $("media-status").textContent = "视频播放失败";
});
player.addEventListener("click", () => player.paused ? playFrom(player.currentTime) : player.pause());

$("play-toggle").onclick = () => player.paused ? playFrom(player.currentTime) : player.pause();
$("backward-button").onclick = () => { player.currentTime = Math.max(0, player.currentTime - 10); };
$("forward-button").onclick = () => { player.currentTime = Math.min(player.duration || Infinity, player.currentTime + 10); };
$("seek-bar").oninput = () => {
  if (Number.isFinite(player.duration)) player.currentTime = (Number($("seek-bar").value) / 1000) * player.duration;
};
$("playback-rate").onchange = () => { player.playbackRate = Number($("playback-rate").value); };
$("mute-toggle").onclick = () => {
  player.muted = !player.muted;
  $("mute-toggle").textContent = player.muted ? "取消静音" : "静音";
};
$("fullscreen-button").onclick = () => player.requestFullscreen?.();
$("reload-media").onclick = () => state.selected && loadMedia(state.selected);
$("transcript-follow").onchange = () => {
  localStorage.setItem("workbench.transcriptFollow", String($("transcript-follow").checked));
  $("active-transcript-hint").textContent = $("transcript-follow").checked ? "已开启自动跟随。" : "已暂停自动滚动；高亮仍会随播放更新。";
  if ($("transcript-follow").checked) syncTranscript(player.currentTime, true);
};

$("catalog-toggle").onclick = () => toggleCatalog();
$("mobile-backdrop").onclick = () => toggleCatalog(false);
$("notes-toggle").onclick = () => document.body.classList.contains("notes-collapsed") ? focusNoteComposer() : setNotesCollapsed(true);
$("notes-close").onclick = () => setNotesCollapsed(true);
$("notes-rail").onclick = focusNoteComposer;
$("focus-note-button").onclick = focusNoteComposer;
$("learn-view-tab").onclick = () => setWorkspaceView("learn");
$("map-view-tab").onclick = () => setWorkspaceView("map");


$("mind-map-zoom-out").onclick = () => setMindMapZoom(state.mindMapZoom - 0.12);
$("mind-map-zoom-in").onclick = () => setMindMapZoom(state.mindMapZoom + 0.12);
$("mind-map-reset").onclick = fitMindMap;
$("mind-map-center").onclick = () => { clearMindMapFocus(); centerMindMap(); };
$("mind-map-fullscreen").onclick = async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await $("map-view").requestFullscreen?.();
  } catch (_) { $("status").textContent = "暂时无法切换全屏，请重试"; }
};
document.addEventListener("fullscreenchange", () => {
  const fullscreen = document.fullscreenElement === $("map-view");
  const button = $("mind-map-fullscreen");
  button.textContent = fullscreen ? "退出全屏" : "⛶";
  button.setAttribute("aria-label", fullscreen ? "退出导图全屏" : "全屏浏览导图");
  button.title = fullscreen ? "退出全屏" : "全屏浏览导图";
  requestAnimationFrame(() => { if (state.workspaceView === "map") fitMindMap(); });
});

const panState = { active: false, x: 0, y: 0, left: 0, top: 0 };
$("mind-map-viewport").addEventListener("pointerdown", (event) => {
  if (event.target.closest(".map-node")) return;
  const viewport = $("mind-map-viewport");
  panState.active = true;
  panState.x = event.clientX;
  panState.y = event.clientY;
  panState.left = viewport.scrollLeft;
  panState.top = viewport.scrollTop;
  viewport.classList.add("panning");
  viewport.setPointerCapture(event.pointerId);
});
$("mind-map-viewport").addEventListener("pointermove", (event) => {
  if (!panState.active) return;
  const viewport = $("mind-map-viewport");
  viewport.scrollLeft = panState.left - (event.clientX - panState.x);
  viewport.scrollTop = panState.top - (event.clientY - panState.y);
});
function stopMindMapPan() {
  panState.active = false;
  $("mind-map-viewport").classList.remove("panning");
}
$("mind-map-viewport").addEventListener("pointerup", stopMindMapPan);
$("mind-map-viewport").addEventListener("pointercancel", stopMindMapPan);
$("mind-map-viewport").addEventListener("wheel", (event) => {
  if (!event.ctrlKey) return;
  event.preventDefault();
  setMindMapZoom(state.mindMapZoom + (event.deltaY < 0 ? 0.1 : -0.1));
}, { passive: false });

$("anchor-button").onclick = async () => {
  if (!state.selected) return;
  if ($("note-input").disabled) return;
  const time = player.currentTime;
  setNotesCollapsed(false);
  insertDocumentBlock(`[${formatTime(time)}](time:${Number(time).toFixed(3)})`);
  await saveNoteDocument();
  $("status").textContent = "当前时间已插入学习文档";
};
$("screenshot-button").onclick = captureScreenshot;
$("document-time-button").onclick = () => $("anchor-button").click();
$("document-screenshot-button").onclick = captureScreenshot;
$("training-button").onclick = async () => {
  if (!state.selected || state.trainingPending) return;
  const lessonId = state.selected.id;
  state.trainingPending = true;
  $("training-button").disabled = true;
  $("training-button").textContent = "生成中…";
  setNotesCollapsed(false);
  document.querySelector(".training-section").open = true;
  $("training-status").textContent = "AI 正在布置今日作业…";
  try {
    await flushNoteDocument();
    const generated = await json("/api/training/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lesson_id: lessonId }) });
    state.training.push(...generated.tasks);
    renderTraining();
    renderSessionProgress();
    $("training-status").textContent = state.selected?.id === lessonId ? "今日作业已布置" : "上一课的今日作业已布置";
    if (compactViewport.matches) requestAnimationFrame(() => document.querySelector(".training-section").scrollIntoView({ behavior: "smooth", block: "start" }));
  } catch (error) {
    $("training-status").textContent = `未生成：${error.message}`;
    // Refresh after conflicts or a lost response without creating another batch.
    try { state.training = await json("/api/training"); renderTraining(); } catch (_) { /* keep current records */ }
  } finally {
    state.trainingPending = false;
    $("training-button").disabled = !state.selected;
    $("training-button").textContent = "今日练习";
  }
};
$("notes-all").onclick = () => setNoteSourceFilter("all");
$("notes-mine").onclick = () => setNoteSourceFilter("mine");
$("search-button").onclick = runSearch;
$("search-input").addEventListener("keydown", (event) => { if (event.key === "Enter") runSearch(); });
$("feedback-open-tab").onclick = () => { state.feedbackFilter = "open"; renderFeedback(); };
$("feedback-resolved-tab").onclick = () => { state.feedbackFilter = "resolved"; renderFeedback(); };

$("save-feedback").onclick = async () => {
  const text = $("feedback-input").value.trim();
  if (!text) return;
  const saved = await json("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: $("feedback-kind").value, text, lesson_id: state.selected?.id || null }) });
  state.feedback.push(saved);
  $("feedback-input").value = "";
  renderFeedback();
  $("status").textContent = "意见已保存到本机";
};

document.addEventListener("keydown", (event) => {
  if (event.isComposing) return;
  const key = String(event.key || "").toLowerCase();
  if (event.ctrlKey && key === "s" && document.activeElement === $("note-input")) {
    event.preventDefault();
    saveNoteDocument().catch(() => undefined);
    return;
  }
  const action = NoteDocument.shortcutAction(event);
  if (!action) {
    if (event.key === "Escape" && state.workspaceView === "map") clearMindMapFocus();
    return;
  }
  if (event.ctrlKey && document.activeElement !== $("note-input")) return;
  event.preventDefault();
  if (action === "catalog") toggleCatalog();
  if (action === "notes") focusNoteComposer();
  if (action === "time" && state.selected) $("anchor-button").click();
  if (action === "screenshot" && state.selected) captureScreenshot();
  if (action === "map") setWorkspaceView("map");
  if (action === "learn") setWorkspaceView("learn");
});

compactViewport.addEventListener("change", () => {
  document.body.classList.remove("mobile-catalog-open");
  if (!compactViewport.matches) {
    document.body.classList.toggle("catalog-collapsed", storedBoolean("workbench.catalogCollapsed", false));
  }
  updateCatalogToggle();
});

window.addEventListener("resize", () => {
  if (state.workspaceView === "map" && state.mindMap.length && state.mindMapNeedsFit) fitMindMap();
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") saveNoteDocument().catch(() => undefined);
});

window.addEventListener("beforeunload", event => {
  if (!state.noteDocument.dirty && !noteEditor.isComposing()) return;
  event.preventDefault();
  event.returnValue = "";
});

async function boot() {
  try {
    document.body.classList.toggle("catalog-collapsed", !compactViewport.matches && storedBoolean("workbench.catalogCollapsed", false));
    setNotesCollapsed(storedBoolean("workbench.notesCollapsed", compactViewport.matches), false);
    $("transcript-follow").checked = storedBoolean("workbench.transcriptFollow", true);
    setWorkspaceView(localStorage.getItem("workbench.workspaceView") || "learn", false);
    updateCatalogToggle();
    const [manifest, training, feedback, generationPayload] = await Promise.all([
      json("/api/manifest"),
      json("/api/training"),
      json("/api/feedback"),
      json("/api/generation-jobs"),
    ]);
    state.manifest = manifest;
    renderCourseTitle(manifest);
    state.training = training;
    state.feedback = feedback;
    rememberGenerationJobs(generationPayload.jobs);
    renderLessons();
    renderFeedback();
    $("status").textContent = "本地工作区已就绪";
    const remembered = localStorage.getItem("workbench.selectedLesson");
    const initial = state.manifest.lessons.find((lesson) => lesson.id === remembered) || state.manifest.lessons[0];
    if (initial) await selectLesson(initial.id);
  } catch (error) {
    $("status").textContent = "请先运行课程登记并启动本地工作区";
    console.error(error);
  }
}

boot();
