"""CSV/TSV 解析（stdlib）。行锚点 + 方言嗅探 + 编码探测。"""

from __future__ import annotations

import csv
import io
from pathlib import Path

PARSER_VERSION = "0.1.0"


def _decode(raw: bytes) -> tuple[str, str, list[str]]:
    notes: list[str] = []
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc, notes
        except UnicodeDecodeError:
            continue
    notes.append("编码无法确认，按 latin-1 兜底")
    return raw.decode("latin-1"), "latin-1", notes


def parse(source_path: Path, *, kind: str, format: str | None, options: dict, limits: dict, work_dir: Path) -> dict:
    raw = source_path.read_bytes()
    text, encoding, notes = _decode(raw)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel_tab if (format or "") == "tsv" else csv.excel
    has_header = False
    try:
        has_header = csv.Sniffer().has_header(sample)
    except csv.Error:
        pass

    max_rows = limits.get("csv_max_rows", 5_000_000)
    reader = csv.reader(io.StringIO(text), dialect)
    elements: list[dict] = []
    header: list[str] | None = None
    truncated = False
    for i, row in enumerate(reader):
        if i >= max_rows:
            truncated = True
            notes.append(f"行数超过上限 {max_rows}，已截断")
            break
        if i == 0 and has_header:
            header = row
            elements.append(
                {
                    "type": "cell_row",
                    "text": " | ".join(row),
                    "locator": {"system": "row", "index": 0},
                    "attrs": {"is_header": True, "cells": row},
                }
            )
            continue
        elements.append(
            {
                "type": "cell_row",
                "text": " | ".join(row),
                "locator": {"system": "row", "index": i if not has_header else i},
                "attrs": {"cells": row, **({"header": header} if header else {})},
            }
        )
    rows_total = len(elements)
    blocks = [
        {"block_type": "section", "index": 0, "label": "元数据", "elements": [
            {"type": "field", "text": f"encoding: {encoding}", "locator": {"system": "field", "name": "encoding"}},
            {"type": "field", "text": f"delimiter: {dialect.delimiter!r}", "locator": {"system": "field", "name": "delimiter"}},
            {"type": "field", "text": f"rows: {rows_total}", "locator": {"system": "field", "name": "rows"}},
        ]},
        {"block_type": "sheet", "index": 0, "label": source_path.name, "elements": elements},
    ]
    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        "text_extraction": "pass" if elements else "warning",
        "layout_preservation": "pass",
        "metadata": "pass",
        "source_anchoring": "pass",
    }
    return {
        "parser_name": "csv_lite",
        "parser_version": PARSER_VERSION,
        "config_hash": f"encoding={encoding};dialect={getattr(dialect, 'delimiter', ',')}",
        "operation": "parse",
        "content": {"blocks": blocks},
        "quality_dims": dims,
        "notes": notes,
        "truncated": truncated,
    }
