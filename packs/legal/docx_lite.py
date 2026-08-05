"""DOCX 解析（stdlib）：正文段落 + 表格 + 批注/修订多轨 + 页眉页脚 + 嵌入对象。

法律场景关键能力：word/comments.xml 批注、w:ins/w:del 修订轨道、宏检测。
锚点：paragraph index（文档顺序块级序号）；批注/修订经 _anchor_locator 挂到段落。
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from gateway.security import detect_office_macros

PARSER_VERSION = "0.1.0"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
DC = "{http://purl.org/dc/elements/1.1/}"
DCT = "{http://purl.org/dc/terms/}"

_HEADING_STYLES = {"Heading1": 1, "Heading2": 2, "Heading3": 3, "1": 1, "2": 2, "3": 3}


def _text_of_paragraph(p: ET.Element) -> str:
    """段落可见文本：w:t（含 w:ins 内），排除 w:delText（删除轨道）。"""
    parts: list[str] = []
    for node in p.iter():
        if node.tag == f"{W}t":
            parts.append(node.text or "")
        elif node.tag == f"{W}tab":
            parts.append("\t")
        elif node.tag == f"{W}br":
            parts.append("\n")
    return "".join(parts)


def _paragraph_style(p: ET.Element) -> str | None:
    ppr = p.find(f"{W}pPr")
    if ppr is None:
        return None
    ps = ppr.find(f"{W}pStyle")
    if ps is None:
        return None
    return ps.get(f"{W}val")


def _style_to_heading(style: str | None) -> int | None:
    if not style:
        return None
    if style in _HEADING_STYLES:
        return _HEADING_STYLES[style]
    low = style.lower()
    if low.startswith("heading") and low[7:].isdigit():
        return min(int(low[7:]), 3)
    return None


def _table_rows(tbl: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in tbl.findall(f"{W}tr"):
        cells: list[str] = []
        for tc in tr.findall(f"{W}tc"):
            cell_text = "\n".join(_text_of_paragraph(p) for p in tc.findall(f"{W}p"))
            cells.append(cell_text.strip())
        rows.append(cells)
    return rows


def _core_props(zf: zipfile.ZipFile) -> list[dict]:
    fields: list[dict] = []
    try:
        root = ET.fromstring(zf.read("docProps/core.xml"))
    except (KeyError, ET.ParseError):
        return fields
    mapping = [
        (f"{DC}creator", "author"),
        (f"{DC}lastModifiedBy", "last_modified_by"),
        (f"{DC}title", "title"),
        (f"{DC}subject", "subject"),
        (f"{DCT}created", "created"),
        (f"{DCT}modified", "modified"),
        (f"{CP}revision", "revision"),
    ]
    for tag, name in mapping:
        el = root.find(tag)
        if el is not None and el.text:
            fields.append({"type": "field", "text": f"{name}: {el.text}", "locator": {"system": "field", "name": name}})
    return fields


def _parse_comments(zf: zipfile.ZipFile, para_index_of_comment: dict[str, int]) -> list[dict]:
    try:
        root = ET.fromstring(zf.read("word/comments.xml"))
    except (KeyError, ET.ParseError):
        return []
    comments: list[dict] = []
    for c in root.findall(f"{W}comment"):
        cid = c.get(f"{W}id", "")
        text = "".join(t.text or "" for t in c.iter(f"{W}t")).strip()
        entry: dict = {
            "id": cid,
            "author": c.get(f"{W}author"),
            "date": c.get(f"{W}date"),
            "text": text,
            "resolved": None,
            "thread": None,
        }
        idx = para_index_of_comment.get(cid)
        if idx is not None:
            entry["_anchor_locator"] = {"system": "paragraph", "index": idx}
        comments.append(entry)
    return comments


def parse(source_path: Path, *, kind: str, format: str | None, options: dict, limits: dict, work_dir: Path) -> dict:
    notes: list[str] = []
    truncated = False
    max_paras = limits.get("docx_max_paragraphs", 500_000)
    try:
        zf = zipfile.ZipFile(source_path)
    except zipfile.BadZipFile:
        from gateway.errors import WorkerFailed

        raise WorkerFailed("DOCX 不是合法 zip（文件损坏）", kind="crash")
    with zf:
        names = zf.namelist()
        if detect_office_macros(names):
            notes.append("检测到宏（vbaProject.bin）——活动内容不执行，仅标记")
        try:
            doc_root = ET.fromstring(zf.read("word/document.xml"))
        except (KeyError, ET.ParseError) as e:
            from gateway.errors import WorkerFailed

            raise WorkerFailed(f"document.xml 无法解析: {e}", kind="crash")

        body = doc_root.find(f"{W}body")
        elements: list[dict] = []
        para_index_of_comment: dict[str, int] = {}
        revisions: list[dict] = []
        rev_seq = 0
        idx = 0  # 文档顺序块级序号
        if body is not None:
            for child in body:
                if idx >= max_paras:
                    truncated = True
                    notes.append(f"段落数超过上限 {max_paras}，已截断")
                    break
                if child.tag == f"{W}p":
                    # 批注锚点收集
                    for ref in child.iter(f"{W}commentRangeStart"):
                        cid = ref.get(f"{W}id")
                        if cid is not None:
                            para_index_of_comment.setdefault(cid, idx)
                    # 修订轨道
                    for ins in child.iter(f"{W}ins"):
                        rev_text = "".join(t.text or "" for t in ins.iter(f"{W}t")).strip()
                        revisions.append(
                            {
                                "id": f"ins-{rev_seq}",
                                "kind": "insert",
                                "author": ins.get(f"{W}author"),
                                "date": ins.get(f"{W}date"),
                                "text": rev_text,
                                "_anchor_locator": {"system": "paragraph", "index": idx},
                            }
                        )
                        rev_seq += 1
                    for dele in child.iter(f"{W}del"):
                        rev_text = "".join(t.text or "" for t in dele.iter(f"{W}delText")).strip()
                        revisions.append(
                            {
                                "id": f"del-{rev_seq}",
                                "kind": "delete",
                                "author": dele.get(f"{W}author"),
                                "date": dele.get(f"{W}date"),
                                "text": rev_text,
                                "_anchor_locator": {"system": "paragraph", "index": idx},
                            }
                        )
                        rev_seq += 1
                    text = _text_of_paragraph(child)
                    style = _paragraph_style(child)
                    level = _style_to_heading(style)
                    etype = f"heading{level}" if level else "paragraph"
                    if text.strip() or level:
                        elements.append(
                            {
                                "type": etype,
                                "text": text,
                                "locator": {"system": "paragraph", "index": idx},
                                **({"attrs": {"style": style}} if style else {}),
                            }
                        )
                    idx += 1
                elif child.tag == f"{W}tbl":
                    rows = _table_rows(child)
                    elements.append(
                        {
                            "type": "table",
                            "text": "\n".join(" | ".join(r) for r in rows),
                            "locator": {"system": "paragraph", "index": idx},
                            "attrs": {"rows": rows},
                        }
                    )
                    idx += 1

        comments = _parse_comments(zf, para_index_of_comment)

        # 页眉页脚
        header_footer_elements: list[dict] = []
        hf_idx = 0
        for name in sorted(names):
            if name.startswith(("word/header", "word/footer")) and name.endswith(".xml"):
                try:
                    root = ET.fromstring(zf.read(name))
                except ET.ParseError:
                    continue
                for p in root.iter(f"{W}p"):
                    t = _text_of_paragraph(p)
                    if t.strip():
                        header_footer_elements.append(
                            {
                                "type": "paragraph",
                                "text": t,
                                "locator": {"system": "paragraph", "index": hf_idx},
                                "attrs": {"part": name},
                            }
                        )
                        hf_idx += 1

        # 嵌入对象与媒体
        embedded: list[dict] = []
        for name in names:
            if name.startswith("word/embeddings/"):
                info = zf.getinfo(name)
                embedded.append({"name": name.rsplit("/", 1)[-1], "mime": None, "bytes": info.file_size, "child_source_id": None})
        media_count = sum(1 for n in names if n.startswith("word/media/"))
        if media_count:
            notes.append(f"含 {media_count} 个图片/媒体文件（未 OCR，列入清单）")
            embedded.append({"name": f"word/media/*（{media_count} 个）", "mime": "image/*", "bytes": None, "child_source_id": None})
        meta_fields = _core_props(zf)

    blocks: list[dict] = []
    if meta_fields:
        blocks.append({"block_type": "section", "index": 0, "label": "元数据", "elements": meta_fields})
    blocks.append({"block_type": "section", "index": len(blocks), "label": "正文", "elements": elements})
    if header_footer_elements:
        blocks.append({"block_type": "header", "index": len(blocks), "label": "页眉页脚", "elements": header_footer_elements})

    text_found = any((el.get("text") or "").strip() for el in elements)
    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        "text_extraction": "pass" if text_found else "warning",
        "layout_preservation": "pass",
        "attachments": "pass" if not embedded else "pass",
        "metadata": "pass" if meta_fields else "warning",
        "source_anchoring": "pass",
    }
    if any("宏" in n for n in notes):
        dims["metadata"] = "warning"
    return {
        "parser_name": "docx_lite",
        "parser_version": PARSER_VERSION,
        "operation": "parse",
        "content": {"blocks": blocks},
        "annotations": {"comments": comments, "revisions": revisions, "embedded_objects": embedded},
        "quality_dims": dims,
        "fitness": {"layout_sensitive_analysis": True},
        "notes": notes,
        "truncated": truncated,
    }
