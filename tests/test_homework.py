import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from learning_workbench.homework import execute_codex_homework, homework_day, homework_prompt, parse_homework
from learning_workbench.note_provenance import user_note_content


TRANSCRIPT = {"segments": [{"start": 10.0, "end": 20.0, "text": "用证据检验假设"},
                           {"start": 30.0, "end": 45.0, "text": "比较备选方案"}]}


def response():
    return {"tasks": [{"type": kind, "task": "用自己的话说明本课的方法并写下一个可检查的例子", "anchor_seconds": 12.5,
                       "anchor_label": "方法与证据"} for kind in ("回忆", "应用", "输出")]}


def test_prompt_is_generic_source_grounded_and_uses_only_user_notes():
    prompt = homework_prompt({"id": "lesson", "title": "假设检验"}, TRANSCRIPT,
                             {"introduction": "统计推断", "mind_map": [{"label": "检验", "start": 10}]},
                             "我的思考 [00:12](time:12)\n![截图](/screenshots/a)\n## 早期学习记录\n旧记录")
    # the instruction part is course-agnostic; course facts only appear in the reference material
    assert "假设检验" not in prompt.split("参考材料：")[0]
    context = json.loads(prompt.split("参考材料：\n")[1])
    assert context["transcript"] == TRANSCRIPT["segments"]
    assert context["introduction"] == "统计推断"
    assert context["mind_map"][0]["start"] == 10
    assert context["user_notes"] == "我的思考"
    assert "18-40" in prompt and "不调用工具" in prompt


@pytest.mark.parametrize("fenced", [False, True])
def test_parse_valid_json(fenced):
    output = json.dumps(response(), ensure_ascii=False)
    if fenced:
        output = f"```json\n{output}\n```"
    tasks = parse_homework(output, TRANSCRIPT)
    assert len(tasks) == 3 and tasks[0]["anchor_seconds"] == 12.5


@pytest.mark.parametrize("output", ["no json", "[]", "{}", '{"tasks":{}}', '{"tasks":[null,null,null]}'])
def test_parse_rejects_invalid_json_or_structure(output):
    with pytest.raises(ValueError):
        parse_homework(output, TRANSCRIPT)


@pytest.mark.parametrize("key,value", [("type", "考试"), ("type", []), ("task", "太短"),
                                     ("task", "长" * 41), ("task", 123), ("anchor_label", "")])
def test_parse_rejects_invalid_fields(key, value):
    payload = response()
    payload["tasks"][0][key] = value
    with pytest.raises(ValueError):
        parse_homework(json.dumps(payload), TRANSCRIPT)


def test_parse_requires_one_of_each_type():
    payload = response()
    payload["tasks"][1]["type"] = "回忆"
    with pytest.raises(ValueError, match="各有一项"):
        parse_homework(json.dumps(payload), TRANSCRIPT)


@pytest.mark.parametrize("anchor,expected", [(-100, 10), (999, 30), (None, None), ("bad", None),
                                           (True, None), (float("nan"), None), (float("inf"), None)])
def test_anchor_snaps_or_is_null(anchor, expected):
    payload = response()
    payload["tasks"][0]["anchor_seconds"] = anchor
    assert parse_homework(json.dumps(payload), TRANSCRIPT)[0]["anchor_seconds"] == expected


def test_transcript_requires_valid_timing():
    with pytest.raises(ValueError, match="有效时间"):
        parse_homework(json.dumps(response()), {"segments": [{"start": -1, "end": 0, "text": "bad"}]})


def test_runner_is_read_only_ephemeral_and_reads_final_message(tmp_path):
    def run(command, **kwargs):
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "--ephemeral" in command and command[-1] == "-"
        assert kwargs["input"] == "prompt" and kwargs["timeout"] == 300
        Path(command[command.index("--output-last-message") + 1]).write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="logs are not JSON", stderr="")
    with patch("learning_workbench.homework.shutil.which", return_value="codex.exe"):
        assert execute_codex_homework(tmp_path, "prompt", runner=run) == "{}"


@pytest.mark.parametrize("mode", ["missing", "exit", "timeout", "no_response", "os_error"])
def test_runner_errors_are_clear_and_mocked(tmp_path, mode):
    runner = Mock(return_value=SimpleNamespace(returncode=1 if mode == "exit" else 0))
    if mode == "timeout":
        runner.side_effect = subprocess.TimeoutExpired("codex", 300)
    if mode == "os_error":
        runner.side_effect = OSError("cannot launch")
    with patch("learning_workbench.homework.shutil.which", return_value=None if mode == "missing" else "codex.exe"):
        with pytest.raises(RuntimeError, match="Codex CLI"):
            execute_codex_homework(tmp_path, "prompt", runner=runner)
    if mode == "missing":
        runner.assert_not_called()


def test_user_content_preserves_mixed_text_and_closes_legacy_section():
    content = "我的理解\n- [00:10](time:10)\n[回到](time:10) 我的感想\n## 早期学习记录\n旧文本\n### 细节\n旧细节\n## 新想法\n新文本\n## AI 作业\n机器内容"
    assert user_note_content(content) == "我的理解\n\n 我的感想\n## 新想法\n新文本"


def test_homework_day_uses_local_calendar():
    assert homework_day("2026-10-09T12:00:00+00:00") == datetime_local_day("2026-10-09T12:00:00+00:00")


def datetime_local_day(timestamp):
    from datetime import datetime
    return datetime.fromisoformat(timestamp).astimezone().date().isoformat()
