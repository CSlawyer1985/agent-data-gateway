"""文档视觉模型 worker 的保守结果映射。

生成式 OCR 可以恢复阅读顺序、表格和公式，但不能凭模型输出宣称像素级锚点或可直接
用于真伪判断。本模块把各模型的输出收敛到同一个 ADG fragment 契约。
"""

from __future__ import annotations

import hashlib
import html
import json
import re

_HTML_IMAGE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[^>]+>")
_MARKDOWN_MARKUP = re.compile(r"[#*_>`~\[\]()]")


def config_fingerprint(config: dict) -> str:
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def markdown_fragment(*, parser_name: str, parser_version: str, pages: list[str], notes: list[str]) -> dict:
    """把每页 Markdown 映射为页级可追溯 fragment。"""
    blocks = []
    searchable_text = []
    image_placeholders = 0
    for page_index, markdown in enumerate(pages):
        text = (markdown or "").strip()
        image_placeholders += len(_HTML_IMAGE.findall(text)) + len(_MARKDOWN_IMAGE.findall(text))
        plain = _HTML_IMAGE.sub(" ", text)
        plain = _MARKDOWN_IMAGE.sub(" ", plain)
        plain = _HTML_TAG.sub(" ", plain)
        plain = html.unescape(_MARKDOWN_MARKUP.sub(" ", plain)).strip()
        if plain:
            searchable_text.append(plain)
        elements = []
        if text:
            elements.append(
                {
                    "type": "paragraph",
                    "text": text,
                    "locator": {"system": "page_bbox", "page": page_index},
                    "attrs": {"format": "markdown"},
                }
            )
        blocks.append({"block_type": "page", "index": page_index, "elements": elements})
    has_text = bool(searchable_text)
    text_quality = "pass" if has_text and image_placeholders == 0 else "warning"
    if image_placeholders:
        notes = notes + [f"输出含 {image_placeholders} 个未转写图片占位符；不能视为正文提取完整"]
    return _fragment(
        parser_name=parser_name,
        parser_version=parser_version,
        blocks=blocks,
        has_text=has_text,
        text_quality=text_quality,
        layout_quality="warning",
        source_anchoring="pass",
        notes=notes + ["生成式 OCR 有损；关键引用、表格与公式须对照原件核验"],
    )


def _fragment(
    *, parser_name: str, parser_version: str, blocks: list[dict], has_text: bool,
    text_quality: str, layout_quality: str, source_anchoring: str, notes: list[str]
) -> dict:
    return {
        "parser_name": parser_name,
        "parser_version": parser_version,
        "operation": "ocr",
        "coordinate_system": {"name": "page_bbox", "units": "0-1000", "origin": "top-left"},
        "content": {"blocks": blocks},
        "quality_dims": {
            "discovery": "pass",
            "type_detection": "pass",
            "text_extraction": text_quality,
            "layout_preservation": layout_quality,
            "ocr": text_quality,
            "metadata": "pass",
            "source_anchoring": source_anchoring,
        },
        "fitness": {
            "fulltext_search": has_text,
            "fact_extraction": has_text and text_quality == "pass",
            "quotation": False,
            "layout_sensitive_analysis": layout_quality == "pass",
        },
        "notes": notes,
        "truncated": False,
    }
