#!/usr/bin/env python3
"""officecli worker adapter — ADG Worker Protocol v0.1。

officecli query <file> comment/revision + view <file> text → docx 多轨 fragment。
officecli 输出格式未锁定：本适配器尽力解析 JSON/文本两种形态，失败则报 error。
"""

import json
import subprocess
import sys

WORKER = {"name": "officecli", "version": "0.1.0", "maturity": "experimental", "license": "Apache-2.0"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _run(args, timeout=300):
    proc = subprocess.run(["officecli"] + args, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def _try_json(text):
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # 兼容 ```json 包裹
    if text.startswith("```"):
        inner = "\n".join(text.split("\n")[1:-1])
        try:
            return json.loads(inner)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def handle_parse(req):
    src = req["source_path"]
    warnings: list[str] = []

    rc, out, err = _run(["view", src, "text"])
    if rc != 0:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "crash", "message": f"officecli view 失败: {err[-300:]}"}}
    paragraphs = [p for p in out.split("\n\n") if p.strip()]
    elements = [
        {"type": "paragraph", "text": p.strip(), "locator": {"system": "paragraph", "index": i}}
        for i, p in enumerate(paragraphs)
    ]

    comments, revisions = [], []
    rc, out, err = _run(["query", src, "comment"])
    if rc == 0:
        data = _try_json(out)
        if isinstance(data, list):
            for i, c in enumerate(data):
                comments.append({
                    "id": str(c.get("id", i)),
                    "author": c.get("author"),
                    "date": c.get("date"),
                    "text": c.get("text") or c.get("content") or "",
                    "resolved": c.get("done"),
                    "thread": None,
                })
        elif out.strip():
            warnings.append("officecli comment 输出非 JSON，未能结构化（请校准 adapter）")
    else:
        warnings.append(f"officecli query comment 失败: {err[-150:]}")

    rc, out, err = _run(["query", src, "revision"])
    if rc == 0:
        data = _try_json(out)
        if isinstance(data, list):
            for i, r in enumerate(data):
                revisions.append({
                    "id": str(r.get("id", i)),
                    "kind": {"ins": "insert", "del": "delete"}.get(str(r.get("type", "")).lower(), "other"),
                    "author": r.get("author"),
                    "date": r.get("date"),
                    "text": r.get("text") or "",
                })
        elif out.strip():
            warnings.append("officecli revision 输出非 JSON，未能结构化（请校准 adapter）")

    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "officecli",
            "parser_version": WORKER["version"],
            "coordinate_system": {"name": "paragraph", "units": "index"},
            "content": {"blocks": [{"block_type": "section", "index": 0, "label": "正文", "elements": elements}]},
            "annotations": {"comments": comments, "revisions": revisions},
            "quality_dims": {
                "discovery": "pass", "type_detection": "pass",
                "text_extraction": "pass" if elements else "warning",
                "layout_preservation": "warning",
                "metadata": "pass",
                "source_anchoring": "pass",
            },
            "notes": [f"批注 {len(comments)} 条；修订 {len(revisions)} 条"],
            "warnings": warnings,
            "truncated": False,
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
              "handles": [{"kind": "docx", "role": "parse"}],
              "requirements": {"min_ram_mb": 1024, "gpu": "none", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
            result = handle_parse(req)
        except FileNotFoundError:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "dependency_missing", "message": "officecli 不在 PATH（brew install officecli）"}}
        except Exception as e:
            result = {"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": f"{type(e).__name__}: {e}"}}
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed", "error": {"kind": "rejected", "message": "未知消息类型"}})


if __name__ == "__main__":
    main()
