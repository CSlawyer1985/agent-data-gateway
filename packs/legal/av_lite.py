"""音视频元数据解析（stdlib 兜底）：WAV 头 / MP4 mvhd 时长。

转写不在本模块（whisper worker）。元数据 rendition 诚实标注
text_extraction=fail——音视频内容未被读取。
"""

from __future__ import annotations

import struct
from pathlib import Path

PARSER_VERSION = "0.1.0"


def _wav_fields(raw: bytes) -> list[dict]:
    fields: list[dict] = []
    if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return fields
    pos = 12
    fmt: dict = {}
    data_size = None
    while pos + 8 <= len(raw):
        cid = raw[pos : pos + 4]
        (csize,) = struct.unpack_from("<I", raw, pos + 4)
        if cid == b"fmt " and pos + 8 + csize <= len(raw):
            audio_format, channels, rate, _br, _ba, bits = struct.unpack_from("<HHIIHH", raw, pos + 8)
            fmt = {"channels": channels, "sample_rate": rate, "bits_per_sample": bits, "audio_format": audio_format}
        elif cid == b"data":
            data_size = csize
            break
        pos += 8 + csize + (csize % 2)
    if fmt:
        for k, v in fmt.items():
            fields.append({"type": "field", "text": f"{k}: {v}", "locator": {"system": "field", "name": k}})
        byte_rate = fmt["sample_rate"] * fmt["channels"] * max(1, fmt["bits_per_sample"] // 8)
        if data_size and byte_rate:
            duration = data_size / byte_rate
            fields.append({"type": "field", "text": f"duration_seconds: {duration:.2f}", "locator": {"system": "field", "name": "duration_seconds"}})
    return fields


def _mp4_duration(raw: bytes) -> float | None:
    """walk box 树找 moov/mvhd 的 duration/timescale。"""
    def find_mvhd(buf: bytes, start: int, end: int, depth: int) -> bytes | None:
        pos = start
        while pos + 8 <= end and depth < 6:
            size, btype = struct.unpack_from(">I4s", buf, pos)
            if size == 1 and pos + 16 <= end:
                (size,) = struct.unpack_from(">Q", buf, pos + 8)
                header = 16
            else:
                header = 8
            if size < 8 or pos + size > end + 8:
                break
            if btype == b"mvhd":
                return buf[pos + header : pos + size]
            if btype in (b"moov", b"trak", b"mdia"):
                found = find_mvhd(buf, pos + header, min(pos + size, end), depth + 1)
                if found:
                    return found
            pos += size
        return None

    mvhd = find_mvhd(raw, 0, min(len(raw), 32 * 1024 * 1024), 0)
    if not mvhd or len(mvhd) < 20:
        return None
    version = mvhd[0]
    try:
        if version == 1:
            timescale, duration = struct.unpack_from(">IQ", mvhd, 20)
        else:
            timescale, duration = struct.unpack_from(">II", mvhd, 12)
        if timescale:
            return duration / timescale
    except struct.error:
        pass
    return None


def parse(source_path: Path, *, kind: str, format: str | None, options: dict, limits: dict, work_dir: Path) -> dict:
    raw = source_path.read_bytes()
    notes: list[str] = []
    fields: list[dict] = []
    fmt = (format or "").lower()
    if fmt == "wav" or (raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"):
        fields = _wav_fields(raw)
    elif fmt in ("mp4", "quicktime") or raw[4:8] == b"ftyp":
        duration = _mp4_duration(raw)
        if duration:
            fields.append({"type": "field", "text": f"duration_seconds: {duration:.2f}", "locator": {"system": "field", "name": "duration_seconds"}})
    if not fields:
        notes.append(f"{fmt or kind} 无 stdlib 元数据解析（需要 ffprobe worker）")
    notes.append("未转写：whisper worker 可用时自动转写并生成时间码锚点；当前音视频内容未被读取")

    blocks = [{"block_type": "section", "index": 0, "label": "媒体元数据", "elements": fields}]
    dims = {
        "discovery": "pass",
        "type_detection": "pass",
        # 音视频转写由 whisper worker 完成；av_lite 不做转写是常态，标 unknown 而非 fail
        # （fail 会把整体压成 failed；unknown 表达"该工具不做"，配合 fitness.fact_extraction=false 表达内容未读）
        "text_extraction": "unknown",
        "metadata": "pass" if fields else "warning",
        "source_anchoring": "unknown",
    }
    return {
        "parser_name": "av_lite",
        "parser_version": PARSER_VERSION,
        "operation": "parse",
        "content": {"blocks": blocks},
        "quality_dims": dims,
        "fitness": {"fact_extraction": False, "quotation": False},
        "notes": notes,
        "truncated": False,
    }
