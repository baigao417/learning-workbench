from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import mimetypes
import re
import shutil
import subprocess
import sys
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .language import simplify_payload, to_simplified_chinese
from .homework import execute_codex_homework, homework_day, homework_prompt, parse_homework
from .pipeline import load_manifest, probe_media, save_manifest, utc_now


GENERATION_ACTIONS = {"materials", "transcript", "mind_map"}
TRAINING_LOCK = threading.RLock()


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def lesson_material_status(state_dir: Path, lesson: dict) -> dict:
    transcript_ready = False
    transcript_path = lesson.get("transcript_path")
    if lesson.get("transcript_status") == "ready" and transcript_path:
        resolved = Path(transcript_path).resolve()
        transcript_ready = _is_within(resolved, (state_dir / "transcripts").resolve()) and resolved.is_file()

    mind_map_ready = False
    view_path = lesson.get("view_path")
    if view_path:
        resolved = Path(view_path).resolve()
        if _is_within(resolved, (state_dir / "views").resolve()) and resolved.is_file():
            try:
                view = json.loads(resolved.read_text(encoding="utf-8"))
                mind_map_ready = bool(view.get("mind_map")) and bool(view.get("mind_map_source"))
            except (OSError, json.JSONDecodeError):
                mind_map_ready = False
    return {
        "transcript_ready": transcript_ready,
        "mind_map_ready": mind_map_ready,
        "ready": transcript_ready and mind_map_ready,
    }


def _generation_prompt(project_root: Path, state_dir: Path, lesson: dict, action: str) -> str:
    status = lesson_material_status(state_dir, lesson)
    manifest_path = state_dir / "manifest.json"
    commands: list[str] = []
    if action in {"materials", "transcript"} and not status["transcript_ready"]:
        commands.append(
            subprocess.list2cmdline(
                [
                    sys.executable,
                    str(project_root / "scripts" / "transcribe.py"),
                    "--manifest",
                    str(manifest_path),
                    "--lesson-id",
                    lesson["id"],
                    "--model",
                    "large-v3",
                    "--language",
                    "zh",
                    "--device",
                    "cpu",
                    "--compute-type",
                    "int8",
                ]
            )
        )
    if action in {"materials", "mind_map"}:
        commands.append(
            subprocess.list2cmdline(
                [
                    sys.executable,
                    str(project_root / "scripts" / "generate_introductions.py"),
                    "--state",
                    str(state_dir),
                    "--lesson-id",
                    lesson["id"],
                    "--model",
                    "qwen3:4b",
                    "--force-mind-map",
                ]
            )
        )
    numbered = "\n".join(f"{index}. `{command}`" for index, command in enumerate(commands, start=1))
    return f"""你是 Learning Workbench 的本地材料生成执行器。只处理课次 `{lesson['id']}`（{lesson.get('source_name', '')}）。

严格边界：
- 只运行下面列出的既有命令，不修改项目代码、原课程媒体、用户笔记或其他课次。
- 不批处理，不删除或覆盖已就绪材料，不安装依赖，不提交 Git，不调用浏览器。
- 命令失败时立即停止，保留错误并明确返回失败。
- 完成后读取 `{manifest_path}` 和对应生成文件，确认目标课次产物真实存在；不要只根据命令退出码宣称成功。

按顺序执行：
{numbered}
"""


def execute_codex_generation(project_root: Path, state_dir: Path, lesson: dict, action: str) -> str:
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("未找到 Codex CLI，请先确认 codex 命令在 PATH 中可用")
    command = [
        codex,
        "exec",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
        "-C",
        str(project_root),
    ]
    if not _is_within(state_dir, project_root):
        command.extend(["--add-dir", str(state_dir)])
    command.append("-")
    result = subprocess.run(
        command,
        cwd=project_root,
        input=_generation_prompt(project_root, state_dir, lesson, action),
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        raise RuntimeError(output[-2000:] or f"Codex CLI 退出码 {result.returncode}")
    return output[-2000:] or "Codex CLI 已完成"


def _ensure_generation_state(server: ThreadingHTTPServer) -> None:
    if not hasattr(server, "generation_lock"):
        server.generation_lock = threading.Lock()  # type: ignore[attr-defined]
    if hasattr(server, "generation_jobs"):
        return
    path = server.state_dir / "generation-jobs.json"  # type: ignore[attr-defined]
    try:
        jobs = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, json.JSONDecodeError):
        jobs = []
    for job in jobs:
        if job.get("status") in {"queued", "running"}:
            job["status"] = "failed"
            job["message"] = "服务曾中断此任务，请点击重试"
            job["updated_at"] = utc_now()
    server.generation_jobs = {job["job_id"]: job for job in jobs if job.get("job_id")}  # type: ignore[attr-defined]


def _save_generation_jobs(server: ThreadingHTTPServer) -> None:
    path = server.state_dir / "generation-jobs.json"  # type: ignore[attr-defined]
    path.parent.mkdir(parents=True, exist_ok=True)
    jobs = list(server.generation_jobs.values())  # type: ignore[attr-defined]
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _update_generation_job(server: ThreadingHTTPServer, job_id: str, **changes: object) -> dict:
    _ensure_generation_state(server)
    with server.generation_lock:  # type: ignore[attr-defined]
        job = server.generation_jobs[job_id]  # type: ignore[attr-defined]
        job.update(changes)
        job["updated_at"] = utc_now()
        _save_generation_jobs(server)
        return dict(job)


def _run_generation_job(server: ThreadingHTTPServer, job_id: str) -> None:
    job = _update_generation_job(server, job_id, status="running", message="Codex CLI 正在生成本课材料")
    try:
        manifest_path = server.state_dir / "manifest.json"  # type: ignore[attr-defined]
        manifest = load_manifest(manifest_path)
        lesson = next(item for item in manifest.get("lessons", []) if item.get("id") == job["lesson_id"])
        message = execute_codex_generation(
            server.project_root,  # type: ignore[attr-defined]
            server.state_dir,  # type: ignore[attr-defined]
            lesson,
            job["action"],
        )
        refreshed = load_manifest(manifest_path)
        lesson = next(item for item in refreshed.get("lessons", []) if item.get("id") == job["lesson_id"])
        status = lesson_material_status(server.state_dir, lesson)  # type: ignore[attr-defined]
        expected_ready = {
            "transcript": status["transcript_ready"],
            "mind_map": status["mind_map_ready"],
            "materials": status["ready"],
        }[job["action"]]
        if not expected_ready:
            raise RuntimeError("Codex CLI 已结束，但目标生成文件未通过本地核验")
        _update_generation_job(server, job_id, status="succeeded", message=message)
    except Exception as exc:  # background worker must always leave a readable terminal state
        _update_generation_job(server, job_id, status="failed", message=str(exc)[-2000:])


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "LearningWorkbench/0.1"

    def _send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    @property
    def state_dir(self) -> Path:
        return self.server.state_dir  # type: ignore[attr-defined]

    @property
    def manifest_path(self) -> Path:
        return self.state_dir / "manifest.json"

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/manifest":
            if not self.manifest_path.exists():
                return self._send_json({"error": "manifest_not_found"}, HTTPStatus.NOT_FOUND)
            manifest = load_manifest(self.manifest_path)
            for lesson in manifest.get("lessons", []):
                lesson["materials"] = lesson_material_status(self.state_dir, lesson)
            return self._send_json(manifest)
        if parsed.path == "/api/generation-jobs":
            return self._list_generation_jobs()
        if parsed.path.startswith("/api/generation-jobs/"):
            job_id = unquote(parsed.path.removeprefix("/api/generation-jobs/"))
            return self._get_generation_job(job_id)
        if parsed.path == "/api/annotations":
            return self._send_json(self._load_list("annotations.json"))
        if parsed.path == "/api/training":
            with TRAINING_LOCK:
                return self._send_json(self._load_list("training.json"))
        if parsed.path == "/api/feedback":
            return self._send_json(self._load_list("feedback.json"))
        if parsed.path == "/api/screenshots":
            return self._send_json(self._load_list("screenshots.json"))
        if parsed.path.startswith("/api/note-documents/"):
            lesson_id = unquote(parsed.path.removeprefix("/api/note-documents/"))
            return self._get_note_document(lesson_id)
        if parsed.path == "/api/search":
            query = parse_qs(parsed.query).get("q", [""])[0].strip()
            return self._search(query)
        if parsed.path.startswith("/api/media/") and parsed.path.endswith("/health"):
            requested_id = unquote(parsed.path.removeprefix("/api/media/").removesuffix("/health"))
            return self._media_health(requested_id)
        if parsed.path.startswith("/api/transcript/"):
            return self._serve_transcript(unquote(parsed.path.removeprefix("/api/transcript/")))
        if parsed.path.startswith("/api/view/"):
            return self._serve_view(unquote(parsed.path.removeprefix("/api/view/")))
        if parsed.path.startswith("/media/"):
            return self._serve_media(unquote(parsed.path.removeprefix("/media/")))
        if parsed.path.startswith("/screenshots/"):
            return self._serve_screenshot(unquote(parsed.path.removeprefix("/screenshots/")))
        return self._serve_static(parsed.path)

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/media/"):
            return self._serve_media(unquote(parsed.path.removeprefix("/media/")))
        return self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._read_json_body()
        except (ValueError, json.JSONDecodeError):
            return self._send_json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
        if not isinstance(payload, dict):
            return self._send_json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
        if parsed.path == "/api/annotations":
            return self._append_record("annotations.json", payload)
        if parsed.path == "/api/training":
            with TRAINING_LOCK:
                return self._append_record("training.json", payload)
        if parsed.path == "/api/training/generate":
            return self._generate_training(payload)
        if parsed.path == "/api/feedback":
            payload.setdefault("status", "open")
            return self._append_record("feedback.json", payload)
        if parsed.path == "/api/screenshots":
            return self._create_screenshot(payload)
        if parsed.path == "/api/generation-jobs":
            return self._start_generation_job(payload)
        if parsed.path.startswith("/api/media/") and parsed.path.endswith("/prepare"):
            requested_id = unquote(parsed.path.removeprefix("/api/media/").removesuffix("/prepare"))
            return self._prepare_media(requested_id)
        return self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/training/") or parsed.path.startswith("/api/feedback/"):
            try:
                payload = self._read_json_body()
            except (ValueError, json.JSONDecodeError):
                return self._send_json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
            if not isinstance(payload, dict):
                return self._send_json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
            if parsed.path.startswith("/api/training/"):
                record_id = unquote(parsed.path.removeprefix("/api/training/"))
                with TRAINING_LOCK:
                    return self._update_record("training.json", record_id, payload)
            record_id = unquote(parsed.path.removeprefix("/api/feedback/"))
            return self._update_feedback(record_id, payload)
        return self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/feedback/"):
            record_id = unquote(parsed.path.removeprefix("/api/feedback/"))
            return self._delete_record("feedback.json", record_id)
        return self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._read_json_body()
        except (ValueError, json.JSONDecodeError):
            return self._send_json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
        if not isinstance(payload, dict):
            return self._send_json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
        if parsed.path.startswith("/api/note-documents/"):
            lesson_id = unquote(parsed.path.removeprefix("/api/note-documents/"))
            return self._upsert_note_document(lesson_id, payload)
        return self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def _load_list(self, name: str) -> list[dict]:
        path = self.state_dir / name
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_list(self, name: str, records: list[dict]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / name).write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _list_generation_jobs(self) -> None:
        _ensure_generation_state(self.server)  # type: ignore[arg-type]
        with self.server.generation_lock:  # type: ignore[attr-defined]
            jobs = list(self.server.generation_jobs.values())  # type: ignore[attr-defined]
        jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return self._send_json({"jobs": jobs})

    def _get_generation_job(self, job_id: str) -> None:
        _ensure_generation_state(self.server)  # type: ignore[arg-type]
        with self.server.generation_lock:  # type: ignore[attr-defined]
            job = self.server.generation_jobs.get(job_id)  # type: ignore[attr-defined]
        if not job:
            return self._send_json({"error": "generation_job_not_found"}, HTTPStatus.NOT_FOUND)
        return self._send_json(job)

    def _start_generation_job(self, payload: dict) -> None:
        lesson_id = str(payload.get("lesson_id", "")).strip()
        action = str(payload.get("action", "materials")).strip()
        lesson = self._find_lesson(lesson_id)
        if not lesson:
            return self._send_json({"error": "lesson_not_found"}, HTTPStatus.NOT_FOUND)
        if action not in GENERATION_ACTIONS:
            return self._send_json({"error": "generation_action_invalid"}, HTTPStatus.BAD_REQUEST)
        materials = lesson_material_status(self.state_dir, lesson)
        if action == "mind_map" and not materials["transcript_ready"]:
            return self._send_json(
                {"error": "transcript_required", "message": "请先生成逐字稿，或直接生成本课全部材料"},
                HTTPStatus.CONFLICT,
            )
        already_ready = {
            "transcript": materials["transcript_ready"],
            "mind_map": materials["mind_map_ready"],
            "materials": materials["ready"],
        }[action]

        _ensure_generation_state(self.server)  # type: ignore[arg-type]
        with self.server.generation_lock:  # type: ignore[attr-defined]
            active = next(
                (
                    item
                    for item in self.server.generation_jobs.values()  # type: ignore[attr-defined]
                    if item.get("lesson_id") == lesson_id and item.get("status") in {"queued", "running"}
                ),
                None,
            )
            if active:
                return self._send_json(active)
            job_id = uuid.uuid4().hex[:12]
            job = {
                "job_id": job_id,
                "lesson_id": lesson_id,
                "action": action,
                "status": "succeeded" if already_ready else "queued",
                "message": "本课材料已就绪" if already_ready else "等待 Codex CLI 启动",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "reused": already_ready,
            }
            self.server.generation_jobs[job_id] = job  # type: ignore[attr-defined]
            _save_generation_jobs(self.server)  # type: ignore[arg-type]
        if already_ready:
            return self._send_json(job)
        thread = threading.Thread(
            target=_run_generation_job,
            args=(self.server, job_id),
            daemon=True,
            name=f"lesson-generation-{job_id}",
        )
        thread.start()
        return self._send_json(job, HTTPStatus.ACCEPTED)

    def _generate_training(self, payload: dict) -> None:
        lesson_id = payload.get("lesson_id")
        if set(payload) != {"lesson_id"} or not isinstance(lesson_id, str) or not lesson_id.strip():
            return self._send_json({"error": "invalid_training_request", "message": "只接受 lesson_id"}, HTTPStatus.BAD_REQUEST)
        lesson = self._find_lesson(lesson_id)
        if not lesson:
            return self._send_json({"error": "lesson_not_found", "message": "课次不存在"}, HTTPStatus.NOT_FOUND)

        def generated_today() -> bool:
            for record in self._load_list("training.json"):
                source = record.get("source")
                if record.get("lesson_id") != lesson_id or not isinstance(source, dict) or source.get("kind") != "ai_generated":
                    continue
                try:
                    if homework_day(record["created_at"]) == homework_day():
                        return True
                except (KeyError, TypeError, ValueError):
                    continue
            return False

        with TRAINING_LOCK:
            if not hasattr(self.server, "training_active"):
                self.server.training_active = set()  # type: ignore[attr-defined]
            active = self.server.training_active  # type: ignore[attr-defined]
            if lesson_id in active or generated_today():
                return self._send_json({"error": "training_exists", "message": "本课今日已有作业或正在生成，请查看训练记录"}, HTTPStatus.CONFLICT)
            active.add(lesson_id)
        try:
            transcript_path = Path(lesson.get("transcript_path") or "").resolve()
            if not _is_within(transcript_path, (self.state_dir / "transcripts").resolve()) or not transcript_path.is_file():
                raise ValueError("本课缺少逐字稿，请先生成本课材料")
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
            view = {}
            if lesson.get("view_path"):
                view_path = Path(lesson["view_path"]).resolve()
                if _is_within(view_path, (self.state_dir / "views").resolve()) and view_path.is_file():
                    view = json.loads(view_path.read_text(encoding="utf-8"))
            prompt = homework_prompt(lesson, transcript, view, self._note_document(lesson_id)["content"])
            output = execute_codex_homework(self.server.project_root, prompt)  # type: ignore[attr-defined]
            tasks = parse_homework(output, transcript)
            created_at, batch_id = utc_now(), uuid.uuid4().hex[:12]
            records = [{**task, "lesson_id": lesson_id, "completed": False, "record_id": uuid.uuid4().hex[:12],
                        "created_at": created_at, "batch_id": batch_id,
                        "source": {"kind": "ai_generated", "backend": "codex_cli", "created_at": created_at}}
                       for task in tasks]
            with TRAINING_LOCK:
                if generated_today():
                    return self._send_json({"error": "training_exists", "message": "本课今日已有作业"}, HTTPStatus.CONFLICT)
                existing = self._load_list("training.json")
                target = self.state_dir / "training.json"
                temporary = target.with_suffix(".tmp")
                temporary.write_text(json.dumps(existing + records, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(target)
            return self._send_json({"tasks": records, "batch_id": batch_id}, HTTPStatus.CREATED)
        except ValueError as exc:
            return self._send_json({"error": "invalid_homework", "message": str(exc)}, HTTPStatus.UNPROCESSABLE_ENTITY)
        except (RuntimeError, OSError) as exc:
            return self._send_json({"error": "homework_generation_failed", "message": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
        finally:
            with TRAINING_LOCK:
                active.discard(lesson_id)

    def _search(self, query: str) -> None:
        if not query:
            return self._send_json({"query": "", "results": []})
        if not self.manifest_path.exists():
            return self._send_json({"error": "manifest_not_found"}, HTTPStatus.NOT_FOUND)
        manifest = load_manifest(self.manifest_path)
        needle = query.casefold()
        simplified_needle = to_simplified_chinese(query).casefold()
        lessons = {lesson["id"]: lesson for lesson in manifest.get("lessons", [])}
        results: list[dict] = []

        def add_result(lesson_id: str, kind: str, text: str, start: float | None = None) -> None:
            lesson = lessons.get(lesson_id)
            simplified_text = to_simplified_chinese(text)
            if not lesson or not text or (
                needle not in text.casefold() and simplified_needle not in simplified_text.casefold()
            ):
                return
            results.append({
                "lesson_id": lesson_id,
                "title": to_simplified_chinese(
                    lesson.get("display_title") or lesson.get("title") or lesson.get("source_name") or lesson_id
                ),
                "kind": kind,
                "start": start,
                "text": simplified_text.strip(),
            })

        documents = self._load_list("note-documents.json")
        annotations = self._load_list("annotations.json")
        for lesson_id in lessons:
            document = self._note_document(lesson_id, documents, annotations)
            add_result(lesson_id, "note_document", document["content"])
        for lesson in manifest.get("lessons", []):
            if lesson.get("transcript_status") != "ready":
                continue
            transcript_path = lesson.get("transcript_path")
            if transcript_path and Path(transcript_path).is_file():
                transcript = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
                for segment in transcript.get("segments", []):
                    add_result(lesson["id"], "transcript", segment.get("text", ""), segment.get("start"))
        return self._send_json({"query": query, "results": results, "total": len(results)})

    def _note_document(
        self, lesson_id: str, documents: list[dict] | None = None, annotations: list[dict] | None = None
    ) -> dict:
        """Compose a compatibility view without modifying either source file."""
        if documents is None:
            documents = self._load_list("note-documents.json")
        if annotations is None:
            annotations = self._load_list("annotations.json")
        document = dict(next(
            (item for item in documents if item.get("lesson_id") == lesson_id),
            {"lesson_id": lesson_id, "content": "", "updated_at": None},
        ))
        imported = list(document.get("legacy_annotation_ids") or [])
        seen = set(imported)
        additions = []
        for record in annotations:
            if record.get("lesson_id") != lesson_id or record.get("type") not in {"note", "anchor", "friction"}:
                continue
            record_id = str(record.get("record_id") or hashlib.sha256(
                json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest())
            if record_id in seen:
                continue
            seen.add(record_id)
            imported.append(record_id)
            label = {"note": "笔记", "anchor": "时间锚点", "friction": "学习摩擦"}[record["type"]]
            time_link = ""
            try:
                seconds = float(record["time_seconds"])
                if math.isfinite(seconds) and seconds >= 0:
                    total = int(seconds)
                    minutes, second = divmod(total, 60)
                    hour, minute = divmod(minutes, 60)
                    clock = f"{hour}:{minute:02d}:{second:02d}" if hour else f"{minute:02d}:{second:02d}"
                    time_link = f" [{clock}](time:{seconds:g})"
            except (KeyError, TypeError, ValueError):
                pass
            timestamp = f"\n记录于 {record['created_at']}" if record.get("created_at") else ""
            additions.append(f"**{label}**{time_link}\n{record.get('text') or ''}{timestamp}")
        if additions:
            document["content"] = "\n\n".join(filter(None, [
                document.get("content", ""), "## 早期学习记录", *additions,
            ]))
        document["legacy_annotation_ids"] = imported
        return document

    def _get_note_document(self, lesson_id: str) -> None:
        if not lesson_id or not self._find_lesson(lesson_id):
            return self._send_json({"error": "lesson_not_found"}, HTTPStatus.NOT_FOUND)
        # Reading a document must not remove assets that undo may restore.
        return self._send_json(self._note_document(lesson_id))

    def _upsert_note_document(self, lesson_id: str, payload: dict) -> None:
        if not lesson_id or not self._find_lesson(lesson_id):
            return self._send_json({"error": "lesson_not_found"}, HTTPStatus.NOT_FOUND)
        content = payload.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > 2 * 1024 * 1024:
            return self._send_json({"error": "invalid_note_document"}, HTTPStatus.BAD_REQUEST)
        legacy_ids = payload.get("legacy_annotation_ids")
        if legacy_ids is not None and (
            not isinstance(legacy_ids, list) or any(not isinstance(item, str) for item in legacy_ids)
        ):
            return self._send_json({"error": "invalid_legacy_annotation_ids"}, HTTPStatus.BAD_REQUEST)

        records = self._load_list("note-documents.json")
        document = next((item for item in records if item.get("lesson_id") == lesson_id), None)
        created = document is None
        if created:
            document = {"lesson_id": lesson_id}
            records.append(document)
        document["content"] = content
        document["updated_at"] = utc_now()
        if legacy_ids is not None:
            document["legacy_annotation_ids"] = list(dict.fromkeys(
                [*(document.get("legacy_annotation_ids") or []), *legacy_ids]
            ))

        self.state_dir.mkdir(parents=True, exist_ok=True)
        target = self.state_dir / "note-documents.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        response = dict(document)
        response["removed_screenshot_ids"] = [] if payload.get("preserve_screenshots") is True else self._prune_note_screenshots(lesson_id, content)
        return self._send_json(response, HTTPStatus.CREATED if created else HTTPStatus.OK)

    def _prune_note_screenshots(self, lesson_id: str, content: str) -> list[str]:
        referenced_ids = set(re.findall(r"/screenshots/([a-zA-Z0-9_-]+)", content))
        records = self._load_list("screenshots.json")
        kept: list[dict] = []
        removed: list[str] = []
        screenshot_root = (self.state_dir / "screenshots").resolve()

        for record in records:
            record_id = str(record.get("record_id", ""))
            if record.get("lesson_id") != lesson_id or record_id in referenced_ids:
                kept.append(record)
                continue
            target = (self.state_dir / str(record.get("relative_path", ""))).resolve()
            if screenshot_root in target.parents and target.is_file():
                target.unlink()
            removed.append(record_id)

        if removed:
            self._save_list("screenshots.json", kept)
        return removed

    def _append_record(self, name: str, payload: dict) -> None:
        payload = dict(payload)
        payload.setdefault("record_id", uuid.uuid4().hex[:12])
        payload.setdefault("created_at", utc_now())
        records = self._load_list(name)
        records.append(payload)
        self._save_list(name, records)
        if name == "annotations.json" and payload.get("type") == "anchor" and self.manifest_path.exists():
            manifest = load_manifest(self.manifest_path)
            for lesson in manifest.get("lessons", []):
                if lesson.get("id") == payload.get("lesson_id"):
                    lesson["anchors_count"] = sum(
                        1 for item in records if item.get("lesson_id") == lesson["id"] and item.get("type") == "anchor"
                    )
                    break
            save_manifest(self.manifest_path, manifest)
        self._send_json(payload, HTTPStatus.CREATED)

    def _update_record(self, name: str, record_id: str, changes: dict) -> None:
        records = self._load_list(name)
        for record in records:
            if record.get("record_id") == record_id:
                record.update(changes)
                record["updated_at"] = utc_now()
                self._save_list(name, records)
                return self._send_json(record)
        return self._send_json({"error": "record_not_found"}, HTTPStatus.NOT_FOUND)

    def _update_feedback(self, record_id: str, changes: dict) -> None:
        status = changes.get("status")
        if status not in {"open", "resolved"} or set(changes) != {"status"}:
            return self._send_json({"error": "invalid_feedback_status"}, HTTPStatus.BAD_REQUEST)
        return self._update_record("feedback.json", record_id, {"status": status})

    def _delete_record(self, name: str, record_id: str) -> None:
        records = self._load_list(name)
        remaining = [record for record in records if record.get("record_id") != record_id]
        if len(remaining) == len(records):
            return self._send_json({"error": "record_not_found"}, HTTPStatus.NOT_FOUND)
        self._save_list(name, remaining)
        return self._send_json({"record_id": record_id, "deleted": True})

    def _create_screenshot(self, payload: dict) -> None:
        lesson_id = str(payload.get("lesson_id", "")).strip()
        if not lesson_id or not self._find_lesson(lesson_id):
            return self._send_json({"error": "lesson_not_found"}, HTTPStatus.NOT_FOUND)
        image_data = payload.get("image_data")
        if not isinstance(image_data, str) or not image_data.startswith("data:image/png;base64,"):
            return self._send_json({"error": "invalid_screenshot"}, HTTPStatus.BAD_REQUEST)
        try:
            image_bytes = base64.b64decode(image_data.partition(",")[2], validate=True)
        except (ValueError, binascii.Error):
            return self._send_json({"error": "invalid_screenshot"}, HTTPStatus.BAD_REQUEST)
        if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n") or len(image_bytes) > 16 * 1024 * 1024:
            return self._send_json({"error": "invalid_screenshot"}, HTTPStatus.BAD_REQUEST)
        try:
            time_seconds = max(0.0, float(payload.get("time_seconds") or 0))
            width = int(payload.get("width") or 0)
            height = int(payload.get("height") or 0)
        except (TypeError, ValueError):
            return self._send_json({"error": "invalid_screenshot_metadata"}, HTTPStatus.BAD_REQUEST)
        if width <= 0 or height <= 0 or width > 16384 or height > 16384:
            return self._send_json({"error": "invalid_screenshot_metadata"}, HTTPStatus.BAD_REQUEST)

        record_id = uuid.uuid4().hex[:12]
        relative_path = Path("screenshots") / lesson_id / f"{record_id}.png"
        target = (self.state_dir / relative_path).resolve()
        screenshot_root = (self.state_dir / "screenshots").resolve()
        if screenshot_root not in target.parents:
            return self._send_json({"error": "invalid_screenshot_path"}, HTTPStatus.BAD_REQUEST)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(image_bytes)
        temporary.replace(target)

        record = {
            "kind": "screenshot",
            "record_id": record_id,
            "lesson_id": lesson_id,
            "time_seconds": time_seconds,
            "width": width,
            "height": height,
            "mime": "image/png",
            "relative_path": relative_path.as_posix(),
            "image_url": f"/screenshots/{record_id}",
            "created_at": utc_now(),
        }
        records = self._load_list("screenshots.json")
        records.append(record)
        self._save_list("screenshots.json", records)
        self._send_json(record, HTTPStatus.CREATED)

    def _serve_screenshot(self, record_id: str) -> None:
        record = next(
            (item for item in self._load_list("screenshots.json") if item.get("record_id") == record_id),
            None,
        )
        if not record:
            return self._send_json({"error": "screenshot_not_found"}, HTTPStatus.NOT_FOUND)
        target = (self.state_dir / str(record.get("relative_path", ""))).resolve()
        screenshot_root = (self.state_dir / "screenshots").resolve()
        if screenshot_root not in target.parents or not target.is_file():
            return self._send_json({"error": "screenshot_not_found"}, HTTPStatus.NOT_FOUND)
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.removeprefix("/")
        target = (self.server.web_dir / relative).resolve()  # type: ignore[attr-defined]
        web_root = self.server.web_dir.resolve()  # type: ignore[attr-defined]
        if web_root not in target.parents and target != web_root:
            return self._send_json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
        if not target.is_file():
            return self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _find_lesson(self, requested_id: str) -> dict | None:
        if not self.manifest_path.exists():
            return None
        manifest = load_manifest(self.manifest_path)
        return next((item for item in manifest.get("lessons", []) if item["id"] == requested_id), None)

    def _proxy_path(self, requested_id: str) -> Path:
        return self.state_dir / "media" / f"{requested_id}.mp4"

    def _media_details(self, lesson: dict) -> dict:
        media_path = Path(lesson["source_path"]).resolve()
        probe = probe_media(media_path) if media_path.is_file() else {"duration_seconds": None, "streams": []}
        video = next((item for item in probe.get("streams", []) if item.get("codec_type") == "video"), {})
        audio = next((item for item in probe.get("streams", []) if item.get("codec_type") == "audio"), {})
        proxy_path = self._proxy_path(lesson["id"])
        direct_playable = video.get("codec_name") in {"h264", "vp8", "vp9", "av1"}
        return {
            "lesson_id": lesson["id"],
            "exists": media_path.is_file(),
            "readable": media_path.is_file(),
            "mime": mimetypes.guess_type(str(media_path))[0] or "application/octet-stream",
            "size_bytes": media_path.stat().st_size if media_path.is_file() else 0,
            "duration_seconds": probe.get("duration_seconds") or lesson.get("duration_seconds"),
            "video_codec": video.get("codec_name"),
            "audio_codec": audio.get("codec_name"),
            "direct_playable": direct_playable,
            "proxy_ready": proxy_path.is_file() and proxy_path.stat().st_size > 0,
            "playable": direct_playable or (proxy_path.is_file() and proxy_path.stat().st_size > 0),
            "source_policy": "local_only_no_upload",
        }

    def _media_health(self, requested_id: str) -> None:
        lesson = self._find_lesson(requested_id)
        if not lesson:
            return self._send_json({"error": "lesson_not_found"}, HTTPStatus.NOT_FOUND)
        return self._send_json(self._media_details(lesson))

    def _prepare_media(self, requested_id: str) -> None:
        lesson = self._find_lesson(requested_id)
        if not lesson:
            return self._send_json({"error": "lesson_not_found"}, HTTPStatus.NOT_FOUND)
        details = self._media_details(lesson)
        if not details["exists"]:
            return self._send_json({"error": "media_not_found"}, HTTPStatus.NOT_FOUND)
        if details["direct_playable"] or details["proxy_ready"]:
            return self._send_json(details)

        source = Path(lesson["source_path"]).resolve()
        proxy = self._proxy_path(requested_id)
        proxy.parent.mkdir(parents=True, exist_ok=True)
        temporary = proxy.with_name(f"{proxy.stem}.preparing.mp4")
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", str(temporary),
        ]
        try:
            subprocess.run(command, capture_output=True, text=True, check=True)
            temporary.replace(proxy)
        except (OSError, subprocess.CalledProcessError) as exc:
            if temporary.exists():
                temporary.unlink()
            message = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            return self._send_json(
                {"error": "media_prepare_failed", "message": message[-1000:]},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        return self._send_json(self._media_details(lesson))

    def _serve_media(self, requested_id: str) -> None:
        lesson = self._find_lesson(requested_id)
        if not lesson:
            return self._send_json({"error": "lesson_not_found"}, HTTPStatus.NOT_FOUND)
        proxy_path = self._proxy_path(requested_id)
        media_path = proxy_path if proxy_path.is_file() else Path(lesson["source_path"]).resolve()
        if not media_path.is_file():
            return self._send_json({"error": "media_not_found"}, HTTPStatus.NOT_FOUND)
        self._send_file_with_range(media_path)

    def _serve_transcript(self, requested_id: str) -> None:
        if not self.manifest_path.exists():
            return self._send_json({"error": "manifest_not_found"}, HTTPStatus.NOT_FOUND)
        manifest = load_manifest(self.manifest_path)
        lesson = next((item for item in manifest.get("lessons", []) if item["id"] == requested_id), None)
        if not lesson or not lesson.get("transcript_path"):
            return self._send_json({"error": "transcript_not_ready"}, HTTPStatus.NOT_FOUND)
        transcript_path = Path(lesson["transcript_path"]).resolve()
        transcript_root = (self.state_dir / "transcripts").resolve()
        if transcript_root not in transcript_path.parents or not transcript_path.is_file():
            return self._send_json({"error": "transcript_path_invalid"}, HTTPStatus.FORBIDDEN)
        payload = json.loads(transcript_path.read_text(encoding="utf-8"))
        payload["display_language"] = "zh-CN"
        payload["display_policy"] = "traditional_source_preserved_simplified_at_api"
        return self._send_json(simplify_payload(payload))

    def _serve_view(self, requested_id: str) -> None:
        if not self.manifest_path.exists():
            return self._send_json({"error": "manifest_not_found"}, HTTPStatus.NOT_FOUND)
        manifest = load_manifest(self.manifest_path)
        lesson = next((item for item in manifest.get("lessons", []) if item["id"] == requested_id), None)
        if not lesson or not lesson.get("view_path"):
            return self._send_json({"error": "view_not_ready"}, HTTPStatus.NOT_FOUND)
        view_path = Path(lesson["view_path"]).resolve()
        view_root = (self.state_dir / "views").resolve()
        if view_root not in view_path.parents or not view_path.is_file():
            return self._send_json({"error": "view_path_invalid"}, HTTPStatus.FORBIDDEN)
        payload = json.loads(view_path.read_text(encoding="utf-8"))
        payload["display_language"] = "zh-CN"
        return self._send_json(simplify_payload(payload))

    def _send_file_with_range(self, path: Path) -> None:
        size = path.stat().st_size
        start, end = 0, size - 1
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            try:
                spec = range_header.removeprefix("bytes=").split(",", 1)[0].strip()
                left, separator, right = spec.partition("-")
                if not separator or (not left and not right):
                    raise ValueError("invalid range")
                if left:
                    start = int(left)
                    end = int(right) if right else size - 1
                else:
                    suffix_length = int(right)
                    if suffix_length <= 0:
                        raise ValueError("invalid suffix range")
                    start = max(size - suffix_length, 0)
                    end = size - 1
                end = min(end, size - 1)
                if start < 0 or start > end or start >= size:
                    raise ValueError("range outside file")
            except ValueError:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        else:
            status = HTTPStatus.OK
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    break
                remaining -= len(chunk)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[workbench] {fmt % args}")


def serve(state_dir: Path, web_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), WorkbenchHandler)
    httpd.state_dir = state_dir.resolve()  # type: ignore[attr-defined]
    httpd.web_dir = web_dir.resolve()  # type: ignore[attr-defined]
    httpd.project_root = web_dir.resolve().parent  # type: ignore[attr-defined]
    _ensure_generation_state(httpd)
    print(f"Learning Workbench: http://{host}:{port}")
    print(f"State: {httpd.state_dir}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
