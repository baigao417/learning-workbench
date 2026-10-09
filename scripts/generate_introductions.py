from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from opencc import OpenCC


_T2S = OpenCC("t2s")


def to_simplified_chinese(text: str) -> str:
    return _T2S.convert(text)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_source_blocks(transcript: dict, target_chars: int = 180) -> list[dict[str, Any]]:
    """Aggregate tiny Whisper segments into prompt-sized, time-anchored source blocks."""
    blocks: list[dict[str, Any]] = []
    pending: list[dict] = []
    pending_chars = 0

    def flush() -> None:
        nonlocal pending, pending_chars
        if not pending:
            return
        blocks.append(
            {
                "index": len(blocks),
                "start": float(pending[0].get("start", 0) or 0),
                "end": float(pending[-1].get("end", pending[-1].get("start", 0)) or 0),
                "segment_start": int(pending[0]["_index"]),
                "segment_end": int(pending[-1]["_index"]),
                "text": " ".join(item.get("text", "").strip() for item in pending).strip(),
            }
        )
        pending = []
        pending_chars = 0

    for index, segment in enumerate(transcript.get("segments", [])):
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        if pending and pending_chars + len(text) > target_chars:
            flush()
        pending.append({**segment, "_index": index})
        pending_chars += len(text)
    flush()
    return blocks


def format_source_blocks(blocks: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"[区块 {item['index']}] [{item['start']:.1f}s-{item['end']:.1f}s] {item['text']}"
        for item in blocks
    )


def parse_generated_payload(raw: str) -> dict[str, Any]:
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model response did not contain JSON")
    payload = json.loads(cleaned[start : end + 1])
    title = to_simplified_chinese(str(payload.get("title", "")).strip().strip("《》\"'“”"))
    introduction = to_simplified_chinese(str(payload.get("introduction", "")).strip())
    mind_map = payload.get("mind_map")
    if not title or not introduction or not isinstance(mind_map, list) or not mind_map:
        raise ValueError("model response missed title, introduction, or mind_map")
    return {
        "title": title[:24].rstrip("，、： "),
        "introduction": introduction,
        "mind_map": mind_map,
    }


def compile_mind_map(nodes: list[dict[str, Any]], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not blocks:
        return []
    compiled: list[dict[str, Any]] = []
    for node in nodes[:7]:
        label = to_simplified_chinese(str(node.get("label", "")).strip())
        summary = to_simplified_chinese(str(node.get("summary", "")).strip())
        if not label:
            continue
        try:
            start_block = max(0, min(int(node.get("start_block", 0)), len(blocks) - 1))
            end_block = max(start_block, min(int(node.get("end_block", start_block)), len(blocks) - 1))
        except (TypeError, ValueError):
            start_block = end_block = 0
        children: list[dict[str, Any]] = []
        for child in node.get("children", [])[:5]:
            child_label = to_simplified_chinese(str(child.get("label", "")).strip())
            if not child_label:
                continue
            try:
                block_index = max(0, min(int(child.get("block", start_block)), len(blocks) - 1))
            except (TypeError, ValueError):
                block_index = start_block
            source = blocks[block_index]
            children.append(
                {
                    "label": child_label,
                    "start": source["start"],
                    "end": source["end"],
                    "text": to_simplified_chinese(source["text"][:220]),
                    "source_block_index": block_index,
                    "source_segment_index": source["segment_start"],
                }
            )
        used_blocks = {item["source_block_index"] for item in children}
        for block_index in (start_block, end_block, (start_block + end_block) // 2):
            if len(children) >= 2 or block_index in used_blocks:
                continue
            source = blocks[block_index]
            source_text = to_simplified_chinese(source["text"])
            short_label = source_text.replace("，", ",").split(",", 1)[0].strip()[:18]
            children.append(
                {
                    "label": short_label or "关键依据",
                    "start": source["start"],
                    "end": source["end"],
                    "text": source_text[:220],
                    "source_block_index": block_index,
                    "source_segment_index": source["segment_start"],
                }
            )
            used_blocks.add(block_index)
        if not children:
            source = blocks[start_block]
            children.append(
                {
                    "label": summary or "核心内容",
                    "start": source["start"],
                    "end": source["end"],
                    "text": to_simplified_chinese(source["text"][:220]),
                    "source_block_index": start_block,
                    "source_segment_index": source["segment_start"],
                }
            )
        compiled.append(
            {
                "label": label,
                "summary": summary,
                "start": blocks[start_block]["start"],
                "end": blocks[end_block]["end"],
                "source_block_range": [start_block, end_block],
                "children": children,
                "source_policy": "local_llm_topic_with_block_anchors",
            }
        )
    if not compiled:
        raise ValueError("model mind map did not contain usable nodes")
    return compiled


def call_ollama(
    blocks: list[dict[str, Any]],
    existing_introduction: str,
    model: str,
    endpoint: str,
) -> dict[str, Any]:
    prompt = f"""你是严谨的简体中文课程编辑和 XMind 思维导图整理者。请根据下面这节课程的完整来源区块，生成课程标题、内容简介和层级思维导图。

标题要求：8 到 24 个简体中文字符，概括真实核心内容，不使用原始文件名，不写“本视频”“第几课”。

简介要求：80 到 140 字，说明主要内容、关键观点和学习者能获得的理解；不编造、不夸张。

思维导图要求：
- 生成 3 到 6 个主分支，每个主分支包含 2 到 4 个子节点；
- 主分支是课程的真实主题结构，不得使用“结构分组”“来源段落”之类机械名称；
- 主分支之间要互不重复、按课程讲述顺序排列，并尽量覆盖从开头到结尾的完整内容；
- 节点文字简短，适合直接显示在 XMind 式图形节点中；
- 每个主分支必须给出覆盖的 start_block 和 end_block；
- 每个子节点必须给出最相关的 block；所有编号必须来自下方来源区块；
- 只使用简体中文，口语转写明显有误时按上下文谨慎概括。

只返回合法 JSON，不要附加解释：
{{
  "title": "课程标题",
  "introduction": "内容简介",
  "mind_map": [
    {{
      "label": "主分支",
      "summary": "一句话概括",
      "start_block": 0,
      "end_block": 2,
      "children": [
        {{"label": "子节点一", "block": 0}},
        {{"label": "子节点二", "block": 2}}
      ]
    }}
  ]
}}

已有内容简介（仅作辅助，仍须以来源区块为准）：
{existing_introduction or "无"}

完整来源区块：
{format_source_blocks(blocks)}
"""
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "format": "json",
            "think": False,
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 8192, "num_predict": 1800},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=300) as response:
        result = json.loads(response.read().decode("utf-8"))
    return parse_generated_payload(str(result.get("response", "")))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate local lesson titles, introductions, and mind maps.")
    parser.add_argument("--state", type=Path, default=Path(".local/state"))
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434/api/generate")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--lesson-id", default="")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--force-mind-map", action="store_true")
    args = parser.parse_args()

    manifest_path = args.state / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected = [lesson for lesson in manifest.get("lessons", []) if lesson.get("transcript_status") == "ready"]
    if args.lesson_id:
        selected = [lesson for lesson in selected if lesson.get("id") == args.lesson_id]
    if args.limit:
        selected = selected[: args.limit]
    for index, lesson in enumerate(selected, start=1):
        view_path = Path(lesson["view_path"])
        view = json.loads(view_path.read_text(encoding="utf-8"))
        complete = lesson.get("display_title") and view.get("introduction_source") and view.get("mind_map_source")
        if complete and not args.force and not args.force_mind_map:
            print(f"[{index}/{len(selected)}] skip {lesson['id']} (already generated)")
            continue
        transcript = json.loads(Path(lesson["transcript_path"]).read_text(encoding="utf-8"))
        blocks = build_source_blocks(transcript)
        generated: dict[str, Any] | None = None
        last_error: Exception | None = None
        for attempt in range(args.retries + 1):
            try:
                generated = call_ollama(blocks, str(view.get("introduction", "")), args.model, args.endpoint)
                break
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                print(f"[{index}/{len(selected)}] retry {lesson['id']} ({attempt + 1}): {exc}")
        if generated is None:
            raise RuntimeError(f"failed to generate {lesson['id']}: {last_error}")

        created_at = utc_now()
        if args.force or not lesson.get("display_title"):
            lesson["display_title"] = generated["title"]
            view["display_title"] = generated["title"]
            view["title_source"] = {
                "kind": "local_llm_title",
                "model": args.model,
                "created_at": created_at,
                "source_policy": "transcript_grounded_local_generation",
            }
        if args.force or not view.get("introduction_source"):
            view["introduction"] = generated["introduction"]
            view["introduction_source"] = {
                "kind": "local_llm_summary",
                "model": args.model,
                "created_at": created_at,
                "source_policy": "transcript_grounded_local_generation",
            }
        if args.force or args.force_mind_map or not view.get("mind_map_source"):
            view["mind_map"] = compile_mind_map(generated["mind_map"], blocks)
            view["mind_map_policy"] = "semantic_hierarchy_with_source_time_anchors"
            view["mind_map_source"] = {
                "kind": "local_llm_mind_map",
                "model": args.model,
                "created_at": created_at,
                "source_policy": "full_transcript_blocks_local_generation",
                "block_count": len(blocks),
            }
        view_path.write_text(json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{index}/{len(selected)}] {lesson['id']} -> map {len(view['mind_map'])} branches")


if __name__ == "__main__":
    main()
