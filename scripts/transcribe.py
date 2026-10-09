from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
import sys
from typing import Any

from faster_whisper import WhisperModel

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from learning_workbench.pipeline import compile_lesson_view, load_manifest, save_manifest, utc_now


DEFAULT_INITIAL_PROMPT = (
    "请保留专有名词、英文缩写、数字和人名，不要把停顿扩写成内容。"
)
# Domain terms help Whisper on specialised courses; pass --hotwords per course.
DEFAULT_HOTWORDS = ""


def _backup_existing(output_path: Path, lesson_id: str, model_name: str) -> Path | None:
    if not output_path.exists():
        return None
    previous_model = model_name
    try:
        previous = json.loads(output_path.read_text(encoding="utf-8"))
        previous_model = str(previous.get("model") or model_name)
    except (OSError, json.JSONDecodeError):
        pass
    version_dir = output_path.parent / "versions" / lesson_id
    version_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().replace(":", "").replace("+00:00", "Z")
    backup_path = version_dir / f"{stamp}-{previous_model}.json"
    shutil.copy2(output_path, backup_path)
    return backup_path


def _segment_payload(segments: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for segment in segments:
        text = str(getattr(segment, "text", "")).strip()
        if not text:
            continue
        rows.append(
            {
                "id": len(rows),
                "start": round(float(getattr(segment, "start", 0.0)), 3),
                "end": round(float(getattr(segment, "end", 0.0)), 3),
                "text": text,
            }
        )
    return rows


def transcribe_lesson(
    manifest_path: Path,
    lesson: dict,
    model: WhisperModel,
    model_name: str,
    language: str,
    device: str,
    compute_type: str,
    beam_size: int,
    vad_min_silence_ms: int,
    initial_prompt: str,
    hotwords: str,
) -> Path:
    segments, info = model.transcribe(
        lesson["source_path"],
        language=language or None,
        task="transcribe",
        beam_size=beam_size,
        best_of=5,
        temperature=0.0,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": vad_min_silence_ms},
        condition_on_previous_text=False,
        initial_prompt=initial_prompt or None,
        hotwords=hotwords or None,
        word_timestamps=False,
    )
    segment_rows = _segment_payload(list(segments))
    transcript_dir = manifest_path.parent / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    output_path = transcript_dir / f"{lesson['id']}.json"
    backup_path = _backup_existing(output_path, lesson["id"], model_name)
    payload = {
        "schema_version": 2,
        "created_at": utc_now(),
        "lesson_id": lesson["id"],
        "source_name": lesson["source_name"],
        "backend": "faster-whisper",
        "model": model_name,
        "language": getattr(info, "language", None) or language,
        "device": device,
        "compute_type": compute_type,
        "quality_profile": "zh_course_v1",
        "beam_size": beam_size,
        "best_of": 5,
        "vad_filter": True,
        "vad_min_silence_duration_ms": vad_min_silence_ms,
        "condition_on_previous_text": False,
        "initial_prompt": initial_prompt,
        "hotwords": hotwords,
        "duration_seconds": getattr(info, "duration", None),
        "segment_count": len(segment_rows),
        "text": " ".join(item["text"] for item in segment_rows).strip(),
        "segments": segment_rows,
    }
    temporary_path = output_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(output_path)
    view_dir = manifest_path.parent / "views"
    view_path = view_dir / f"{lesson['id']}.json"
    compile_lesson_view(output_path, view_path)
    lesson["transcript_status"] = "ready"
    lesson["transcript_path"] = str(output_path.resolve())
    lesson["view_status"] = "ready"
    lesson["view_path"] = str(view_path.resolve())
    if backup_path:
        lesson["previous_transcript_path"] = str(backup_path.resolve())
    lesson["transcript_backend"] = "faster-whisper"
    lesson["transcript_model"] = model_name
    lesson["transcript_quality_profile"] = "zh_course_v1"
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcribe one local lesson with cached faster-whisper.")
    parser.add_argument("--manifest", type=Path, default=Path(".local/state/manifest.json"))
    parser.add_argument("--lesson-id")
    parser.add_argument("--all", action="store_true", help="转录所有尚未就绪的课次")
    parser.add_argument("--limit", type=int, default=0, help="--all 时最多处理多少节，0 表示不限制")
    parser.add_argument("--model", default="large-v3", help="faster-whisper model name")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--compute-type", choices=["int8", "float16", "float32"], default="int8")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--vad-min-silence-ms", type=int, default=700)
    parser.add_argument("--initial-prompt", default=DEFAULT_INITIAL_PROMPT)
    parser.add_argument("--hotwords", default=DEFAULT_HOTWORDS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--force", action="store_true", help="重转已就绪课次，并保留旧稿版本")
    mode.add_argument("--upgrade", action="store_true", help="只重转非当前质量档的课次，并保留旧稿版本")
    parser.add_argument("--language", default="zh")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    if not args.lesson_id and not args.all:
        raise SystemExit("provide --lesson-id or --all")
    selected = [
        item for item in manifest.get("lessons", [])
        if (
            args.all
            and (
                args.force
                or item.get("transcript_status") != "ready"
                or (
                    args.upgrade
                    and (
                        item.get("transcript_backend") != "faster-whisper"
                        or item.get("transcript_model") != args.model
                        or item.get("transcript_quality_profile") != "zh_course_v1"
                    )
                )
            )
        )
        or item.get("id") == args.lesson_id
    ]
    if args.limit:
        selected = selected[:args.limit]
    if not selected:
        raise SystemExit("no matching pending lessons")
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    for index, lesson in enumerate(selected, start=1):
        output_path = transcribe_lesson(
            args.manifest,
            lesson,
            model,
            args.model,
            args.language,
            args.device,
            args.compute_type,
            args.beam_size,
            args.vad_min_silence_ms,
            args.initial_prompt,
            args.hotwords,
        )
        print(f"[{index}/{len(selected)}] transcribed {lesson['source_name']} -> {output_path}")
        save_manifest(args.manifest, manifest)


if __name__ == "__main__":
    main()
