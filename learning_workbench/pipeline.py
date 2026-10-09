from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def natural_sort_key(value: Path | str) -> tuple[object, ...]:
    """Sort lesson filenames by their final numeric course number."""
    name = value.name if isinstance(value, Path) else str(value)
    lesson_number = re.search(r"(\d+)(?=\.[^.]+$)", name)
    if lesson_number:
        return (0, int(lesson_number.group(1)), name.casefold())
    return (
        1,
        tuple(
            int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", name)
        ),
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def lesson_id(path: Path) -> str:
    """Return a stable id without exposing the full local path in the id."""
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]


def probe_media(path: Path) -> dict[str, Any]:
    """Read safe media metadata via ffprobe; never copies or rewrites the source."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration:stream=index,codec_name,codec_type,profile,pix_fmt,width,height",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout or "{}")
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        return {"probe_error": str(exc), "duration_seconds": None, "streams": []}

    fmt = data.get("format", {})
    return {
        "duration_seconds": float(fmt["duration"]) if fmt.get("duration") else None,
        "streams": data.get("streams", []),
    }


def build_manifest(source_dir: Path, output_path: Path) -> dict[str, Any]:
    """Create a safe local manifest. Source media stays in place and is never copied."""
    source_dir = source_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise ValueError(f"Source directory does not exist: {source_dir}")

    lessons: list[dict[str, Any]] = []
    for path in sorted(source_dir.iterdir(), key=natural_sort_key):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        meta = probe_media(path)
        lessons.append(
            {
                "id": lesson_id(path),
                "title": path.stem,
                "source_path": str(path),
                "source_name": path.name,
                "size_bytes": path.stat().st_size,
                "duration_seconds": meta.get("duration_seconds"),
                "streams": meta.get("streams", []),
                "transcript_status": "pending",
                "transcript_path": None,
                "view_status": "pending",
                "view_path": None,
                "anchors_count": 0,
            }
        )

    manifest = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "source_root": str(source_dir),
        "source_policy": "local_only_no_media_copy",
        "lesson_count": len(lessons),
        "lessons": lessons,
    }
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def compile_lesson_view(transcript_path: Path, output_path: Path) -> dict[str, Any]:
    """Compile source-grounded preview/linear/mind-map views without inventing facts."""
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    segments = transcript.get("segments", [])
    preview_segments = segments[:3]
    introduction = " ".join(item.get("text", "").strip() for item in segments[:6]).strip()
    if len(introduction) > 320:
        introduction = introduction[:317].rstrip() + "…"
    existing_introduction = None
    existing_introduction_source = None
    if output_path.exists():
        try:
            previous_view = json.loads(output_path.read_text(encoding="utf-8"))
            if previous_view.get("introduction_source"):
                existing_introduction = previous_view.get("introduction")
                existing_introduction_source = previous_view.get("introduction_source")
        except (OSError, json.JSONDecodeError):
            pass
    group_size = 8
    mind_map: list[dict[str, Any]] = []
    for group_start in range(0, len(segments), group_size):
        group = segments[group_start : group_start + group_size]
        if not group:
            continue
        children = [
            {
                "label": f"来源段落 {group_start + child_index + 1:02d}",
                "start": item.get("start", 0),
                "end": item.get("end", 0),
                "text": item.get("text", "").strip(),
                "source_segment_index": group_start + child_index,
            }
            for child_index, item in enumerate(group)
        ]
        mind_map.append(
            {
                "label": f"结构分组 {len(mind_map) + 1:02d} · 来源段落 {group_start + 1:02d}–{group_start + len(group):02d}",
                "start": group[0].get("start", 0),
                "end": group[-1].get("end", 0),
                "children": children,
                "source_policy": "grouped_source_segments_no_generated_topic",
            }
        )
    view = {
        "schema_version": 1,
        "lesson_id": transcript.get("lesson_id"),
        "source_name": transcript.get("source_name"),
        "introduction": existing_introduction or introduction,
        "preview": " ".join(item.get("text", "").strip() for item in preview_segments).strip(),
        "linear_note": transcript.get("text", "").strip(),
        "mind_map": mind_map,
        "mind_map_policy": "hierarchical_source_groups_no_generated_topics",
        "source_policy": "source_grounded_no_generated_claims",
    }
    if existing_introduction_source:
        view["introduction_source"] = existing_introduction_source
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8")
    return view
