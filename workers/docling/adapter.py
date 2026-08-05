#!/usr/bin/env python3
"""docling worker adapter — ADG Worker Protocol v0.1。

docling DocumentConverter → EvidenceDocument fragment。
bbox 归一化到 0-1000 左上原点坐标系（docling 原生为 PDF pt 左下原点）。
"""

import json
import sys

WORKER = {"name": "docling", "version": "0.1.0", "maturity": "implemented", "license": "MIT"}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _norm_bbox(bbox, page_h, page_w):
    """docling bbox(pt, 原点在 coord_origin 字段声明) → 0-1000 左上。"""
    if not bbox or not page_h or not page_w:
        return None
    l, t, r, b = bbox.get("l"), bbox.get("t"), bbox.get("r"), bbox.get("b")
    if None in (l, t, r, b):
        return None
    if bbox.get("coord_origin") == "BOTTOMLEFT":
        top = page_h - t
        bottom = page_h - b
        t, b = min(top, bottom), max(top, bottom)
    return [round(l / page_w * 1000, 1), round(t / page_h * 1000, 1), round(r / page_w * 1000, 1), round(b / page_h * 1000, 1)]


def handle_parse(req):
    from docling.document_converter import DocumentConverter

    conv = DocumentConverter()
    result = conv.convert(req["source_path"])
    doc = result.document
    d = doc.export_to_dict()

    pages_meta = {}
    for pno, pinfo in (d.get("pages") or {}).items():
        size = (pinfo or {}).get("size") or {}
        pages_meta[int(pno) - 1] = (size.get("width"), size.get("height"))

    pages: dict[int, list] = {}
    label_map = {"section_header": "heading2", "title": "heading1", "caption": "paragraph", "footnote": "paragraph", "list_item": "paragraph", "formula": "paragraph", "code": "paragraph"}
    for t in d.get("texts") or []:
        prov = (t.get("prov") or [{}])[0]
        page = int(prov.get("page_no", 1)) - 1
        pw, ph = pages_meta.get(page, (None, None))
        bbox = _norm_bbox(prov.get("bbox"), ph, pw)
        label = (t.get("label") or "").lower()
        etype = label_map.get(label, "paragraph")
        loc = {"system": "page_bbox", "page": page}
        if bbox:
            loc["bbox"] = bbox
        el = {"type": etype, "text": t.get("text") or "", "locator": loc}
        pages.setdefault(page, []).append(el)

    for tbl in d.get("tables") or []:
        prov = (tbl.get("prov") or [{}])[0]
        page = int(prov.get("page_no", 1)) - 1
        cells = (tbl.get("data") or {}).get("table_cells") or []
        grid: dict[tuple, str] = {}
        max_r = max_c = -1
        for c in cells:
            r, cc = c.get("start_row_offset_idx", 0), c.get("start_col_offset_idx", 0)
            grid[(r, cc)] = c.get("text") or ""
            max_r, max_c = max(max_r, r), max(max_c, cc)
        rows = [[grid.get((r, c), "") for c in range(max_c + 1)] for r in range(max_r + 1)]
        pw, ph = pages_meta.get(page, (None, None))
        bbox = _norm_bbox(prov.get("bbox"), ph, pw)
        loc = {"system": "page_bbox", "page": page}
        if bbox:
            loc["bbox"] = bbox
        pages.setdefault(page, []).append(
            {"type": "table", "text": "\n".join(" | ".join(row) for row in rows), "locator": loc, "attrs": {"rows": rows}}
        )

    blocks = [
        {"block_type": "page", "index": page, "elements": elements}
        for page, elements in sorted(pages.items())
    ]
    text_chars = sum(len(el.get("text") or "") for b in blocks for el in b["elements"])
    notes = []
    if not pages_meta:
        notes.append("docling 未返回页面尺寸，bbox 未归一化（保留原生坐标）")
    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "docling",
            "parser_version": WORKER["version"],
            "coordinate_system": {"name": "page_bbox", "units": "0-1000", "origin": "top-left"},
            "content": {"blocks": blocks},
            "quality_dims": {
                "discovery": "pass",
                "type_detection": "pass",
                "text_extraction": "pass" if text_chars else "warning",
                "layout_preservation": "pass" if pages_meta else "warning",
                "metadata": "pass",
                "source_anchoring": "pass" if pages_meta else "warning",
            },
            "fitness": {"layout_sensitive_analysis": bool(pages_meta)},
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
    rtype = req.get("type")
    if rtype == "capabilities_request":
        send({
            "type": "capabilities",
            "protocol_version": "0.1",
            "worker": WORKER,
            "handles": [{"kind": k, "role": "parse"} for k in ("pdf", "docx", "doc", "xlsx", "xls", "presentation", "html")],
            "requirements": {"min_ram_mb": 4096, "gpu": "optional", "backend": ["subprocess"], "network": False},
        })
        return
    if rtype == "parse_request":
        try:
            result = handle_parse(req)
        except ImportError as e:
            result = {"type": "parse_result", "status": "failed", "error": {"kind": "dependency_missing", "message": str(e)}}
        except Exception as e:
            result = {"type": "parse_result", "status": "failed", "error": {"kind": "crash", "message": f"{type(e).__name__}: {e}"}}
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed", "error": {"kind": "rejected", "message": f"未知消息类型 {rtype}"}})


if __name__ == "__main__":
    main()
