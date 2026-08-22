#!/usr/bin/env python3
"""tika worker adapter — ADG Worker Protocol v0.1（enrich，实验性）。

需要 ADG_TIKA_JAR 环境变量指向 tika-server JAR；本适配器以 CLI 模式调 Tika
（不开 server，避免常驻进程与端口）。
"""

import json
import os
import subprocess
import sys

WORKER = {"name": "tika", "version": "0.1.0", "maturity": "experimental", "license": "Apache-2.0"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_parse(req):
    jar = os.environ.get("ADG_TIKA_JAR")
    if not jar or not os.path.exists(jar):
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "dependency_missing", "message": "ADG_TIKA_JAR 未设置或不存在"}}
    proc = subprocess.run(
        ["java", "-jar", jar, "--metadata", req["source_path"]],
        capture_output=True, text=True, errors="replace", timeout=300,
    )
    if proc.returncode != 0:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "crash", "message": f"tika 退出码 {proc.returncode}: {(proc.stderr or '')[-300:]}"}}
    fields = []
    for i, line in enumerate(proc.stdout.splitlines()):
        if ":" in line:
            name, _, value = line.partition(":")
            name = name.strip()
            if name:
                fields.append({"type": "field", "text": f"{name}: {value.strip()}", "locator": {"system": "field", "name": name[:64]}})
    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "tika",
            "parser_version": WORKER["version"],
            "operation": "enrich",
            "content": {"blocks": [{"block_type": "section", "index": 0, "label": "Tika 元数据", "elements": fields}]},
            "quality_dims": {"metadata": "pass"},
            "notes": [],
        },
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
              "handles": [{"kind": "unknown", "role": "enrich"}],
              "requirements": {"min_ram_mb": 2048, "gpu": "none", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        result = handle_parse(req)
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed", "error": {"kind": "rejected", "message": "未知消息类型"}})


if __name__ == "__main__":
    main()
