"""XLSX 解析（stdlib）：工作表 + 单元格锚点（sheet + A1）+ 共享字符串 + 公式标注。"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

PARSER_VERSION = "0.1.0"

SS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
DC = "{http://purl.org/dc/elements/1.1/}"
DCT = "{http://purl.org/dc/terms/}"

_CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except (KeyError, ET.ParseError):
        return []
    out: list[str] = []
    for si in root.findall(f"{SS}si"):
        out.append("".join(t.text or "" for t in si.iter(f"{SS}t")))
    return out


def _sheets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """[(sheet_name, worksheet_part_path)]"""
    try:
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    except (KeyError, ET.ParseError):
        return []
    rid_to_target: dict[str, str] = {}
    for rel in rels.findall(f"{PR}Relationship"):
        rid_to_target[rel.get("Id", "")] = rel.get("Target", "")
    sheets: list[tuple[str, str]] = []
    for sh in wb.iter(f"{SS}sheet"):
        name = sh.get("name", "")
        rid = sh.get(f"{R}id", "")
        target = rid_to_target.get(rid, "")
        if target and not target.startswith("xl/"):
            target = "xl/" + target.lstrip("/")
        if name and target:
            sheets.append((name, target))
    return sheets


def _cell_value(c: ET.Element, shared: list[str]) -> str:
    t = c.get("t")
    if t == "s":
        v = c.find(f"{SS}v")
        if v is not None and v.text is not None:
            try:
                return shared[int(v.text)]
            except (ValueError, IndexError):
                return v.text
        return ""
    if t == "inlineStr":
        return "".join(x.text or "" for x in c.iter(f"{SS}t"))
    if t == "str":
        v = c.find(f"{SS}v")
        return v.text if v is not None and v.text else ""
    if t == "b":
        v = c.find(f"{SS}v")
        return "TRUE" if (v is not None and v.text == "1") else "FALSE"
    v = c.find(f"{SS}v")
    return v.text if v is not None and v.text is not None else ""


def _core_props(zf: zipfile.ZipFile) -> list[dict]:
    fields: list[dict] = []
    try:
        root = ET.fromstring(zf.read("docProps/core.xml"))
    except (KeyError, ET.ParseError):
        return fields
    for tag, name in [(f"{DC}creator", "author"), (f"{DC}lastModifiedBy", "last_modified_by"), (f"{DCT}created", "created"), (f"{DCT}modified", "modified")]:
        el = root.find(tag)
        if el is not None and el.text:
            fields.append({"type": "field", "text": f"{name}: {el.text}", "locator": {"system": "field", "name": name}})
    return fields


def parse(source_path: Path, *, kind: str, format: str | None, options: dict, limits: dict, work_dir: Path) -> dict:
    notes: list[str] = []
    truncated = False
    max_cells = limits.get("spreadsheet_max_cells", 2_000_000)
    try:
        zf = zipfile.ZipFile(source_path)
    except zipfile.BadZipFile:
        from gateway.errors import WorkerFailed

        raise WorkerFailed("XLSX 不是合法 zip（文件损坏）", kind="crash")
    with zf:
        names = zf.namelist()
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            notes.append("检测到宏（vbaProject.bin）——活动内容不执行，仅标记")
        shared = _shared_strings(zf)
        sheets = _sheets(zf)
        if not sheets:
            from gateway.errors import WorkerFailed

            raise WorkerFailed("workbook.xml 无法解析或无工作表", kind="crash")
        blocks: list[dict] = []
        meta = _core_props(zf)
        if meta:
            blocks.append({"block_type": "section", "index": 0, "label": "元数据", "elements": meta})
        cells_total = 0
        for si, (sheet_name, target) in enumerate(sheets):
            try:
                root = ET.fromstring(zf.read(target))
            except (KeyError, ET.ParseError) as e:
                notes.append(f"工作表 {sheet_name} 解析失败: {e}")
                continue
            elements: list[dict] = []
            data = root.find(f"{SS}sheetData")
            if data is not None:
                for row in data.findall(f"{SS}row"):
                    row_cells: list[dict] = []
                    display: list[str] = []
                    for c in row.findall(f"{SS}c"):
                        ref = c.get("r", "")
                        val = _cell_value(c, shared)
                        f = c.find(f"{SS}f")
                        entry: dict = {"cell": ref, "v": val}
                        if f is not None and f.text:
                            entry["formula"] = f.text
                        row_cells.append(entry)
                        display.append(val)
                        cells_total += 1
                        if cells_total > max_cells:
                            truncated = True
                            break
                    if truncated:
                        notes.append(f"单元格数超过上限 {max_cells}，已截断")
                        break
                    if not row_cells:
                        continue
                    first_ref = row_cells[0]["cell"] or "A1"
                    elements.append(
                        {
                            "type": "cell_row",
                            "text": " | ".join(display),
                            "locator": {"system": "cell", "sheet": sheet_name, "cell": first_ref},
                            "attrs": {"cells": row_cells},
                        }
                    )
                if truncated:
                    break
            blocks.append({"block_type": "sheet", "index": si + (1 if meta else 0), "label": sheet_name, "elements": elements})

    text_found = any((el.get("text") or "").strip() for b in blocks for el in b["elements"])
    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        "text_extraction": "pass" if text_found else "warning",
        "layout_preservation": "pass",
        "metadata": "pass" if meta else "warning",
        "source_anchoring": "pass",
    }
    return {
        "parser_name": "xlsx_lite",
        "parser_version": PARSER_VERSION,
        "operation": "parse",
        "content": {"blocks": blocks},
        "quality_dims": dims,
        "fitness": {"layout_sensitive_analysis": True},
        "notes": notes,
        "truncated": truncated,
    }
