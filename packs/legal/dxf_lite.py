"""DXF 解析（stdlib 兜底）：图层、范围、实体计数、TEXT/MTEXT 文本、句柄锚点。

首选 ezdxf worker（完整几何）；本模块提供无依赖的图层/文本/锚点骨架。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

PARSER_VERSION = "0.1.0"

_TEXT_ENTITIES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}
_MAX_ENTITIES = 100_000


def _iter_pairs(text: str):
    """DXF 是 组码/值 行对。"""
    lines = text.split("\n")
    i = 0
    n = len(lines)
    while i + 1 < n:
        code = lines[i].strip()
        value = lines[i + 1].rstrip("\r")
        i += 2
        if code.isdigit():
            yield int(code), value


def parse(source_path: Path, *, kind: str, format: str | None, options: dict, limits: dict, work_dir: Path) -> dict:
    raw = source_path.read_bytes()
    notes: list[str] = ["dxf_lite 兜底：仅图层/范围/实体骨架与文本，完整几何请用 ezdxf worker"]
    for enc in ("utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("latin-1")
        notes.append("编码按 latin-1 兜底")

    layers: list[str] = []
    extents: dict[str, tuple[float, ...]] = {}
    entity_counts: Counter[str] = Counter()
    text_elements: list[dict] = []
    truncated = False

    in_tables = False
    in_entities = False
    cur_entity: str | None = None
    cur_handle: str | None = None
    cur_layer: str | None = None
    cur_text: str | None = None
    header_var: str | None = None
    extmin: list[float] = []
    extmax: list[float] = []
    entity_seq = 0

    def flush_entity():
        nonlocal cur_entity, cur_handle, cur_layer, cur_text, entity_seq
        if cur_entity:
            entity_counts[cur_entity] += 1
            if cur_entity in _TEXT_ENTITIES and cur_text and cur_text.strip():
                text_elements.append(
                    {
                        "type": "entity",
                        "text": cur_text.strip(),
                        "locator": {"system": "entity", "handle": cur_handle, "layer": cur_layer},
                        "attrs": {"entity_type": cur_entity},
                    }
                )
            entity_seq += 1
        cur_entity = None
        cur_handle = None
        cur_layer = None
        cur_text = None

    for code, value in _iter_pairs(text):
        if code == 0:
            v = value.strip().upper()
            if v == "SECTION":
                flush_entity()
            elif v == "ENDSEC":
                in_tables = False
                in_entities = False
                header_var = None
                flush_entity()
            elif v == "TABLE":
                in_tables = True
            elif v == "ENDTAB":
                in_tables = False
            elif v == "ENTITIES":
                in_entities = True
            elif in_entities:
                flush_entity()
                cur_entity = v
                if entity_seq >= _MAX_ENTITIES:
                    truncated = True
                    notes.append(f"实体数超过 {_MAX_ENTITIES}，文本提取截断")
                    break
            continue
        if code == 2 and in_tables and value.strip().upper() == "LAYER":
            pass  # LAYER 表开始
        elif code == 2 and in_tables and value and value.strip() not in ("LAYER",) and len(layers) < 512:
            # 粗糙：TABLES 段内 code 2 的值为表项名（含图层名）；过滤明显非图层的
            if value.strip() and not value.startswith("*") and value.upper() not in {"APPID", "DIMSTYLE", "LTYPE", "STYLE", "UCS", "VIEW", "VPORT", "BLOCK_RECORD"}:
                if value not in layers:
                    layers.append(value)
        if not in_entities:
            if code == 9:
                header_var = value.strip()
                continue
            if header_var == "$EXTMIN" and code in (10, 20, 30):
                try:
                    extmin.append(float(value))
                except ValueError:
                    pass
                if len(extmin) == 3:
                    extents["extmin"] = tuple(extmin)
            elif header_var == "$EXTMAX" and code in (10, 20, 30):
                try:
                    extmax.append(float(value))
                except ValueError:
                    pass
                if len(extmax) == 3:
                    extents["extmax"] = tuple(extmax)
            continue
        # ENTITIES 段
        if cur_entity:
            if code == 5 and cur_handle is None:
                cur_handle = value.strip()
            elif code == 8 and cur_layer is None:
                cur_layer = value.strip()
            elif code == 1 and cur_text is None:
                cur_text = value
    flush_entity()

    meta_elements: list[dict] = []
    for key, val in extents.items():
        meta_elements.append(
            {"type": "field", "text": f"{key}: ({val[0]:.3f}, {val[1]:.3f}, {val[2]:.3f})", "locator": {"system": "field", "name": key}}
        )
    meta_elements.append({"type": "field", "text": f"entities_total: {sum(entity_counts.values())}", "locator": {"system": "field", "name": "entities_total"}})

    layer_elements = [
        {"type": "field", "text": layer, "locator": {"system": "field", "name": f"layer_{i}"}}
        for i, layer in enumerate(layers)
    ]
    count_elements = [
        {"type": "field", "text": f"{etype}: {count}", "locator": {"system": "field", "name": f"entity_{etype}"}}
        for etype, count in entity_counts.most_common(40)
    ]

    blocks: list[dict] = [
        {"block_type": "section", "index": 0, "label": "图纸信息", "elements": meta_elements},
        {"block_type": "section", "index": 1, "label": f"图层（{len(layers)}）", "elements": layer_elements},
        {"block_type": "section", "index": 2, "label": "实体统计", "elements": count_elements},
        {"block_type": "section", "index": 3, "label": f"文本实体（{len(text_elements)}）", "elements": text_elements},
    ]
    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        "text_extraction": "pass" if text_elements else "warning",
        "layout_preservation": "warning",
        "metadata": "warning",
        "source_anchoring": "pass",
    }
    return {
        "parser_name": "dxf_lite",
        "parser_version": PARSER_VERSION,
        "operation": "parse",
        "content": {"blocks": blocks},
        "quality_dims": dims,
        "notes": notes,
        "truncated": truncated,
    }
