import json
import unittest
from pathlib import Path
from unittest.mock import patch

from learning_workbench.pipeline import build_manifest, compile_lesson_view, lesson_id


class PipelineTests(unittest.TestCase):
    def test_lesson_id_is_stable_and_path_scoped(self) -> None:
        source = Path(self.tempdir.name) / "lesson.mp4"
        source.write_bytes(b"fake")
        self.assertEqual(lesson_id(source), lesson_id(source))
        self.assertEqual(len(lesson_id(source)), 12)


    def setUp(self) -> None:
        import tempfile
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_build_manifest_registers_media_without_copying(self) -> None:
        tmp_path = Path(self.tempdir.name)
        source = tmp_path / "source"
        source.mkdir()
        video = source / "第一节.mp4"
        video.write_bytes(b"fake")
        (source / "ignore.txt").write_text("ignore", encoding="utf-8")
        output = tmp_path / "state" / "manifest.json"
        with patch("learning_workbench.pipeline.probe_media", return_value={"duration_seconds": 12.5, "streams": []}):
            manifest = build_manifest(source, output)
        self.assertEqual(manifest["lesson_count"], 1)
        self.assertEqual(manifest["source_policy"], "local_only_no_media_copy")
        self.assertEqual(manifest["lessons"][0]["source_path"], str(video.resolve()))
        self.assertTrue(output.exists())
        self.assertFalse((output.parent / video.name).exists())

    def test_build_manifest_uses_natural_lesson_order(self) -> None:
        tmp_path = Path(self.tempdir.name)
        source = tmp_path / "source"
        source.mkdir()
        names = [
            "lesson_20260808140152_10.mp4",
            "lesson_20260808140152_8.mp4",
            "lesson_20260808140159_121.mp4",
            "lesson_20260808140152_39.mp4",
        ]
        for name in names:
            (source / name).write_bytes(b"fake")
        output = tmp_path / "state" / "manifest.json"
        with patch("learning_workbench.pipeline.probe_media", return_value={"duration_seconds": 1, "streams": []}):
            manifest = build_manifest(source, output)
        self.assertEqual(
            [item["source_name"] for item in manifest["lessons"]],
            [
                "lesson_20260808140152_8.mp4",
                "lesson_20260808140152_10.mp4",
                "lesson_20260808140152_39.mp4",
                "lesson_20260808140159_121.mp4",
            ],
        )

    def test_compile_lesson_view_is_source_grounded(self) -> None:
        tmp_path = Path(self.tempdir.name)
        transcript = tmp_path / "transcript.json"
        transcript.write_text(
            '{"lesson_id":"x","source_name":"lesson","text":"第一段 第二段","segments":[{"start":0,"end":2,"text":"第一段"},{"start":2,"end":4,"text":"第二段"}]}',
            encoding="utf-8",
        )
        output = tmp_path / "views" / "x.json"
        view = compile_lesson_view(transcript, output)
        self.assertEqual(view["preview"], "第一段 第二段")
        self.assertEqual(view["introduction"], "第一段 第二段")
        self.assertEqual(len(view["mind_map"]), 1)
        self.assertEqual(len(view["mind_map"][0]["children"]), 2)
        self.assertEqual(view["mind_map_policy"], "hierarchical_source_groups_no_generated_topics")
        self.assertEqual(view["source_policy"], "source_grounded_no_generated_claims")
        self.assertTrue(output.exists())

    def test_compile_lesson_view_preserves_local_generated_introduction(self) -> None:
        tmp_path = Path(self.tempdir.name)
        transcript = tmp_path / "transcript.json"
        transcript.write_text(
            '{"lesson_id":"x","source_name":"lesson","text":"新文本","segments":[{"start":0,"end":2,"text":"新文本"}]}',
            encoding="utf-8",
        )
        output = tmp_path / "views" / "x.json"
        output.parent.mkdir()
        output.write_text(
            json.dumps({"introduction": "本机生成的内容简介", "introduction_source": {"kind": "local_llm_summary"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        view = compile_lesson_view(transcript, output)
        self.assertEqual(view["introduction"], "本机生成的内容简介")
        self.assertEqual(view["introduction_source"]["kind"], "local_llm_summary")
