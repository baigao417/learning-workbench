"""Read-time provenance heuristics; never rewrite stored Markdown."""
from __future__ import annotations

import re


AUTOMATIC_TOKEN = re.compile(r"!\[[^\]]*\]\(/screenshots/[\w-]+\)|(?<!!)\[[^\]]*\]\(time:\d+(?:\.\d+)?\)")


def user_note_content(content: str) -> str:
    lines = []
    section_level = None
    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            level, title = len(heading[1]), heading[2]
            if section_level is not None and level <= section_level:
                section_level = None
            if title == "早期学习记录" or re.match(r"^AI(?:\s|生成|简介|作业|笔记|内容)", title):
                section_level = level
        if section_level is None:
            mine = AUTOMATIC_TOKEN.sub("", line)
            text = re.sub(r"^\s*(?:#{1,3}\s+|[-*]\s+|\d+\.\s+|>\s?)", "", mine).strip()
            lines.append("" if mine != line and not text else mine)
    return "\n".join(lines).strip()
