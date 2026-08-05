#!/usr/bin/env python3
"""xls worker adapter — ADG Worker Protocol v0.1。

xlrd 读取老 .xls（BIFF），单元格锚点（sheet + A1）。
"""

import json
import sys

WORKER = {"name": "xls", "version": "0.1.0", "maturity": "implemented", "license": "BSD-3"}

_MAX_CELLS = 2_000_000


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _col_letter(idx: int) -> str:
    s = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def _cell_ref(r: int, c: int) -> str:
    return f"{_col_letter(c)}{r + 1}"


def handle_parse(req):
    import xlrd

    try:
        book = xlrd.open_workbook(req["source_path"], on_demand=True, formatting_info=False)
    except xlrd.XLRDError as e:
        msg = str(e)
        if "password" in msg.lower() or "encrypt" in msg.lower():
            return {"type": "parse_result", "status": "failed",
                    "error": {"kind": "unsupported", "message": f"xls 加密: {msg}"}}
        return {"type": "parse_result", "status": "failed",
                "error": {"kind": "crash", "message": f"xlrd 解析失败: {msg}"}}

    blocks = []
    cells_total = 0
    truncated = False
    notes = []
    meta_fields = [
        {"type": "field", "text": f"sheets: {book.nsheets}", "locator": {"system": "field", "name": "sheets"}},
    ]
    for si in range(book.nsheets):
        sheet = book.sheet_by_index(si)
        if sheet.nrows == 0:
            continue
        elements = []
        for r in range(sheet.nrows):
            if cells_total >= _MAX_CELLS:
                truncated = True
                notes.append(f"单元格数超过 {_MAX_CELLS}，已截断")
                break
            row_cells = []
            display = []
            for c in range(sheet.ncols):
                try:
                    val = sheet.cell_value(r, c)
                except IndexError:
                    continue
                ctype = sheet.cell_type(r, c)
                if ctype == 3:  # XL_CELL_DATE
                    entry = {"cell": _cell_ref(r, c), "v": str(val), "is_date": True}
                elif ctype == 2 and isinstance(val, float) and val.is_integer():
                    entry = {"cell": _cell_ref(r, c), "v": str(int(val))}
                else:
                    entry = {"cell": _cell_ref(r, c), "v": "" if val == "" else str(val)}
                if ctype == 4:  # boolean
                    entry["v"] = "TRUE" if val else "FALSE"
                row_cells.append(entry)
                display.append(entry["v"])
                cells_total += 1
            if not row_cells:
                continue
            elements.append({
                "type": "cell_row",
                "text": " | ".join(display),
                "locator": {"system": "cell", "sheet": sheet.name, "cell": _cell_ref(r, 0)},
                "attrs": {"cells": row_cells},
            })
        blocks.append({"block_type": "sheet", "index": si + 1, "label": sheet.name, "elements": elements})
    book.release_resources()

    blocks.insert(0, {"block_type": "section", "index": 0, "label": "元数据", "elements": meta_fields})
    text_found = any((el.get("text") or "").strip() for b in blocks for el in b["elements"])
    return {
        "type": "parse_result",
        "status": "ok",
        "fragment": {
            "parser_name": "xls",
            "parser_version": WORKER["version"],
            "coordinate_system": {"name": "cell", "units": "A1"},
            "content": {"blocks": blocks},
            "quality_dims": {
                "discovery": "pass", "type_detection": "pass",
                "text_extraction": "pass" if text_found else "warning",
                "layout_preservation": "pass",
                "metadata": "pass",
                "source_anchoring": "pass",
            },
            "fitness": {"layout_sensitive_analysis": True},
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
              "handles": [{"kind": "xls", "role": "parse"}],
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
