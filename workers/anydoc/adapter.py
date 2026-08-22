#!/usr/bin/env python3
"""anydoc worker adapter — ADG Worker Protocol v0.1。

Firecrawl anydoc（Rust，MIT）：14 种办公格式 → 结构化文档模型。
定位见 worker.json notes：主要补齐本项目此前无 parser 的格式（RTF/EPUB/ODS/ODP/…），
并在 lite parser 之后充当零系统依赖的第二意见。

锚点：paragraph index（文档顺序块级序号），表格行用 row index。
诚实边界：anydoc 是 Markdown 级结构，没有页码与坐标，所以 layout_preservation 恒 warning。
"""

import json
import sys

WORKER = {"name": "anydoc", "version": "0.1.0", "maturity": "implemented", "license": "MIT"}

MAX_ELEMENTS = 20000


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _inline_text(inlines) -> str:
    """递归拼接 Inline 树的可见文本。"""
    out = []
    for node in inlines or []:
        text = getattr(node, "text", None)
        if text:
            out.append(text)
        child = getattr(node, "content", None)
        if child:
            out.append(_inline_text(child))
    return "".join(out)


def _block_text(block) -> str:
    text = getattr(block, "text", None)
    if text:
        return text
    return _inline_text(getattr(block, "content", None))


def _table_rows(table) -> list[list[str]]:
    """grid → 二维文本。合并单元格：span 槽位留空，origin 槽位出内容。"""
    rows: list[list[str]] = []
    for slot_row in getattr(table, "grid", None) or []:
        row: list[str] = []
        for slot in slot_row:
            if getattr(slot, "kind", None) != "origin":
                row.append("")
                continue
            cell = getattr(slot, "cell", None)
            parts = [_block_text(b) for b in (getattr(cell, "blocks", None) or [])]
            row.append(" ".join(p for p in parts if p).strip())
        rows.append(row)
    return rows


def _elements_from_blocks(blocks, elements, index_ref, depth=0):
    """anydoc Block 树 → ADG 元素列表（就地追加）。"""
    for block in blocks or []:
        if len(elements) >= MAX_ELEMENTS:
            return True
        kind = getattr(block, "kind", "paragraph")
        idx = index_ref[0]

        table = getattr(block, "table", None)
        if table is not None:
            rows = _table_rows(table)
            flat = "\n".join(" | ".join(c for c in row) for row in rows)
            elements.append({
                "type": "table", "text": flat,
                "locator": {"system": "paragraph", "index": idx},
                "attrs": {"rows": rows, "header_rows": getattr(table, "header_rows", 0)},
            })
            index_ref[0] += 1
            for r, row in enumerate(rows):
                if not any(c.strip() for c in row):
                    continue
                elements.append({
                    "type": "cell_row", "text": " | ".join(row),
                    "locator": {"system": "row", "index": r},
                    "attrs": {"table_at_paragraph": idx},
                })
            continue

        nested = getattr(block, "blocks", None)
        if nested:
            if _elements_from_blocks(nested, elements, index_ref, depth + 1):
                return True
            continue

        text = _block_text(block).strip()
        if not text:
            continue
        if kind == "heading":
            level = getattr(block, "level", None) or 1
            el_type = f"heading{min(int(level), 3)}"
        else:
            el_type = "paragraph"
        attrs = {}
        if kind not in ("paragraph", "heading"):
            attrs["style"] = kind  # list / block_quote / literal 等原样保留
        el = {"type": el_type, "text": text, "locator": {"system": "paragraph", "index": idx}}
        if attrs:
            el["attrs"] = attrs
        elements.append(el)
        index_ref[0] += 1
    return False


def handle_parse(req):
    import anydoc

    with open(req["source_path"], "rb") as f:
        data = f.read()
    fmt = (req.get("format") or "").lstrip(".") or None
    try:
        doc = anydoc.to_document(data)
    except TypeError:
        doc = anydoc.to_document(data, fmt) if fmt else None
    except anydoc.EncryptedError as e:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "unsupported", "message": f"文件加密，anydoc 无法读取: {e}"}}
    except anydoc.UnsupportedError as e:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "unsupported", "message": f"anydoc 不支持该输入: {e}"}}
    if doc is None:
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "unsupported", "message": "anydoc 无法确定输入格式"}}

    elements: list[dict] = []
    truncated = _elements_from_blocks(doc.blocks, elements, [0])
    notes = ["anydoc 结构化提取：Markdown 级结构，无页码与坐标，锚点为段落/行序号"]
    assets = list(getattr(doc, "assets", None) or [])
    if assets:
        notes.append(f"文档内含 {len(assets)} 个嵌入资源（图片等），本 worker 不导出，其中文字需 OCR 轨补充")
    if truncated:
        notes.append(f"元素数超过上限 {MAX_ELEMENTS}，已截断")

    text_chars = sum(len(e.get("text") or "") for e in elements)
    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        "text_extraction": "pass" if text_chars else "warning",
        "layout_preservation": "warning",
        "attachments": "warning" if assets else "pass",
        "metadata": "unknown",
        "source_anchoring": "pass",
    }
    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "anydoc",
            "parser_version": WORKER["version"],
            "operation": "parse",
            "coordinate_system": {"name": "paragraph", "units": "index", "origin": "document-order"},
            "content": {"blocks": [{"block_type": "section", "index": 0, "label": "正文", "elements": elements}]},
            "quality_dims": dims,
            "fitness": {"layout_sensitive_analysis": False},
            "notes": notes,
            "truncated": truncated,
        },
        "metrics": {"elements": len(elements), "text_chars": text_chars, "assets": len(assets)},
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
              "handles": [{"kind": k, "role": "parse"} for k in
                          ("doc", "docx", "xls", "xlsx", "presentation", "csv")],
              "requirements": {"min_ram_mb": 512, "gpu": "none", "backend": ["subprocess"], "network": False}})
        return
    if req.get("type") == "parse_request":
        try:
            result = handle_parse(req)
        except ImportError:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "dependency_missing", "message": "firecrawl-anydoc 未安装（adg setup --worker anydoc）"}}
        except Exception as e:
            result = {"type": "parse_result", "status": "failed",
                      "error": {"kind": "crash", "message": f"{type(e).__name__}: {e}"}}
        result["job_id"] = req.get("job_id")
        send(result)
        return
    send({"type": "parse_result", "job_id": req.get("job_id"), "status": "failed",
          "error": {"kind": "rejected", "message": "未知消息类型"}})


if __name__ == "__main__":
    main()
