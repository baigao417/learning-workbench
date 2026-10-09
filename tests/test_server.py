import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from learning_workbench.server import WorkbenchHandler


class ServerLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.state_dir = root / "state"
        self.state_dir.mkdir()
        transcripts = self.state_dir / "transcripts"
        views = self.state_dir / "views"
        transcripts.mkdir()
        views.mkdir()
        lesson_id = "lesson123456"
        self.lesson_id = lesson_id
        self.media_path = root / "sample.mp4"
        self.media_path.write_bytes(b"0123456789abcdef")
        transcript_path = transcripts / f"{lesson_id}.json"
        view_path = views / f"{lesson_id}.json"
        transcript_path.write_text(
            json.dumps(
                {
                    "lesson_id": lesson_id,
                    "source_name": "sample.mp4",
                    "text": "學習需要依據",
                    "segments": [{"start": 1.5, "end": 3.0, "text": "學習需要依據"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        view_path.write_text(
            json.dumps({"lesson_id": lesson_id, "preview": "學習需要依據", "mind_map": []}, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.state_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "lesson_count": 1,
                    "lessons": [
                        {
                            "id": lesson_id,
                            "title": "示例课",
                            "source_name": "sample.mp4",
                            "source_path": str(self.media_path),
                            "transcript_status": "ready",
                            "transcript_path": str(transcript_path.resolve()),
                            "view_path": str(view_path.resolve()),
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
        self.server.state_dir = self.state_dir.resolve()  # type: ignore[attr-defined]
        self.server.web_dir = (Path(__file__).resolve().parents[1] / "web").resolve()  # type: ignore[attr-defined]
        self.server.project_root = Path(__file__).resolve().parents[1]  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(self, path: str, method: str = "GET", payload: dict | None = None) -> dict | str:
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        with urlopen(Request(self.base_url + path, data=body, headers=headers, method=method)) as response:
            content = response.read().decode("utf-8")
            if response.headers.get_content_type() == "application/json":
                return json.loads(content)
            return content

    def test_homework_success_preserves_old_records_and_completion(self) -> None:
        from test_homework import response

        old = self.request("/api/training", "POST", {"lesson_id": self.lesson_id, "task": "旧任务", "completed": False})
        with patch("learning_workbench.server.execute_codex_homework", return_value=json.dumps(response())) as runner:
            generated = self.request("/api/training/generate", "POST", {"lesson_id": self.lesson_id})
            self.assertEqual(len(generated["tasks"]), 3)
            self.assertEqual(self.request("/api/training")[0], old)
            task = generated["tasks"][0]
            self.assertEqual(task["source"], {"kind": "ai_generated", "backend": "codex_cli", "created_at": task["created_at"]})
            self.assertEqual(task["batch_id"], generated["batch_id"])
            self.assertFalse(task["completed"])
            completed = self.request(f"/api/training/{task['record_id']}", "PATCH", {"completed": True, "result": "我的证据"})
            self.assertTrue(completed["completed"])
            self.assertEqual(completed["source"], task["source"])
            with self.assertRaises(HTTPError) as error:
                self.request("/api/training/generate", "POST", {"lesson_id": self.lesson_id})
            self.assertEqual(error.exception.code, 409)
            self.assertEqual(runner.call_count, 1)

    def test_homework_failure_does_not_write_and_allows_retry(self) -> None:
        from test_homework import response

        with patch("learning_workbench.server.execute_codex_homework", side_effect=["bad json", RuntimeError("CLI unavailable"), json.dumps(response())]):
            for status in (422, 503):
                with self.assertRaises(HTTPError) as error:
                    self.request("/api/training/generate", "POST", {"lesson_id": self.lesson_id})
                self.assertEqual(error.exception.code, status)
                self.assertFalse((self.state_dir / "training.json").exists())
            self.assertEqual(len(self.request("/api/training/generate", "POST", {"lesson_id": self.lesson_id})["tasks"]), 3)

    def test_homework_rejects_unknown_lesson_extra_fields_and_missing_transcript(self) -> None:
        with patch("learning_workbench.server.execute_codex_homework") as runner:
            for payload, status in (({"lesson_id": "unknown"}, 404), ({"lesson_id": self.lesson_id, "prompt": "run"}, 400),
                                    ({"lesson_id": []}, 400)):
                with self.assertRaises(HTTPError) as error:
                    self.request("/api/training/generate", "POST", payload)
                self.assertEqual(error.exception.code, status)
            transcript = self.state_dir / "transcripts" / f"{self.lesson_id}.json"
            transcript.write_text('{"segments": []}', encoding="utf-8")
            with self.assertRaises(HTTPError) as error:
                self.request("/api/training/generate", "POST", {"lesson_id": self.lesson_id})
            self.assertEqual(error.exception.code, 422)
            runner.assert_not_called()
            self.assertFalse((self.state_dir / "training.json").exists())

    def test_homework_failed_validation_preserves_existing_file_bytes(self) -> None:
        old = self.request("/api/training", "POST", {"lesson_id": self.lesson_id, "task": "旧任务"})
        path = self.state_dir / "training.json"
        before = path.read_bytes()
        with patch("learning_workbench.server.execute_codex_homework", return_value='{"tasks": []}'):
            with self.assertRaises(HTTPError):
                self.request("/api/training/generate", "POST", {"lesson_id": self.lesson_id})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.request("/api/training"), [old])

    def test_homework_concurrent_requests_generate_only_once(self) -> None:
        from test_homework import response

        entered, release = threading.Event(), threading.Event()
        results = []
        def run(*args):
            entered.set()
            release.wait(timeout=5)
            return json.dumps(response())
        def first_request():
            results.append(self.request("/api/training/generate", "POST", {"lesson_id": self.lesson_id}))
        with patch("learning_workbench.server.execute_codex_homework", side_effect=run) as runner:
            thread = threading.Thread(target=first_request)
            thread.start()
            try:
                self.assertTrue(entered.wait(timeout=3))
                with self.assertRaises(HTTPError) as error:
                    self.request("/api/training/generate", "POST", {"lesson_id": self.lesson_id})
                self.assertEqual(error.exception.code, 409)
            finally:
                release.set()
                thread.join(timeout=5)
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(len(results[0]["tasks"]), 3)

    def test_learning_loop_persists_and_searches_user_records(self) -> None:
        search = self.request(f"/api/search?q={quote('依据')}")
        self.assertEqual(search["total"], 1)
        transcript = self.request(f"/api/transcript/{self.lesson_id}")
        view = self.request(f"/api/view/{self.lesson_id}")
        self.assertEqual(transcript["segments"][0]["text"], "学习需要依据")
        self.assertEqual(transcript["display_language"], "zh-CN")
        self.assertEqual(view["preview"], "学习需要依据")
        note = self.request(
            "/api/annotations",
            "POST",
            {"lesson_id": "lesson123456", "type": "note", "time_seconds": 2, "text": "我的判断依据"},
        )
        friction = self.request(
            "/api/annotations",
            "POST",
            {"lesson_id": "lesson123456", "type": "friction", "time_seconds": 4, "text": "找不到章节入口"},
        )
        training = self.request(
            "/api/training",
            "POST",
            {"lesson_id": "lesson123456", "task": "完成一次应用", "completed": False},
        )
        completed = self.request(
            f"/api/training/{training['record_id']}",
            "PATCH",
            {"completed": True, "result": "完成并写下判断依据"},
        )
        friction_search = self.request(f"/api/search?q={quote('章节入口')}")
        annotations = self.request("/api/annotations")
        training_rows = self.request("/api/training")
        page = self.request("/")
        app = self.request("/app.js")

        self.assertEqual(note["type"], "note")
        self.assertEqual(friction["type"], "friction")
        self.assertEqual(completed["result"], "完成并写下判断依据")
        self.assertEqual(friction_search["total"], 1)
        self.assertEqual(len(annotations), 2)
        self.assertTrue(training_rows[0]["completed"])
        self.assertIn("一体化学习工作台", page)
        self.assertIn("同步逐字稿", page)
        self.assertIn("结构化思维导图", page)
        self.assertIn("id=\"mind-map-canvas\"", page)
        self.assertIn("id=\"play-toggle\"", page)
        self.assertIn("id=\"catalog-toggle\"", page)
        self.assertIn("id=\"screenshot-button\"", page)
        self.assertIn("id=\"note-time\"", page)
        self.assertIn("aria-label=\"学习笔记\"", page)
        self.assertNotIn("id=\"note-read-mode\"", page)
        self.assertNotIn("id=\"record-stream\"", page)
        self.assertNotIn("id=\"save-friction\"", page)
        self.assertIn("/note-document.js", page)
        self.assertIn("/api/generation-jobs", app)
        self.assertNotIn("id=\"save-note\"", page)
        self.assertNotIn("<video id=\"player\" controls", page)

        legacy_document = self.request(f"/api/note-documents/{self.lesson_id}")
        self.assertIn("我的判断依据", legacy_document["content"])
        self.assertIn("找不到章节入口", legacy_document["content"])
        document = self.request(
            f"/api/note-documents/{self.lesson_id}",
            "PUT",
            {"content": "# 本课判断\n\n- [00:02](time:2.5) 版型依据",
             "legacy_annotation_ids": legacy_document["legacy_annotation_ids"]},
        )
        updated_document = self.request(
            f"/api/note-documents/{self.lesson_id}",
            "PUT",
            {"content": "# 本课判断\n\n自动保存后的版型依据"},
        )
        document_rows = json.loads((self.state_dir / "note-documents.json").read_text(encoding="utf-8"))
        document_search = self.request(f"/api/search?q={quote('自动保存')}" )
        self.assertEqual(document["lesson_id"], self.lesson_id)
        self.assertEqual(updated_document["content"], "# 本课判断\n\n自动保存后的版型依据")
        self.assertEqual(len(document_rows), 1)
        self.assertEqual(document_search["results"][0]["kind"], "note_document")

        feedback = self.request(
            "/api/feedback",
            "POST",
            {"lesson_id": "lesson123456", "kind": "idea", "text": "希望结构视图可以折叠"},
        )
        feedback_rows = self.request("/api/feedback")
        self.assertEqual(feedback["status"], "open")
        self.assertEqual(feedback_rows[0]["text"], "希望结构视图可以折叠")
        resolved_feedback = self.request(
            f"/api/feedback/{feedback['record_id']}",
            "PATCH",
            {"status": "resolved"},
        )
        self.assertEqual(resolved_feedback["status"], "resolved")
        self.assertIn("updated_at", resolved_feedback)
        deleted_feedback = self.request(f"/api/feedback/{feedback['record_id']}", "DELETE")
        self.assertTrue(deleted_feedback["deleted"])
        self.assertEqual(self.request("/api/feedback"), [])

        screenshot = self.request(
            "/api/screenshots",
            "POST",
            {
                "lesson_id": self.lesson_id,
                "time_seconds": 2.5,
                "width": 1,
                "height": 1,
                "image_data": (
                    "data:image/png;base64,"
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                ),
            },
        )
        screenshot_rows = self.request("/api/screenshots")
        self.assertEqual(screenshot["lesson_id"], self.lesson_id)
        self.assertEqual(screenshot_rows[0]["time_seconds"], 2.5)
        screenshot_path = self.state_dir / screenshot["relative_path"]
        self.assertTrue(screenshot_path.is_file())
        with urlopen(self.base_url + screenshot["image_url"]) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertTrue(response.read().startswith(b"\x89PNG"))

        kept_document = self.request(
            f"/api/note-documents/{self.lesson_id}",
            "PUT",
            {"content": f"![截图]({screenshot['image_url']})"},
        )
        self.assertEqual(kept_document["removed_screenshot_ids"], [])
        self.assertTrue(screenshot_path.is_file())

        reversible_document = self.request(
            f"/api/note-documents/{self.lesson_id}",
            "PUT",
            {"content": "暂时移除截图，可撤销", "preserve_screenshots": True},
        )
        self.assertEqual(reversible_document["removed_screenshot_ids"], [])
        self.request(f"/api/note-documents/{self.lesson_id}")
        self.assertTrue(screenshot_path.is_file())
        self.assertEqual(len(self.request("/api/screenshots")), 1)

        pruned_document = self.request(
            f"/api/note-documents/{self.lesson_id}",
            "PUT",
            {"content": "截图已从笔记删除"},
        )
        self.assertEqual(pruned_document["removed_screenshot_ids"], [screenshot["record_id"]])
        self.assertFalse(screenshot_path.exists())
        self.assertEqual(self.request("/api/screenshots"), [])

    def test_legacy_notes_are_readable_searchable_and_read_only_until_saved(self) -> None:
        records = [
            {"record_id": "old-note", "lesson_id": self.lesson_id, "type": "note",
             "text": "legacy-marker\n第二行", "time_seconds": 12.5, "created_at": "2026-09-01T10:00:00Z"},
            {"record_id": "old-anchor", "lesson_id": self.lesson_id, "type": "anchor",
             "text": "", "time_seconds": 3723},
            {"record_id": "other", "lesson_id": "another-lesson", "type": "note", "text": "other-private"},
        ]
        legacy_path = self.state_dir / "annotations.json"
        original = json.dumps(records, ensure_ascii=False).encode("utf-8")
        legacy_path.write_bytes(original)
        document = self.request(f"/api/note-documents/{self.lesson_id}")
        self.assertIn("legacy-marker\n第二行", document["content"])
        self.assertIn("[00:12](time:12.5)", document["content"])
        self.assertIn("[1:02:03](time:3723)", document["content"])
        self.assertIn("2026-09-01T10:00:00Z", document["content"])
        self.assertNotIn("other-private", document["content"])
        self.assertEqual(document["legacy_annotation_ids"], ["old-note", "old-anchor"])
        self.assertEqual(self.request("/api/search?q=legacy-marker")["total"], 1)
        self.assertEqual(self.request(f"/api/note-documents/{self.lesson_id}"), document)
        self.assertFalse((self.state_dir / "note-documents.json").exists())
        self.assertEqual(legacy_path.read_bytes(), original)

        self.request(f"/api/note-documents/{self.lesson_id}", "PUT", {
            "content": document["content"], "legacy_annotation_ids": document["legacy_annotation_ids"],
        })
        reloaded = self.request(f"/api/note-documents/{self.lesson_id}")
        self.assertEqual(reloaded["content"], document["content"])
        self.assertEqual(reloaded["content"].count("legacy-marker"), 1)
        self.assertEqual(self.request("/api/search?q=legacy-marker")["total"], 1)

        # Intentional deletion must not resurrect imported records on reload.
        self.request(f"/api/note-documents/{self.lesson_id}", "PUT", {"content": ""})
        self.assertEqual(self.request(f"/api/note-documents/{self.lesson_id}")["content"], "")
        self.assertEqual(self.request("/api/search?q=legacy-marker")["total"], 0)
        self.assertEqual(legacy_path.read_bytes(), original)

    def test_legacy_records_append_to_existing_document_without_duplicates(self) -> None:
        self.request(f"/api/note-documents/{self.lesson_id}", "PUT", {"content": "# 新笔记"})
        self.request("/api/annotations", "POST", {
            "lesson_id": self.lesson_id, "type": "friction", "text": "旧问题", "time_seconds": 0,
        })
        document = self.request(f"/api/note-documents/{self.lesson_id}")
        self.assertTrue(document["content"].startswith("# 新笔记"))
        self.assertIn("[00:00](time:0)", document["content"])
        self.assertIn("旧问题", document["content"])
        self.request(f"/api/note-documents/{self.lesson_id}", "PUT", document)
        self.request("/api/annotations", "POST", {
            "lesson_id": self.lesson_id, "type": "note", "text": "后来补录的旧记录",
        })
        reloaded = self.request(f"/api/note-documents/{self.lesson_id}")
        self.assertEqual(reloaded["content"].count("旧问题"), 1)
        self.assertEqual(reloaded["content"].count("后来补录的旧记录"), 1)
        self.assertEqual(len(reloaded["legacy_annotation_ids"]), 2)

    def test_invalid_legacy_receipt_does_not_overwrite_document(self) -> None:
        self.request(f"/api/note-documents/{self.lesson_id}", "PUT", {"content": "保留"})
        with self.assertRaises(HTTPError) as raised:
            self.request(f"/api/note-documents/{self.lesson_id}", "PUT", {
                "content": "不应覆盖", "legacy_annotation_ids": "not-a-list",
            })
        self.assertEqual(raised.exception.code, 400)
        self.assertEqual(self.request(f"/api/note-documents/{self.lesson_id}")["content"], "保留")

    def test_generation_job_runs_codex_and_verifies_written_materials(self) -> None:
        def generate_materials(project_root: Path, state_dir: Path, lesson: dict, action: str) -> str:
            self.assertEqual(project_root, Path(__file__).resolve().parents[1])
            self.assertEqual(state_dir, self.state_dir.resolve())
            self.assertEqual(lesson["id"], self.lesson_id)
            self.assertEqual(action, "materials")
            view_path = Path(lesson["view_path"])
            view = json.loads(view_path.read_text(encoding="utf-8"))
            view["mind_map"] = [{"label": "核心判断", "children": []}]
            view["mind_map_source"] = {
                "kind": "local_llm_mind_map",
                "model": "qwen3:4b",
                "created_at": "2026-09-04T00:00:00+00:00",
            }
            view_path.write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")
            return "generated and verified"

        with patch("learning_workbench.server.execute_codex_generation", side_effect=generate_materials) as mocked:
            job = self.request(
                "/api/generation-jobs",
                "POST",
                {"lesson_id": self.lesson_id, "action": "materials"},
            )
            deadline = time.monotonic() + 3
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.02)
                job = self.request(f"/api/generation-jobs/{job['job_id']}")

        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["message"], "generated and verified")
        self.assertEqual(mocked.call_count, 1)
        manifest = self.request("/api/manifest")
        self.assertTrue(manifest["lessons"][0]["materials"]["ready"])
        persisted = json.loads((self.state_dir / "generation-jobs.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted[0]["job_id"], job["job_id"])

    def test_generation_job_rejects_arbitrary_actions(self) -> None:
        with self.assertRaises(HTTPError) as raised:
            self.request(
                "/api/generation-jobs",
                "POST",
                {"lesson_id": self.lesson_id, "action": "run-any-command"},
            )
        self.assertEqual(raised.exception.code, 400)

    def test_media_health_head_and_range_support_local_playback(self) -> None:
        probe = {
            "duration_seconds": 12.5,
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
        with patch("learning_workbench.server.probe_media", return_value=probe):
            health = self.request(f"/api/media/{self.lesson_id}/health")
        self.assertTrue(health["direct_playable"])
        self.assertTrue(health["playable"])

        range_request = Request(
            self.base_url + f"/media/{self.lesson_id}",
            headers={"Range": "bytes=2-5"},
        )
        with urlopen(range_request) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers["Content-Range"], "bytes 2-5/16")
            self.assertEqual(response.read(), b"2345")

        suffix_request = Request(
            self.base_url + f"/media/{self.lesson_id}",
            headers={"Range": "bytes=-4"},
        )
        with urlopen(suffix_request) as response:
            self.assertEqual(response.read(), b"cdef")

        head_request = Request(self.base_url + f"/media/{self.lesson_id}", method="HEAD")
        with urlopen(head_request) as response:
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
            self.assertEqual(response.headers["Content-Length"], "16")
            self.assertEqual(response.read(), b"")

    def test_incompatible_media_is_prepared_as_local_proxy(self) -> None:
        probe = {
            "duration_seconds": 12.5,
            "streams": [
                {"codec_type": "video", "codec_name": "hevc"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }

        def create_proxy(command: list[str], **_: object) -> object:
            Path(command[-1]).write_bytes(b"compatible-proxy")
            return object()

        with (
            patch("learning_workbench.server.probe_media", return_value=probe),
            patch("learning_workbench.server.subprocess.run", side_effect=create_proxy),
        ):
            prepared = self.request(f"/api/media/{self.lesson_id}/prepare", "POST", {})

        self.assertTrue(prepared["proxy_ready"])
        self.assertTrue(prepared["playable"])
        self.assertTrue((self.state_dir / "media" / f"{self.lesson_id}.mp4").is_file())


if __name__ == "__main__":
    unittest.main()
