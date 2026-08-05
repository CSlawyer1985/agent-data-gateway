#!/usr/bin/env python3
"""py7zr worker adapter — ADG Worker Protocol v0.1。7z 安全展开（transform）。

编排器在 work_dir（= 目标展开目录）内接收成员清单，安全参数由编排器二次审查。
"""

import json
import sys
from pathlib import Path, PurePosixPath

WORKER = {"name": "py7zr", "version": "0.1.0", "maturity": "implemented", "license": "LGPL-2.1"}

MAX_TOTAL = 50 * 1024 * 1024 * 1024


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _unsafe(name: str) -> str | None:
    norm = name.replace("\\", "/")
    if norm.startswith("/") or "\x00" in name:
        return "绝对路径/非法名"
    if ".." in PurePosixPath(norm).parts:
        return "路径穿越"
    return None


def handle_parse(req):
    import py7zr

    work_dir = Path(req["work_dir"])
    rejected = []
    members = []
    try:
        with py7zr.SevenZipFile(req["source_path"], "r") as zf:
            infos = zf.list()
            total = 0
            targets = []
            for info in infos:
                name = info.filename
                reason = _unsafe(name)
                if reason:
                    rejected.append({"name": name, "reason": reason})
                    continue
                if info.is_directory:
                    continue
                if getattr(info, "compressed", 1) and info.uncompressed / max(1, info.compressed) > 200:
                    rejected.append({"name": name, "reason": "压缩比异常"})
                    continue
                total += info.uncompressed
                if total > MAX_TOTAL:
                    rejected.append({"name": name, "reason": "超出累计解压上限"})
                    continue
                targets.append(name)
            if targets:
                zf.extract(path=str(work_dir), targets=targets)
            for name in targets:
                p = work_dir / name
                if p.is_file():
                    members.append(name)
    except py7zr.exceptions.PasswordRequired:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "unsupported", "message": "7z 加密（需要密码）"}}
    except Exception as e:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "crash", "message": f"{type(e).__name__}: {e}"}}
    return {
        "type": "parse_result",
        "status": "ok",
        "output_files": [{"path": m, "format": "file"} for m in members],
        "fragment": {"notes": [f"7z 展开 {len(members)} 个成员；拒绝 {len(rejected)} 个"], "rejected": rejected},
        "warnings": [f"拒绝成员 {len(rejected)} 个"] if rejected else [],
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
              "handles": [{"kind": "archive", "formats": ["7z"], "role": "transform"}],
              "requirements": {"min_ram_mb": 1024, "gpu": "none", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        result = handle_parse(req)
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed", "error": {"kind": "rejected", "message": "未知消息类型"}})


if __name__ == "__main__":
    main()
