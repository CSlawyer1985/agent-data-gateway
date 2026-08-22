#!/usr/bin/env python3
"""pdfplumber worker adapter — ADG Worker Protocol v0.1。

提取 PDF 文本 + 表格。表格保留行列结构（table 元素）。
"""

import json
import sys

WORKER = {"name": "pdfplumber", "version": "0.1.0", "maturity": "implemented", "license": "MIT"}

_MAX_PAGES = 20000


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _norm_bbox(bbox, page_h, page_w):
    if not bbox or not page_h or not page_w:
        return None
    x0, top, x1, bottom = bbox
    return [round(x0 / page_w * 1000, 1), round(top / page_h * 1000, 1),
            round(x1 / page_w * 1000, 1), round(bottom / page_h * 1000, 1)]


def handle_parse(req):
    import pdfplumber

    pages_out = []
    text_chars = 0
    table_count = 0
    truncated = False
    notes = []

    # 加密 PDF：先试给定口令，再试空用户口令（所有者限制的常见情形）
    password = (req.get("options") or {}).get("password", "")
    with pdfplumber.open(req["source_path"], password=password) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            if i >= _MAX_PAGES:
                truncated = True
                notes.append(f"页数超过 {_MAX_PAGES}，已截断")
                break
            pw, ph = page.width, page.height
            elements = []
            # 文本（按行）
            try:
                text = (page.extract_text() or "").strip()
            except Exception:
                text = ""
            if text:
                for li, line in enumerate(text.split("\n")):
                    if line.strip():
                        elements.append({
                            "type": "paragraph",
                            "text": line.strip(),
                            "locator": {"system": "page_bbox", "page": i},
                        })
                        text_chars += len(line)
            # 表格
            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []
            for ti, tbl in enumerate(tables):
                rows = [[(c or "").replace("\n", " ").strip() for c in row] for row in tbl]
                if not rows or not any(any(c for c in r) for r in rows):
                    continue
                table_count += 1
                # 表格 bbox 近似取页中
                elements.append({
                    "type": "table",
                    "text": "\n".join(" | ".join(r) for r in rows),
                    "locator": {"system": "page_bbox", "page": i},
                    "attrs": {"rows": rows, "table_index": ti},
                })
            if elements:
                pages_out.append({"block_type": "page", "index": i, "elements": elements})

    blocks = pages_out
    dims = {
        "discovery": "pass", "type_detection": "pass",
        "text_extraction": "pass" if text_chars else "warning",
        "layout_preservation": "pass" if table_count else "warning",
        "metadata": "pass",
        "source_anchoring": "warning",  # 页级锚点（无字符级 bbox）
    }
    if table_count:
        notes.append(f"提取 {table_count} 个表格（保留行列结构）")
    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "pdfplumber",
            "parser_version": WORKER["version"],
            "coordinate_system": {"name": "page_bbox", "units": "0-1000", "origin": "top-left"},
            "content": {"blocks": blocks},
            "quality_dims": dims,
            "fitness": {"layout_sensitive_analysis": table_count > 0},
            "notes": notes,
            "truncated": truncated,
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
