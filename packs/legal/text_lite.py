"""txt/md 解析（stdlib）。段落锚点 + Markdown 标题结构。"""

from __future__ import annotations

import re
from pathlib import Path

PARSER_VERSION = "0.1.0"


def _decode(raw: bytes) -> tuple[str, str, list[str]]:
    notes: list[str] = []
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc, notes
        except UnicodeDecodeError:
            continue
    notes.append("编码无法确认为 UTF-8/GB18030，按 latin-1 兜底（非 ASCII 可能失真）")
    return raw.decode("latin-1"), "latin-1", notes


def parse(source_path: Path, *, kind: str, format: str | None, options: dict, limits: dict, work_dir: Path) -> dict:
    raw = source_path.read_bytes()
    notes: list[str] = []
    truncated = False
    max_bytes = limits.get("text_max_bytes", 512 * 1024 * 1024)
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
        truncated = True
        notes.append(f"文件超过文本上限 {max_bytes}B，已截断")
    text, encoding, enc_notes = _decode(raw)
    notes.extend(enc_notes)
    is_md = (format or "") == "markdown" or source_path.suffix.lower() == ".md"

    elements: list[dict] = []
    idx = 0
    for chunk in re.split(r"\n\s*\n", text):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        etype = "paragraph"
        if is_md:
            m = re.match(r"^(#{1,6})\s+(.*)$", chunk)
            if m:
                level = len(m.group(1))
                etype = f"heading{min(level, 3)}"
                chunk = m.group(2).strip()
        elements.append(
            {
                "type": etype,
                "text": chunk,
                "locator": {"system": "paragraph", "index": idx},
            }
        )
        idx += 1

    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        "text_extraction": "pass" if text.strip() else "warning",
        "layout_preservation": "pass",
        "metadata": "pass",
        "source_anchoring": "pass",
    }
    meta_elements = [
        {"type": "field", "text": f"encoding: {encoding}", "locator": {"system": "field", "name": "encoding"}},
    ]
    blocks = [
        {"block_type": "section", "index": 0, "label": "元数据", "elements": meta_elements},
        {"block_type": "section", "index": 1, "label": "正文", "elements": elements},
    ]
    if not text.strip():
        notes.append("空文件或无可见文本")
    return {
        "parser_name": "text_lite",
        "parser_version": PARSER_VERSION,
        "config_hash": f"encoding={encoding};md={is_md}",
        "operation": "parse",
        "content": {"blocks": blocks},
        "quality_dims": dims,
        "notes": notes,
        "truncated": truncated,
    }
