#!/usr/bin/env python3
"""extract-msg worker adapter — ADG Worker Protocol v0.1。Outlook .msg → 邮件 fragment。"""

import json
import re
import sys
from pathlib import Path

WORKER = {"name": "extract_msg", "version": "0.1.0", "maturity": "implemented", "license": "GPL-3.0"}

_SAFE = re.compile(r"[^\w.\-一-鿿]+")


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _safe(name: str, fallback: str) -> str:
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip().strip(".")
    name = _SAFE.sub("_", name)
    return name[:180] or fallback


def handle_parse(req):
    import extract_msg

    work_dir = Path(req["work_dir"])
    msg = extract_msg.Message(req["source_path"])

    meta = []
    for name, value in [
        ("from", msg.sender), ("to", msg.to), ("cc", msg.cc),
        ("subject", msg.subject), ("date", msg.date),
        ("message_id", msg.messageId),
    ]:
        if value:
            meta.append({"type": "field", "text": f"{name}: {value}", "locator": {"system": "field", "name": name}})

    body_elements = []
    idx = 0
    body = msg.body or ""
    for chunk in re.split(r"\n\s*\n", body):
        chunk = chunk.strip()
        if chunk:
            body_elements.append({"type": "paragraph", "text": chunk, "locator": {"system": "paragraph", "index": idx}})
            idx += 1

    attachments = []
    att_elements = []
    for i, att in enumerate(msg.attachments, 1):
        filename = _safe(att.longFilename or att.shortFilename or f"attachment-{i}.bin", f"attachment-{i}.bin")
        data = att.data or b""
        stored = f"att_{i}_{filename}"
        (work_dir / stored).write_bytes(data)
        attachments.append({"name": filename, "path": stored})
        att_elements.append({
            "type": "attachment_ref", "text": filename,
            "locator": {"system": "field", "name": f"attachment_{i}"},
            "attrs": {"bytes": len(data)},
        })

    blocks = [
        {"block_type": "section", "index": 0, "label": "邮件头", "elements": meta},
        {"block_type": "section", "index": 1, "label": "正文", "elements": body_elements},
    ]
    if att_elements:
        blocks.append({"block_type": "attachment_list", "index": 2, "label": "附件清单", "elements": att_elements})
    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "extract_msg",
            "parser_version": WORKER["version"],
            "content": {"blocks": blocks},
            "quality_dims": {
                "discovery": "pass", "type_detection": "pass",
                "text_extraction": "pass" if body_elements else "warning",
                "layout_preservation": "warning",
                "attachments": "pass",
                "metadata": "pass" if meta else "warning",
                "source_anchoring": "pass",
            },
            "notes": [],
            "truncated": False,
            "attachments": attachments,
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
              "handles": [{"kind": "email", "formats": ["msg"], "role": "parse"}],
              "requirements": {"min_ram_mb": 1024, "gpu": "none", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
            result = handle_parse(req)
        except ImportError as e:
            result = {"type": "parse_result", "status": "failed", "error": {"kind": "dependency_missing", "message": str(e)}}
        except Exception as e:
            result = {"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": f"{type(e).__name__}: {e}"}}
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed", "error": {"kind": "rejected", "message": "未知消息类型"}})


if __name__ == "__main__":
    main()
