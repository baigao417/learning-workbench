import unittest

from scripts.generate_introductions import build_source_blocks, compile_mind_map, parse_generated_payload


class GeneratedLessonMetadataTests(unittest.TestCase):
    def test_parses_structured_simplified_chinese_title_and_introduction(self) -> None:
        payload = parse_generated_payload(
            '```json\n{"title":"如何根據目的選擇閱讀方式","introduction":"這節課說明不同目的和材料需要不同的閱讀速度。","mind_map":[{"label":"閱讀目的","summary":"先看目的","start_block":0,"end_block":1,"children":[{"label":"速度選擇","block":1}]}]}\n```'
        )
        self.assertEqual(payload["title"], "如何根据目的选择阅读方式")
        self.assertIn("不同目的和材料", payload["introduction"])
        self.assertEqual(payload["mind_map"][0]["label"], "閱讀目的")

    def test_builds_time_anchored_semantic_mind_map(self) -> None:
        transcript = {
            "segments": [
                {"start": 0, "end": 2, "text": "先确定目的"},
                {"start": 2, "end": 4, "text": "再选择速度"},
            ]
        }
        blocks = build_source_blocks(transcript, target_chars=5)
        mind_map = compile_mind_map(
            [
                {
                    "label": "目的与材料",
                    "summary": "判断阅读任务",
                    "start_block": 0,
                    "end_block": 1,
                    "children": [{"label": "选择阅读速度", "block": 1}],
                }
            ],
            blocks,
        )
        self.assertEqual(mind_map[0]["label"], "目的与材料")
        self.assertEqual(mind_map[0]["start"], 0)
        self.assertEqual(mind_map[0]["children"][0]["start"], 2)

    def test_rejects_unstructured_model_output(self) -> None:
        with self.assertRaises(ValueError):
            parse_generated_payload("课程标题：阅读方法")


if __name__ == "__main__":
    unittest.main()
