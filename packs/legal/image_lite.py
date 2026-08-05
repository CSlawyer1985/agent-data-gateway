"""图片元数据解析（stdlib）：JPEG EXIF / PNG IHDR+文本块 / TIFF / BMP / GIF。

OCR 不在本模块（paddleocr worker）。本模块产物是元数据 rendition：
诚实标注 ocr=unknown，fitness.fact_extraction=false——图片未被"读取"。
"""

from __future__ import annotations

import struct
from pathlib import Path

PARSER_VERSION = "0.1.0"

_EXIF_TAGS = {
    0x010F: "make",
    0x0110: "model",
    0x0112: "orientation",
    0x0132: "datetime",
    0x0100: "width",
    0x0101: "height",
    0x829A: "exposure_time",
    0x829D: "f_number",
    0x9003: "datetime_original",
    0xA434: "lens_model",
}


def _tiff_ifd_fields(data: bytes, base: int, bo: str, fields: list[dict], depth: int = 0) -> bool:
    """解析一层 IFD，返回是否含 GPS 指针。bo: '<' 或 '>'。"""
    if depth > 2 or base + 2 > len(data):
        return False
    (count,) = struct.unpack_from(bo + "H", data, base)
    has_gps = False
    for i in range(min(count, 64)):
        off = base + 2 + i * 12
        if off + 12 > len(data):
            break
        tag, ftype, num = struct.unpack_from(bo + "HHI", data, off)
        value_off = off + 8
        if tag == 0x8825:  # GPSInfoIFD 指针
            has_gps = True
            continue
        if tag == 0x8769 and ftype == 4:  # ExifIFD 指针 → 递归
            (sub,) = struct.unpack_from(bo + "I", data, value_off)
            _tiff_ifd_fields(data, sub, bo, fields, depth + 1)
            continue
        name = _EXIF_TAGS.get(tag)
        if not name:
            continue
        value = None
        if ftype == 2 and num <= 64:  # ASCII
            (voff,) = struct.unpack_from(bo + "I", data, value_off) if num > 4 else (value_off,)
            raw = data[voff : voff + num].rstrip(b"\x00")
            value = raw.decode("latin-1", errors="replace").strip()
        elif ftype == 3 and num == 1:  # SHORT
            (value,) = struct.unpack_from(bo + "H", data, value_off)
        elif ftype == 4 and num == 1:  # LONG
            (value,) = struct.unpack_from(bo + "I", data, value_off)
        if value is not None and str(value):
            fields.append({"type": "field", "text": f"{name}: {value}", "locator": {"system": "field", "name": name}})
    return has_gps


def _jpeg_fields(raw: bytes) -> list[dict]:
    fields: list[dict] = []
    # 尺寸：SOF0/2
    i = 2
    while i + 4 < len(raw) and raw[i] == 0xFF:
        marker = raw[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            if i + 9 < len(raw):
                height, width = struct.unpack_from(">HH", raw, i + 5)
                fields.append({"type": "field", "text": f"width: {width}", "locator": {"system": "field", "name": "width"}})
                fields.append({"type": "field", "text": f"height: {height}", "locator": {"system": "field", "name": "height"}})
        if marker == 0xE1 and raw[i + 4 : i + 10] == b"Exif\x00\x00":
            tiff = raw[i + 10 :]
            if tiff[:2] == b"II":
                bo = "<"
            elif tiff[:2] == b"MM":
                bo = ">"
            else:
                break
            try:
                (ifd0,) = struct.unpack_from(bo + "I", tiff, 4)
                has_gps = _tiff_ifd_fields(tiff, ifd0, bo, fields)
                if has_gps:
                    fields.append({"type": "field", "text": "gps: 含 GPS 定位信息", "locator": {"system": "field", "name": "gps"}})
            except struct.error:
                pass
        if marker == 0xDA:  # SOS：扫描数据开始，停止
            break
        if i + 4 >= len(raw):
            break
        (seg_len,) = struct.unpack_from(">H", raw, i + 2)
        i += 2 + seg_len
    return fields


def _png_fields(raw: bytes) -> list[dict]:
    fields: list[dict] = []
    if len(raw) >= 24:
        width, height = struct.unpack_from(">II", raw, 16)
        fields.append({"type": "field", "text": f"width: {width}", "locator": {"system": "field", "name": "width"}})
        fields.append({"type": "field", "text": f"height: {height}", "locator": {"system": "field", "name": "height"}})
    i = 8
    while i + 8 <= len(raw):
        (length,) = struct.unpack_from(">I", raw, i)
        ctype = raw[i + 4 : i + 8]
        if ctype == b"tEXt" and i + 8 + length <= len(raw):
            chunk = raw[i + 8 : i + 8 + length]
            key, _, value = chunk.partition(b"\x00")
            k = key.decode("latin-1", errors="replace")
            v = value.decode("latin-1", errors="replace")[:200]
            if k:
                fields.append({"type": "field", "text": f"{k}: {v}", "locator": {"system": "field", "name": f"png_{k}"}})
        i += 12 + length
        if ctype == b"IDAT":
            break
    return fields


def _tiff_fields(raw: bytes) -> list[dict]:
    fields: list[dict] = []
    bo = "<" if raw[:2] == b"II" else ">"
    try:
        (ifd0,) = struct.unpack_from(bo + "I", raw, 4)
        has_gps = _tiff_ifd_fields(raw, ifd0, bo, fields)
        if has_gps:
            fields.append({"type": "field", "text": "gps: 含 GPS 定位信息", "locator": {"system": "field", "name": "gps"}})
    except struct.error:
        pass
    return fields


def parse(source_path: Path, *, kind: str, format: str | None, options: dict, limits: dict, work_dir: Path) -> dict:
    raw = source_path.read_bytes()
    notes: list[str] = []
    fields: list[dict] = []
    fmt = (format or "").lower()
    if fmt == "jpeg" or raw.startswith(b"\xff\xd8\xff"):
        fields = _jpeg_fields(raw)
    elif fmt == "png" or raw.startswith(b"\x89PNG"):
        fields = _png_fields(raw)
    elif fmt == "tiff" or raw[:4] in (b"II*\x00", b"MM\x00*"):
        fields = _tiff_fields(raw)
    elif fmt == "heic" or raw[4:8] == b"ftyp":
        notes.append("HEIC/HEIF 元数据需要 Pillow/libheif worker，stdlib 无法解析")
    else:
        notes.append(f"图片格式 {fmt or '未知'} 无 stdlib 元数据解析器")

    ocr_executed = bool((options or {}).get("ocr_executed"))
    if not ocr_executed:
        notes.append("未执行 OCR（paddleocr worker 可用时自动执行）；图片中的文字内容未被读取")

    blocks = [{"block_type": "section", "index": 0, "label": "图片元数据", "elements": fields}]
    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        "text_extraction": "unknown",
        "layout_preservation": "pass" if fields else "unknown",
        "ocr": "unknown",
        "metadata": "pass" if fields else "warning",
        "source_anchoring": "pass",
    }
    return {
        "parser_name": "image_lite",
        "parser_version": PARSER_VERSION,
        "operation": "parse",
        "content": {"blocks": blocks},
        "quality_dims": dims,
        "fitness": {"fact_extraction": False, "quotation": True},
        "notes": notes,
        "truncated": False,
    }
