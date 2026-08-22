#!/usr/bin/env python3
"""video_frames worker adapter — ADG Worker Protocol v0.1。

FFmpeg 抽视频关键帧 → JPG（transform）。按时长自适应抽 3-8 帧。
帧回灌走 image 链（rapidocr 读画面文字 / 视觉理解现场内容）。
"""

import json
import subprocess
import sys
from pathlib import Path

WORKER = {"name": "video_frames", "version": "0.1.0", "maturity": "implemented", "license": "LGPL-2.1"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _duration(path: str) -> float:
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, errors="replace", timeout=30,
        )
        return float(p.stdout.strip() or "0")
    except (ValueError, subprocess.TimeoutExpired):
        return 0.0


def handle_parse(req):
    work_dir = Path(req["work_dir"])
    src = req["source_path"]
    options = req.get("options") or {}
    duration = _duration(src)
    if duration <= 0:
        duration = 30  # 探测失败按 30s 计
    # 自适应：每 ~10s 一帧，3-8 帧
    n_frames = options.get("n_frames")
    if n_frames is None:
        n_frames = max(3, min(8, int(duration / 10) + 1))
    interval = max(1.0, duration / n_frames)

    frames_dir = work_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(frames_dir / "frame_%03d.jpg")
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
         "-vf", f"fps=1/{interval:.2f}", "-frames:v", str(n_frames),
         "-q:v", "3", pattern],
        capture_output=True, text=True, errors="replace", timeout=600,
    )
    output_files = []
    if proc.returncode != 0 and not any(frames_dir.glob("*.jpg")):
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "crash", "message": f"ffmpeg 抽帧失败: {(proc.stderr or '')[-300:]}"}}
    for jpg in sorted(frames_dir.glob("*.jpg")):
        output_files.append({"path": f"frames/{jpg.name}", "format": "image/jpeg", "note": f"视频帧（~{interval:.0f}s 间隔）"})
    if not output_files:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "crash", "message": "未抽出任何帧"}}
    return {
        "type": "parse_result",
        "status": "ok",
        "output_files": output_files,
        "warnings": [f"抽出 {len(output_files)} 帧（时长 {duration:.1f}s，间隔 {interval:.1f}s）；画面 OCR/视觉需对照原件核验"],
        "metrics": {"duration": duration, "frame_count": len(output_files)},
    }


def main():
    line = sys.stdin.readline()
    try:
        req = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        send({"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": "协议首行不是合法 JSON"}})
        return
    if req.get("type") == "capabilities_request":
        send({"type": "capabilities", "protocol_version": "0.1", "worker": WORKER,
              "handles": [{"kind": "video", "role": "transform"}],
              "requirements": {"min_ram_mb": 512, "gpu": "none", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
            result = handle_parse(req)
        except FileNotFoundError:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "dependency_missing", "message": "ffmpeg/ffprobe 不在 PATH"}}
        except Exception as e:
            result = {"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": f"{type(e).__name__}: {e}"}}
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed", "error": {"kind": "rejected", "message": "未知消息类型"}})


if __name__ == "__main__":
    main()
