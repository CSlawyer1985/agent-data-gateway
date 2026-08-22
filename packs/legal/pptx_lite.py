"""PPTX 解析（stdlib）：幻灯片文本 + 演讲者备注 + 元数据 + 媒体清单。

pptx 与 docx 同为 OOXML（zip + drawingml/presentationml）。lite parser 仅提取
幻灯片文本流与备注，不保留图形坐标/动画/版面位置（layout_sensitive_analysis=False）。
slide 顺序按 presentation.xml 的 sldIdLst + rels 精确解析（失败降级为按文件名编号）。
旧 .ppt（CFB 二进制）非 zip，触发 BadZipFile → raise，由编排器沿降级链转 docling。
锚点：全局 paragraph index；所属幻灯片页码保存在 attrs.slide（从 1 起）。
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from gateway.security import detect_office_macros

PARSER_VERSION = "0.1.0"

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKGREL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
DC = "{http://purl.org/dc/elements/1.1/}"
DCT = "{http://purl.org/dc/terms/}"


def _slide_num(path: str) -> int:
    m = re.search(r"slide(\d+)\.xml$", path)
    return int(m.group(1)) if m else 0


def _slides_by_name(names: list[str]) -> list[str]:
    """降级排序：按 slide 文件名数字。"""
    found = [n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
    return sorted(found, key=_slide_num)


def _slide_order(zf: zipfile.ZipFile, names: list[str]) -> list[str]:
    """幻灯片有序列表：presentation.xml 的 sldIdLst 中 r:id 顺序 + rels 映射到 slide 文件。
    异常或缺失时降级为按文件名排序。"""
    try:
        root = ET.fromstring(zf.read("ppt/presentation.xml"))
    except (KeyError, ET.ParseError):
        return _slides_by_name(names)
    rids: list[str] = []
    sld_lst = root.find(f"{P}sldIdLst")
    if sld_lst is not None:
        for sld in sld_lst.findall(f"{P}sldId"):
            rid = sld.get(f"{R}id")
            if rid:
                rids.append(rid)
    if not rids:
        return _slides_by_name(names)
    try:
        rels = ET.fromstring(zf.read("ppt/_rels/presentation.xml.rels"))
    except (KeyError, ET.ParseError):
        return _slides_by_name(names)
    rid_target = {rel.get("Id"): rel.get("Target") for rel in rels.findall(f"{PKGREL}Relationship")}
    name_set = set(names)
    ordered: list[str] = []
    for rid in rids:
        target = rid_target.get(rid)
        if not target:
            continue
        path = target if target.startswith("ppt/") else f"ppt/{target}"
        if path in name_set:
            ordered.append(path)
    return ordered if ordered else _slides_by_name(names)


def _paragraphs(root: ET.Element) -> list[str]:
    """drawingml：按 <a:p> 段落分组，拼接 <a:t>，保留 <a:br> 换行。"""
    paras: list[str] = []
    for p in root.iter(f"{A}p"):
        parts: list[str] = []
        for node in p.iter():
            if node.tag == f"{A}t":
                parts.append(node.text or "")
            elif node.tag == f"{A}br":
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paras.append(text)
    return paras


def _core_props(zf: zipfile.ZipFile) -> list[dict]:
    try:
        root = ET.fromstring(zf.read("docProps/core.xml"))
    except (KeyError, ET.ParseError):
        return []
    mapping = [
        (f"{DC}creator", "author"),
        (f"{DC}lastModifiedBy", "last_modified_by"),
        (f"{DC}title", "title"),
        (f"{DC}subject", "subject"),
        (f"{DCT}created", "created"),
        (f"{DCT}modified", "modified"),
        (f"{CP}revision", "revision"),
    ]
    fields: list[dict] = []
    for tag, name in mapping:
        el = root.find(tag)
        if el is not None and el.text:
            fields.append(
                {"type": "field", "text": f"{name}: {el.text}", "locator": {"system": "field", "name": name}}
            )
    return fields


def parse(source_path: Path, *, kind: str, format: str | None, options: dict, limits: dict, work_dir: Path) -> dict:
    notes: list[str] = []
    try:
        zf = zipfile.ZipFile(source_path)
    except zipfile.BadZipFile:
        from gateway.errors import WorkerFailed

        raise WorkerFailed("PPTX 不是合法 zip（旧 .ppt 二进制或文件损坏）", kind="crash")
    with zf:
        names = zf.namelist()
        if detect_office_macros(names):
            notes.append("检测到宏（vbaProject.bin）——活动内容不执行，仅标记")
        slide_paths = _slide_order(zf, names)
        if not slide_paths:
            from gateway.errors import WorkerFailed

            raise WorkerFailed("pptx 内未找到幻灯片（ppt/slides/*.xml）", kind="crash")

        meta_fields = _core_props(zf)
        blocks: list[dict] = []
        if meta_fields:
            blocks.append({"block_type": "section", "index": 0, "label": "元数据", "elements": meta_fields})

        total_chars = 0
        paragraph_idx = 0
        for page_idx, sp in enumerate(slide_paths, start=1):
            try:
                sroot = ET.fromstring(zf.read(sp))
            except ET.ParseError:
                notes.append(f"slide {page_idx} XML 解析失败，跳过")
                continue
            paras = _paragraphs(sroot)
            total_chars += sum(len(t) for t in paras)
            elements = []
            for text in paras:
                elements.append(
                    {
                        "type": "paragraph",
                        "text": text,
                        "locator": {"system": "paragraph", "index": paragraph_idx},
                        "attrs": {"slide": page_idx},
                    }
                )
                paragraph_idx += 1
            blocks.append({"block_type": "page", "index": page_idx, "elements": elements})

        # 演讲者备注
        notes_slides: list[dict] = []
        ns_paths = sorted(
            [n for n in names if n.startswith("ppt/notesSlides/notesSlide") and n.endswith(".xml")],
            key=_slide_num,
        )
        for nsp in ns_paths:
            try:
                nroot = ET.fromstring(zf.read(nsp))
            except ET.ParseError:
                continue
            np = _paragraphs(nroot)
            if np:
                notes_slides.append({"page": _slide_num(nsp), "text": "\n".join(np)})

        # 媒体
        media = [n for n in names if n.startswith("ppt/media/")]
        embedded: list[dict] = []
        if media:
            notes.append(f"含 {len(media)} 个图片/媒体（未 OCR，列入清单）")
            embedded.append(
                {"name": f"ppt/media/*（{len(media)} 个）", "mime": "image/*", "bytes": None, "child_source_id": None}
            )

    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        "text_extraction": "pass" if total_chars else "warning",
        "layout_preservation": "warning",  # 文本流提取，丢失图形/动画/坐标
        "attachments": "pass",
        "metadata": "pass" if meta_fields else "warning",
        "source_anchoring": "pass",
    }
    if any("宏" in n for n in notes):
        dims["metadata"] = "warning"

    annotations: dict = {}
    if notes_slides:
        annotations["notes_slides"] = notes_slides
    if embedded:
        annotations["embedded_objects"] = embedded

    return {
        "parser_name": "pptx_lite",
        "parser_version": PARSER_VERSION,
        "operation": "parse",
        "content": {"blocks": blocks},
        "annotations": annotations,
        "quality_dims": dims,
        "fitness": {"layout_sensitive_analysis": False},
        "notes": notes + ["pptx_lite 提取幻灯片文本流，不保留图形/动画/版面坐标"],
        "truncated": False,
    }
