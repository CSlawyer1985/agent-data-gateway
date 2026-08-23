"""哈希与规范化序列化。

source_id / rendition_id / package_id / payload_sha256 的全部计算入口集中在此，
保证全系统同一种 canonical 形式。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, BinaryIO, Union

CHUNK = 1024 * 1024  # 1MB


def canonical_json(obj: Any) -> str:
    """全系统统一的 canonical JSON：键排序、无空白、UTF-8 原样输出。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_stream(fp: BinaryIO, limit: int | None = None) -> str:
    """流式 sha256。limit 用于只读前 N 字节（预检）。"""
    h = hashlib.sha256()
    remaining = limit
    while True:
        want = CHUNK if remaining is None else min(CHUNK, remaining)
        if want <= 0:
            break
        buf = fp.read(want)
        if not buf:
            break
        h.update(buf)
        if remaining is not None:
            remaining -= len(buf)
    return h.hexdigest()


def sha256_file(path: Union[str, Path]) -> str:
    with open(path, "rb") as f:
        return sha256_stream(f)


def source_id_of_bytes(data: bytes) -> str:
    return "sha256:" + sha256_bytes(data)


def source_id_of_file(path: Union[str, Path]) -> str:
    return "sha256:" + sha256_file(path)


def rendition_id_of(content: dict, annotations: dict, coordinate_system: dict) -> str:
    """rendition_id = sha256({content, annotations, provenance.coordinate_system} 的 canonical JSON)。"""
    payload = {
        "annotations": annotations,
        "content": content,
        "coordinate_system": coordinate_system,
    }
    return "sha256:" + sha256_text(canonical_json(payload))


def payload_hash_of(doc: dict) -> str:
    """对除 integrity.payload_sha256 外的全文档取 canonical JSON sha256。"""
    clone = json.loads(json.dumps(doc))  # 深拷贝（纯 JSON 类型）
    integ = clone.get("integrity", {})
    integ.pop("payload_sha256", None)
    return sha256_text(canonical_json(clone))


def package_id_of(source_ids: list[str]) -> str:
    """package_id = sha256(所有 source_id 排序后以 \\n 连接)。"""
    joined = "\n".join(sorted(source_ids))
    return "sha256:" + sha256_text(joined)

