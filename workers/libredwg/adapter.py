#!/usr/bin/env python3
"""libredwg worker adapter — ADG Worker Protocol v0.1。dwg2dxf 转换（transform）。"""

import json
import subprocess
import sys
from pathlib import Path

WORKER = {"name": "libredwg", "version": "0.1.0", "maturity": "experimental", "license": "GPL-3.0"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_parse(req):
    work_dir = Path(req["work_dir"])
    src = Path(req["source_path"])
    out = work_dir / (src.stem + ".dxf")
    proc = subprocess.run(
        ["dwg2dxf", "-o", str(out), str(src)],
        capture_output=True, text=True, errors="replace", timeout=1800,
    )
    if proc.returncode != 0 or not out.exists():
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "crash", "message": f"dwg2dxf 退出码 {proc.returncode}: {(proc.stderr or '')[-300:]}"}}
    return {
        "type": "parse_result",
        "status": "ok",
        "output_files": [{"path": out.name, "format": "dxf", "note": "LibreDWG 转换，几何可能不完整"}],
        "warnings": ["dwg→dxf 转换有损风险：重要图纸需人工复核"],
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
              "handles": [{"kind": "dwg", "role": "transform"}],
              "requirements": {"min_ram_mb": 1024, "gpu": "none", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
            result = handle_parse(req)
        except FileNotFoundError:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "dependency_missing", "message": "dwg2dxf 不在 PATH（安装 LibreDWG）"}}
        except Exception as e:
            result = {"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": f"{type(e).__name__}: {e}"}}
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed", "error": {"kind": "rejected", "message": "未知消息类型"}})


if __name__ == "__main__":
    main()
