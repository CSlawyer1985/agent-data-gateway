#!/usr/bin/env python3
"""pypdf worker adapter — ADG Worker Protocol v0.1。页级文本提取（无坐标）。"""

import json
import sys

WORKER = {"name": "pypdf", "version": "0.1.0", "maturity": "implemented", "license": "BSD-3"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_parse(req):
    from pypdf import PdfReader

    reader = PdfReader(req["source_path"])
    if reader.is_encrypted:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "unsupported", "message": "PDF 加密（需 qpdf worker + 密码）"}}
    blocks = []
    text_chars = 0
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as e:
            text = ""
        blocks.append({
            "block_type": "page", "index": i,
            "elements": [{"type": "paragraph", "text": text.strip(),
                          "locator": {"system": "page_bbox", "page": i}}] if text.strip() else [],
        })
        text_chars += len(text.strip())
    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "pypdf",
            "parser_version": WORKER["version"],
            "coordinate_system": {"name": "page_bbox", "units": "page", "origin": "top-left"},
            "content": {"blocks": blocks},
            "quality_dims": {
                "discovery": "pass", "type_detection": "pass",
                "text_extraction": "pass" if text_chars else "warning",
                "layout_preservation": "warning",
                "metadata": "pass",
                "source_anchoring": "warning",
            },
            "fitness": {"quotation": True, "layout_sensitive_analysis": False},
            "notes": ["pypdf 页级提取：无版面坐标，锚点为页级"],
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
              "handles": [{"kind": "pdf", "role": "parse"}],
              "requirements": {"min_ram_mb": 512, "gpu": "none", "backend": ["subprocess"], "network": False}})
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
