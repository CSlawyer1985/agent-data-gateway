"""来源锚点：元素确定性 ID 派生与回溯校验（spec 07）。

L2 稳定性：同一 parser/版本/配置重跑同一原件，element_id 必须 100% 一致。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_locator(locator: dict) -> str:
    return json.dumps(locator, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def derive_element_id(parser_name: str, parser_version: str, config_hash: str, locator: dict, ordinal: int = 0) -> str:
    """element_id = el_ + sha1(name|version|config|locator|ordinal)[:12]"""
    base = f"{parser_name}|{parser_version}|{config_hash}|{canonical_locator(locator)}|{ordinal}"
    return "el_" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def validate_document(doc: dict) -> list[str]:
    """锚点回溯校验：locator 字段是否在内容范围内可解析。返回问题列表。"""
    problems: list[str] = []
    blocks = doc.get("content", {}).get("blocks", [])
    for bi, block in enumerate(blocks):
        elements = block.get("elements", [])
        for ei, el in enumerate(elements):
            loc = el.get("locator") or {}
            system = loc.get("system")
            where = f"block[{bi}].element[{ei}]({el.get('id', '?')})"
            if system is None:
                problems.append(f"{where}: 缺 locator.system")
                continue
            if system == "page_bbox":
                page = loc.get("page")
                if not isinstance(page, int) or page < 0:
                    problems.append(f"{where}: page_bbox 缺合法 page")
                bbox = loc.get("bbox")
                if bbox is not None and (not isinstance(bbox, list) or len(bbox) != 4):
                    problems.append(f"{where}: bbox 必须为四元数组")
            elif system == "paragraph":
                if not isinstance(loc.get("index"), int) or loc["index"] < 0:
                    problems.append(f"{where}: paragraph 缺合法 index")
            elif system == "cell":
                if not loc.get("sheet") or not loc.get("cell"):
                    problems.append(f"{where}: cell 缺 sheet/cell")
            elif system == "row":
                if not isinstance(loc.get("index"), int) or loc["index"] < 0:
                    problems.append(f"{where}: row 缺合法 index")
            elif system == "message":
                if loc.get("id") is None and loc.get("timestamp") is None:
                    problems.append(f"{where}: message 缺 id/timestamp")
            elif system == "timecode":
                start, end = loc.get("start"), loc.get("end")
                if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end < start:
                    problems.append(f"{where}: timecode start/end 不合法")
            elif system == "entity":
                if loc.get("handle") is None and loc.get("layer") is None:
                    problems.append(f"{where}: entity 缺 handle/layer")
            elif system in ("stream", "field"):
                pass  # 近似定位器：pdflite 兜底 / 元数据字段，无范围可校验
            else:
                problems.append(f"{where}: 未知 locator.system={system!r}")
    # 批注锚点存在性
    known_ids = {el.get("id") for b in blocks for el in b.get("elements", [])}
    for c in doc.get("annotations", {}).get("comments", []):
        aid = c.get("anchor_element_id")
        if aid is not None and aid not in known_ids:
            problems.append(f"comment {c.get('id')}: anchor_element_id={aid} 不在文档元素中")
    for r in doc.get("annotations", {}).get("revisions", []):
        aid = r.get("anchor_element_id")
        if aid is not None and aid not in known_ids:
            problems.append(f"revision {r.get('id')}: anchor_element_id={aid} 不在文档元素中")
    return problems


def align_renditions(doc_a: dict, doc_b: dict, *, threshold: float = 0.6) -> dict:
    """跨 rendition 对齐（L3）：同 block_type 下按文本相似度贪心匹配。"""
    from difflib import SequenceMatcher

    def texts(doc: dict) -> list[tuple[str, str]]:
        out = []
        for b in doc.get("content", {}).get("blocks", []):
            for el in b.get("elements", []):
                t = el.get("text")
                if t and t.strip():
                    out.append((el["id"], t.strip()))
        return out

    a_items = texts(doc_a)
    b_items = texts(doc_b)
    pairs: list[dict] = []
    used_b: set[int] = set()
    for aid, atext in a_items:
        best_j, best_score = -1, 0.0
        for j, (bid, btext) in enumerate(b_items):
            if j in used_b:
                continue
            if abs(len(atext) - len(btext)) > max(len(atext), len(btext)):
                continue  # 长度差过半直接跳过，省计算
            score = SequenceMatcher(None, atext, btext).ratio()
            if score > best_score:
                best_j, best_score = j, score
        if best_j >= 0 and best_score >= threshold:
            used_b.add(best_j)
            pairs.append(
                {
                    "rendition_a_element": aid,
                    "rendition_b_element": b_items[best_j][0],
                    "alignment_score": round(best_score, 4),
                }
            )
    return {
        "source_id": doc_a.get("source_id"),
        "rendition_a": doc_a.get("rendition_id"),
        "rendition_b": doc_b.get("rendition_id"),
        "pairs": pairs,
        "coverage_a": round(len(pairs) / len(a_items), 4) if a_items else 0.0,
        "coverage_b": round(len(pairs) / len(b_items), 4) if b_items else 0.0,
    }
