#!/usr/bin/env python3
"""trafilatura worker adapter — ADG Worker Protocol v0.1。HTML 正文提取首选。"""

import json
import re
import sys

WORKER = {"name": "trafilatura", "version": "0.1.0", "maturity": "implemented", "license": "Apache-2.0"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_parse(req):
    import trafilatura

    raw = open(req["source_path"], "rb").read()
    for enc in ("utf-8", "gb18030"):
        try:
            html_text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        html_text = raw.decode("latin-1")

    extracted = trafilatura.extract(html_text, include_tables=True, include_comments=False, favor_precision=True)
    meta = trafilatura.extract_metadata(html_text)

    meta_fields = []
    if meta:
        for key in ("title", "author", "date", "sitename", "url"):
            value = getattr(meta, key, None)
            if value:
                meta_fields.append({"type": "field", "text": f"{key}: {value}", "locator": {"system": "field", "name": key}})

    elements = []
    if extracted:
        for i, chunk in enumerate(re.split(r"\n\s*\n", extracted)):
            chunk = chunk.strip()
            if chunk:
                elements.append({"type": "paragraph", "text": chunk, "locator": {"system": "paragraph", "index": i}})

    notes = []
    if not elements:
        notes.append("trafilatura 未提取到正文（可能为 JS 渲染页面或纯导航页）")
    blocks = [
        {"block_type": "section", "index": 0, "label": "元数据", "elements": meta_fields},
        {"block_type": "section", "index": 1, "label": "正文", "elements": elements},
    ]
    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "trafilatura",
            "parser_version": WORKER["version"],
            "content": {"blocks": blocks},
            "quality_dims": {
                "discovery": "pass", "type_detection": "pass",
                "text_extraction": "pass" if elements else "warning",
                "layout_preservation": "warning",
                "metadata": "pass" if meta_fields else "warning",
                "source_anchoring": "warning",
            },
            "notes": notes,
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
              "handles": [{"kind": "html", "role": "parse"}],
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
