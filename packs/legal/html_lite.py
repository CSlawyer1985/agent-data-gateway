"""HTML 正文提取（stdlib 兜底；首选 trafilatura worker）。

剥离 script/style，按块级标签分段，保留标题层级与链接文本。
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

PARSER_VERSION = "0.1.0"

_BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "li", "tr", "table", "ul", "ol", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr", "title"}
_SKIP_TAGS = {"script", "style", "noscript", "template"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[tuple[str, str]] = []  # (tag, text)
        self._buf: list[str] = []
        self._cur_tag = "p"
        self._skip_depth = 0
        self.title = ""
        self._in_title = False

    def _flush(self):
        text = "".join(self._buf).strip()
        self._buf = []
        if text:
            self.parts.append((self._cur_tag, text))

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS:
            self._flush()
            self._cur_tag = tag

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self._flush()
            self._cur_tag = "p"

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data.strip()
            return
        self._buf.append(data)

    def finish(self):
        self._flush()
        return self.parts


def parse(source_path: Path, *, kind: str, format: str | None, options: dict, limits: dict, work_dir: Path) -> dict:
    raw = source_path.read_bytes()
    notes: list[str] = ["stdlib 兜底提取正文，未做 boilerplate 去除；trafilatura worker 可用时质量更佳"]
    truncated = False
    max_bytes = limits.get("html_max_bytes", 128 * 1024 * 1024)
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
        truncated = True
        notes.append(f"HTML 超过上限 {max_bytes}B，已截断")
    for enc in ("utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("latin-1")
        notes.append("编码按 latin-1 兜底")
    ext = _TextExtractor()
    try:
        ext.feed(text)
    except Exception as e:
        notes.append(f"HTML 解析部分失败（容错继续）: {type(e).__name__}")
    parts = ext.finish()

    elements: list[dict] = []
    for i, (tag, txt) in enumerate(parts):
        etype = "paragraph"
        if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
            etype = f"heading{min(int(tag[1]), 3)}"
        elements.append({"type": etype, "text": txt, "locator": {"system": "paragraph", "index": i}})

    meta_elements = []
    if ext.title:
        meta_elements.append({"type": "field", "text": f"title: {ext.title}", "locator": {"system": "field", "name": "title"}})
    blocks = [
        {"block_type": "section", "index": 0, "label": "元数据", "elements": meta_elements},
        {"block_type": "section", "index": 1, "label": "正文", "elements": elements},
    ]
    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        "text_extraction": "pass" if elements else "warning",
        "layout_preservation": "warning",
        "metadata": "pass" if ext.title else "warning",
        "source_anchoring": "warning",
    }
    return {
        "parser_name": "html_lite",
        "parser_version": PARSER_VERSION,
        "operation": "parse",
        "content": {"blocks": blocks},
        "quality_dims": dims,
        "notes": notes,
        "truncated": truncated,
    }
