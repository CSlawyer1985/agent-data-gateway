#!/usr/bin/env python3
"""qpdf worker adapter — ADG Worker Protocol v0.1。加密 PDF 解密（transform）。"""

import json
import subprocess
import sys
from pathlib import Path

WORKER = {"name": "qpdf", "version": "0.1.0", "maturity": "implemented", "license": "Apache-2.0"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_parse(req):
    options = req.get("options") or {}
    password = options.get("password")
    work_dir = Path(req["work_dir"])
    src = Path(req["source_path"])
    out = work_dir / (src.stem + ".decrypted.pdf")

    cmd = ["qpdf", "--decrypt"]
    if password:
        cmd.append(f"--password={password}")
    cmd += [str(src), str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=600)
    if proc.returncode != 0 or not out.exists():
        err = (proc.stderr or "")[-300:]
        if not password and ("password" in err.lower() or "invalid password" in err.lower()):
            return {"type": "parse_result", "status": "failed",
                    "error": {"kind": "unsupported", "message": "PDF 加密：需要密码。用法: adg parse --worker-option password=XXX（密码只进 worker，不写入任何产物）"}}
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "crash", "message": f"qpdf 退出码 {proc.returncode}: {err}"}}
    return {
        "type": "parse_result",
        "status": "ok",
        "output_files": [{"path": out.name, "format": "pdf", "note": "qpdf 解密副本（原加密文件保持不变）"}],
        "warnings": [],
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
              "handles": [{"kind": "pdf", "role": "transform"}],
              "requirements": {"min_ram_mb": 512, "gpu": "none", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
            result = handle_parse(req)
        except FileNotFoundError:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "dependency_missing", "message": "qpdf 不在 PATH（brew install qpdf）"}}
        except Exception as e:
            result = {"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": f"{type(e).__name__}: {e}"}}
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed", "error": {"kind": "rejected", "message": "未知消息类型"}})


if __name__ == "__main__":
    main()
