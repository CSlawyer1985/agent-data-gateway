#!/usr/bin/env python3
"""libpff worker adapter — ADG Worker Protocol v0.1。pffexport → eml 产物（transform）。"""

import json
import subprocess
import sys
from pathlib import Path

WORKER = {"name": "libpff", "version": "0.1.0", "maturity": "experimental", "license": "LGPL-3.0"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_parse(req):
    work_dir = Path(req["work_dir"])
    out_dir = work_dir / "pff-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["pffexport", "-f", "eml", "-t", str(out_dir), req["source_path"]],
        capture_output=True, text=True, errors="replace", timeout=3600,
    )
    if proc.returncode != 0:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "crash", "message": f"pffexport 退出码 {proc.returncode}: {(proc.stderr or '')[-300:]}"}}
    emls = sorted(p for p in out_dir.rglob("*.eml"))
    if not emls:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "crash", "message": "pffexport 未产出 eml（pst 可能加密或为空）"}}
    output_files = [{"path": str(p.relative_to(work_dir)), "format": "eml"} for p in emls]
    return {
        "type": "parse_result",
        "status": "ok",
        "output_files": output_files,
        "warnings": [f"导出 {len(output_files)} 封 eml，将回灌为独立 item"],
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
              "handles": [{"kind": "email", "formats": ["pst", "ost"], "role": "transform"}],
              "requirements": {"min_ram_mb": 2048, "gpu": "none", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
            result = handle_parse(req)
        except FileNotFoundError:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "dependency_missing", "message": "pffexport 不在 PATH（安装 libpff）"}}
        except Exception as e:
            result = {"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": f"{type(e).__name__}: {e}"}}
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed", "error": {"kind": "rejected", "message": "未知消息类型"}})


if __name__ == "__main__":
    main()
