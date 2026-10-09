from __future__ import annotations

import argparse
import socket
import threading
import webbrowser
from pathlib import Path

from .pipeline import build_manifest
from .server import serve


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _choose_port(host: str, requested: int) -> int:
    if requested == 0 or not _port_is_available(host, requested):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, 0))
            return int(probe.getsockname()[1])
    return requested


def _open_browser_later(url: str) -> None:
    timer = threading.Timer(0.6, webbrowser.open, args=(url,))
    timer.daemon = True
    timer.start()


def main() -> None:
    parser = argparse.ArgumentParser(prog="learning-workbench")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="登记本地课程视频，不复制媒体")
    ingest.add_argument("--source", type=Path, required=True)
    ingest.add_argument("--output", type=Path, default=Path(".local/state/manifest.json"))

    run = sub.add_parser("serve", help="启动本地学习工作区")
    run.add_argument("--state", type=Path, default=Path(".local/state"))
    run.add_argument("--source", type=Path, help="manifest 不存在时自动登记此本地课程目录")
    run.add_argument("--port", type=int, default=8765)

    start = sub.add_parser("start", help="一键启动并打开本地学习工作区")
    start.add_argument("--source", type=Path, required=True, help="本地课程目录")
    start.add_argument("--state", type=Path, default=Path(".local/state"))
    start.add_argument("--port", type=int, default=8765)
    start.add_argument("--no-browser", action="store_true", help="只启动服务，不自动打开浏览器")

    args = parser.parse_args()
    if args.command == "ingest":
        manifest = build_manifest(args.source, args.output)
        print(f"registered {manifest['lesson_count']} lessons -> {args.output}")
        return
    if args.command == "serve":
        manifest_path = args.state / "manifest.json"
        if args.source and not manifest_path.exists():
            build_manifest(args.source, manifest_path)
        serve(args.state, PROJECT_ROOT / "web", port=args.port)
        return
    if args.command == "start":
        manifest_path = args.state / "manifest.json"
        if not manifest_path.exists():
            build_manifest(args.source, manifest_path)
        host = "127.0.0.1"
        port = _choose_port(host, args.port)
        url = f"http://{host}:{port}"
        print(f"Starting Learning Workbench: {url}")
        if not args.no_browser:
            _open_browser_later(url)
        serve(args.state, PROJECT_ROOT / "web", host=host, port=port)


if __name__ == "__main__":
    main()
