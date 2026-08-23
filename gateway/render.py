"""EvidenceDocument → Markdown 导出视图（导出只是视图，JSON 才是模型本体）。

输出纪律：只含 package_relative_path；隐私由 privacy.scrub_text 兜底。
"""

from __future__ import annotations


from .privacy import scrub_text

_DIM_LABEL = {
    "discovery": "发现",
    "type_detection": "类型识别",
    "text_extraction": "文本提取",
    "layout_preservation": "版面保留",
    "ocr": "OCR",
    "attachments": "附件",
    "metadata": "元数据",
    "source_anchoring": "来源锚定",
}

_FITNESS_LABEL = {
    "fulltext_search": "全文检索",
    "fact_extraction": "事实提取",
    "quotation": "可靠引用",
    "layout_sensitive_analysis": "版面敏感分析",
    "authenticity_assessment": "真伪鉴定",
}

_DIM_ICON = {"pass": "✅", "warning": "⚠️", "fail": "❌", "unknown": "❔"}


def render_document_md(doc: dict) -> str:
    lines: list[str] = []
    src = doc["source"]
    q = doc["quality"]
    lines.append(f"# {src['package_relative_path']}")
    lines.append("")
    lines.append(f"- source_id：`{doc['source_id'][:23]}…`")
    lines.append(f"- rendition_id：`{doc['rendition_id'][:23]}…`")
    lines.append(f"- 解析器：{doc['parser']['name']} {doc['parser']['version']}（{doc['parser']['worker_id']}）")
    fell = doc["parser"].get("fell_back_from")
    if fell:
        chain = " → ".join(f"{f['name']}({f['reason']})" for f in fell)
        lines.append(f"- 降级链：{chain} → {doc['parser']['name']}")
    lines.append(f"- 字节：{src.get('bytes', 0)}；类型：{src['detected_type'].get('mime', '?')}（{src['detected_type'].get('method', '?')}）")
    lines.append("")
    lines.append(f"## 质量：{q['overall']}")
    lines.append("")
    lines.append("| 维度 | 状态 |")
    lines.append("|------|------|")
    for dim, label in _DIM_LABEL.items():
        v = q.get(dim, "unknown")
        lines.append(f"| {label} | {_DIM_ICON.get(v, v)} {v} |")
    lines.append("")
    lines.append("| 适用性 | 可用 |")
    lines.append("|--------|------|")
    for key, label in _FITNESS_LABEL.items():
        v = q.get("fitness", {}).get(key, False)
        lines.append(f"| {label} | {'是' if v else '否'} |")
    lines.append("")
    notes = q.get("notes") or []
    if notes:
        lines.append("### 质量备注")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    # 内容块
    lines.append("## 内容")
    lines.append("")
    for block in doc["content"].get("blocks", []):
        _render_block(lines, block)

    ann = doc.get("annotations", {})
    if ann.get("comments"):
        lines.append(f"## 批注（{len(ann['comments'])}）")
        lines.append("")
        for c in ann["comments"]:
            meta = " ".join(x for x in (c.get("author"), c.get("date")) if x)
            anchor = f" → `{c['anchor_element_id']}`" if c.get("anchor_element_id") else ""
            lines.append(f"- {meta}：{c.get('text', '')}{anchor}")
        lines.append("")
    if ann.get("revisions"):
        lines.append(f"## 修订（{len(ann['revisions'])}）")
        lines.append("")
        for r in ann["revisions"]:
            meta = " ".join(x for x in (r.get("author"), r.get("date")) if x)
            lines.append(f"- [{r.get('kind')}] {meta}：{(r.get('text') or '')[:120]}")
        lines.append("")
    if ann.get("embedded_objects"):
        lines.append(f"## 嵌入对象（{len(ann['embedded_objects'])}）")
        lines.append("")
        for o in ann["embedded_objects"]:
            lines.append(f"- {o.get('name')}（{o.get('mime') or '?'}，{o.get('bytes') or 0}B）")
        lines.append("")

    if doc.get("lineage"):
        lines.append("## 处理谱系")
        lines.append("")
        lines.append("| 操作 | 工具 | 版本 | 退出码 | 时间 |")
        lines.append("|------|------|------|--------|------|")
        for e in doc["lineage"]:
            lines.append(f"| {e['operation']} | {e['tool']} | {e['tool_version']} | {e['exit_code']} | {e['finished_at']} |")
        lines.append("")
    return scrub_text("\n".join(lines))


def _render_block(lines: list[str], block: dict) -> None:
    bt = block.get("block_type")
    idx = block.get("index")
    label = block.get("label")
    if bt == "page":
        lines.append(f"### 第 {idx + 1} 页")
    elif bt == "sheet":
        lines.append(f"### 工作表：{label or idx}")
    elif bt == "message_group":
        lines.append(f"### 消息组 {label or idx}")
    elif bt == "segment":
        lines.append(f"### 片段 {label or idx}")
    elif label:
        lines.append(f"### {label}")
    lines.append("")
    for el in block.get("elements", []):
        _render_element(lines, el)
    lines.append("")


def _render_element(lines: list[str], el: dict) -> None:
    t = el.get("type")
    text = el.get("text") or ""
    loc = el.get("locator", {})
    if t in ("heading1", "heading2", "heading3", "heading"):
        level = int(t[-1]) if t[-1].isdigit() else 2
        lines.append(f"{'#' * min(level + 3, 6)} {text}")
        lines.append("")
    elif t == "table":
        rows = (el.get("attrs") or {}).get("rows") or []
        if rows:
            lines.append("| " + " | ".join(str(c) for c in rows[0]) + " |")
            lines.append("|" + "---|" * len(rows[0]))
            for row in rows[1:]:
                lines.append("| " + " | ".join(str(c) for c in row) + " |")
            lines.append("")
        elif text:
            lines.append(text)
            lines.append("")
    elif t == "message":
        attrs = el.get("attrs") or {}
        sender = attrs.get("sender") or "?"
        ts = loc.get("timestamp") or attrs.get("timestamp") or ""
        lines.append(f"> **{sender}**（{ts}）：{text}")
        lines.append("")
    elif t == "segment_text":
        start = loc.get("start", 0)
        end = loc.get("end", 0)
        lines.append(f"[{_fmt_tc(start)} → {_fmt_tc(end)}] {text}")
    elif t == "cell_row":
        lines.append(text)
    elif t == "image_ref":
        lines.append(f"![{text or 'image'}]({loc.get('system', '')})")
    else:
        if text:
            lines.append(text)
            lines.append("")


def _fmt_tc(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


# ---------------- 锚点的人类可读形态（search / anchor / get 共用） ----------------


def format_locator(locator: dict | None) -> str:
    """把 locator 渲染成一句可直接写进引用的定位说明（spec 07）。"""
    loc = locator or {}
    system = loc.get("system")
    if system == "page_bbox":
        page = loc.get("page")
        where = f"第 {page + 1} 页" if isinstance(page, int) else "页码未知"
        bbox = loc.get("bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            where += f" [{', '.join(str(round(float(v), 1)) for v in bbox)}]"
        return where
    if system == "paragraph":
        idx = loc.get("index")
        return f"第 {idx + 1} 段" if isinstance(idx, int) else "段落未知"
    if system == "cell":
        return f"{loc.get('sheet', '?')}!{loc.get('cell', '?')}"
    if system == "row":
        idx = loc.get("index")
        return f"第 {idx + 1} 行" if isinstance(idx, int) else "行未知"
    if system == "message":
        parts = [str(loc[k]) for k in ("id", "timestamp") if loc.get(k) is not None]
        return "消息 " + "/".join(parts) if parts else "消息"
    if system == "timecode":
        return f"{_fmt_tc(float(loc.get('start', 0)))} → {_fmt_tc(float(loc.get('end', 0)))}"
    if system == "entity":
        bits = [f"{k}={loc[k]}" for k in ("layer", "handle") if loc.get(k) is not None]
        return "图元 " + " ".join(bits) if bits else "图元"
    if system == "field":
        return f"字段 {loc.get('name', '?')}"
    if system == "stream":
        offset = loc.get("offset")
        return f"流内偏移 {offset}（近似定位）" if offset is not None else "流内（近似定位）"
    return system or "定位未知"
