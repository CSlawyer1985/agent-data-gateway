"""PDF 预检 + stdlib 兜底提取（pdflite）。

preflight_pdf：加密检测、文字层采样、图像占比 → 路由分类（spec 08）。
parse：无 worker 时的最后兜底——解压内容流提取文本操作符字符串。
诚实标注：无版面坐标（layout_preservation=fail）、锚点近似（stream 级）、
CID 字体（常见于中文 PDF）文本可能为乱码。
"""

from __future__ import annotations

import re
import zlib
from pathlib import Path

from gateway.security import detect_pdf_encrypted, detect_pdf_javascript

PARSER_VERSION = "0.1.0"

_RE_PAGE = re.compile(rb"/Type\s*/Page(?![s\w])")
_RE_STREAM = re.compile(rb"stream\r?\n")
_RE_BT_ET = re.compile(rb"BT(.*?)ET", re.S)
_RE_TJ_STR = re.compile(rb"\((?:\\.|[^\\()])*\)\s*Tj")
_RE_TJ_ARR = re.compile(rb"\[(?:\\.|[^\\\]])*\]\s*TJ")
_RE_PDF_STR = re.compile(rb"\((?:\\.|[^\\()])*\)")

_MAX_STREAMS = 2000
_MAX_DECOMPRESSED = 64 * 1024 * 1024


def _decompress_streams(raw: bytes, *, max_streams: int, max_total: int) -> list[bytes]:
    """提取并解压 FlateDecode 流（尽力而为，坏流跳过）。"""
    out: list[bytes] = []
    total = 0
    pos = 0
    while len(out) < max_streams and total < max_total:
        m = _RE_STREAM.search(raw, pos)
        if not m:
            break
        # 前置字典窗口内必须声明 FlateDecode
        dict_start = raw.rfind(b"<<", max(0, m.start() - 4096), m.start())
        header = raw[dict_start : m.start()] if dict_start >= 0 else raw[max(0, m.start() - 4096) : m.start()]
        end = raw.find(b"endstream", m.end())
        if end < 0:
            break
        stream_data = raw[m.end() : end]
        if stream_data.endswith(b"\r\n"):
            stream_data = stream_data[:-2]
        elif stream_data.endswith(b"\n"):
            stream_data = stream_data[:-1]
        pos = end + 9
        if b"FlateDecode" not in header:
            continue
        try:
            data = zlib.decompress(stream_data)
        except zlib.error:
            try:
                data = zlib.decompress(stream_data, -15)
            except zlib.error:
                continue
        total += len(data)
        out.append(data)
    return out


def _pdf_unescape(s: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c == 0x5C and i + 1 < len(s):  # backslash
            n = s[i + 1]
            mapping = {0x6E: 0x0A, 0x72: 0x0D, 0x74: 0x09, 0x28: 0x28, 0x29: 0x29, 0x5C: 0x5C}
            if n in mapping:
                out.append(mapping[n])
                i += 2
            elif 0x30 <= n <= 0x37:  # 八进制
                j = i + 1
                oct_digits = b""
                while j < len(s) and len(oct_digits) < 3 and 0x30 <= s[j] <= 0x37:
                    oct_digits += bytes([s[j]])
                    j += 1
                out.append(int(oct_digits, 8) & 0xFF)
                i = j
            else:
                out.append(n)
                i += 2
        else:
            out.append(c)
            i += 1
    return bytes(out)


def _extract_text_from_content(data: bytes) -> tuple[int, list[str]]:
    """从内容流提取文本操作符字符串。返回 (文本字节数, 片段列表)。"""
    text_bytes = 0
    fragments: list[str] = []
    for bt in _RE_BT_ET.finditer(data):
        block = bt.group(1)
        for m in _RE_TJ_STR.finditer(block):
            s = m.group(0)
            raw_str = s[1 : s.rfind(b")")]
            text_bytes += len(raw_str)
            fragments.append(_pdf_unescape(raw_str).decode("latin-1", errors="replace"))
        for m in _RE_TJ_ARR.finditer(block):
            arr = m.group(0)
            parts = []
            for sm in _RE_PDF_STR.finditer(arr):
                parts.append(_pdf_unescape(sm.group(0)[1:-1]))
            joined = b"".join(parts)
            text_bytes += len(joined)
            fragments.append(joined.decode("latin-1", errors="replace"))
    return text_bytes, fragments


def preflight_pdf(source_path: Path, detected=None) -> dict:
    """路由预检：返回 pdf_class ∈ digital_simple / scanned / mixed_complex / encrypted。"""
    raw = source_path.read_bytes()
    features: dict = {}
    if detect_pdf_encrypted(raw):
        features.update({"pdf_class": "encrypted", "encrypted": True})
        return features
    if detect_pdf_javascript(raw):
        features["javascript"] = True
    pages = max(1, len(_RE_PAGE.findall(raw)))
    features["pages_estimate"] = pages
    image_refs = len(re.findall(rb"/Subtype\s*/Image", raw))
    features["image_xobjects"] = image_refs

    streams = _decompress_streams(raw, max_streams=40, max_total=8 * 1024 * 1024)
    text_total = 0
    for data in streams:
        n, _ = _extract_text_from_content(data)
        text_total += n
    sampled_streams = max(1, len(streams))
    features["sampled_streams"] = sampled_streams
    features["sampled_text_bytes"] = text_total

    # 分类启发式
    if b"%%EOF" not in raw[-2048:]:
        features["possibly_corrupt"] = True
    if text_total >= 500 and image_refs <= pages:
        pdf_class = "digital_simple"
    elif text_total < 100 and image_refs >= max(1, int(pages * 0.6)):
        pdf_class = "scanned"
    elif text_total < 100 and not streams:
        pdf_class = "scanned"
    else:
        pdf_class = "mixed_complex"
    features["pdf_class"] = pdf_class
    features["heuristic"] = "pdflite-sampling"
    return features


def parse(source_path: Path, *, kind: str, format: str | None, options: dict, limits: dict, work_dir: Path) -> dict:
    from gateway.errors import WorkerFailed

    raw = source_path.read_bytes()
    notes: list[str] = [
        "pdflite 兜底提取：无版面坐标，锚点为内容流级近似；建议安装 docling/mineru 获得可靠解析",
    ]
    if detect_pdf_encrypted(raw):
        raise WorkerFailed("PDF 已加密，需要密码（qpdf worker）", kind="unsupported")
    if detect_pdf_javascript(raw):
        notes.append("检测到 /JavaScript 动作字典——活动内容不执行，仅标记")

    max_pages = limits.get("pdf_max_pages_preflight", 20000)
    pages = max(1, len(_RE_PAGE.findall(raw)))
    truncated = pages > max_pages
    if truncated:
        notes.append(f"页数估计 {pages} 超过上限 {max_pages}，仅处理采样部分")

    streams = _decompress_streams(raw, max_streams=_MAX_STREAMS, max_total=_MAX_DECOMPRESSED)
    if len(streams) >= _MAX_STREAMS:
        truncated = True
        notes.append(f"内容流超过 {_MAX_STREAMS}，已截断")

    blocks: list[dict] = []
    text_chars = 0
    cid_garbled = 0
    for si, data in enumerate(streams):
        n, fragments = _extract_text_from_content(data)
        joined = "\n".join(f for f in fragments if f.strip())
        if not joined.strip():
            continue
        text_chars += len(joined)
        # CID 字体启发：高比例不可打印字符
        bad = sum(1 for ch in joined[:200] if ord(ch) < 32 and ch not in "\n\t")
        if len(joined) > 20 and bad > len(joined[:200]) * 0.3:
            cid_garbled += 1
        blocks.append(
            {
                "block_type": "stream",
                "index": si,
                "label": f"内容流 {si}",
                "elements": [
                    {
                        "type": "paragraph",
                        "text": joined,
                        "locator": {"system": "stream", "index": si},
                        "attrs": {"text_bytes": n},
                    }
                ],
            }
        )
    if cid_garbled:
        notes.append(f"{cid_garbled} 个内容流疑似 CID 字体（中文常见），提取文本可能为乱码——务必用 MinerU/Docling 重解析")

    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        "text_extraction": "pass" if text_chars > 0 else "fail",
        "layout_preservation": "fail",
        "metadata": "warning",
        "source_anchoring": "warning",
    }
    if not blocks:
        notes.append("未能提取任何文本（可能为纯扫描件——需要 MinerU/OCR worker）")
    return {
        "parser_name": "pdf_lite",
        "parser_version": PARSER_VERSION,
        "operation": "parse",
        "content": {"blocks": blocks},
        "quality_dims": dims,
        "fitness": {"quotation": False, "layout_sensitive_analysis": False},
        "notes": notes,
        "truncated": truncated,
    }
