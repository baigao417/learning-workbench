import unittest

from learning_workbench.language import simplify_payload, to_simplified_chinese


class SimplifiedChineseDisplayTests(unittest.TestCase):
    def test_converts_traditional_transcript_text(self) -> None:
        self.assertEqual(
            to_simplified_chinese("歡迎來到我們的學習方法課，先看目錄再精讀"),
            "欢迎来到我们的学习方法课，先看目录再精读",
        )

    def test_recursively_converts_api_payload_without_changing_numbers(self) -> None:
        payload = simplify_payload({"text": "處理問題", "segments": [{"start": 1.5, "text": "經驗"}]})
        self.assertEqual(payload["text"], "处理问题")
        self.assertEqual(payload["segments"][0], {"start": 1.5, "text": "经验"})


if __name__ == "__main__":
    unittest.main()
