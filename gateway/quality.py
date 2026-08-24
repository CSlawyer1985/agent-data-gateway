"""质量模型：八维聚合 + fitness 适用性 + reliably_read 判定（spec 05）。

规范数据 models/quality_dimensions.v0.1.json 由本模块加载执行。
parser 给建议值，编排器拥有最终定稿权。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_SPEC_PATH = Path(__file__).resolve().parent.parent / "models" / "quality_dimensions.v0.1.json"

DIMENSIONS = [
    "discovery",
    "type_detection",
    "text_extraction",
    "layout_preservation",
    "ocr",
    "attachments",
    "metadata",
    "source_anchoring",
]

FITNESS_KEYS = [
    "fulltext_search",
    "fact_extraction",
    "quotation",
    "layout_sensitive_analysis",
    "authenticity_assessment",
]

DIM_VALUES = ("pass", "warning", "fail", "unknown")


def load_spec(path: Path | None = None) -> dict[str, Any]:
    with open(path or _SPEC_PATH, encoding="utf-8") as f:
        return json.load(f)


DEFAULT_SPEC: dict[str, Any] = load_spec()


def expected_dimensions(kind: str, spec: dict | None = None) -> set[str]:
    """该 kind 的 expected 维度集合。"""
    spec = spec or DEFAULT_SPEC
    out: set[str] = set()
    for dim, meta in spec["dimensions"].items():
        exp = meta.get("expected_for", [])
        if "*" in exp or kind in exp:
            out.add(dim)
    return out


def aggregate_overall(kind: str, dims: dict[str, str], spec: dict | None = None) -> str:
    """按聚合规则定稿 overall。dims 未覆盖的 expected 维度视为 unknown。"""
    expected = expected_dimensions(kind, spec)
    values = {d: dims.get(d, "unknown") for d in expected}
    if any(v == "fail" for v in values.values()):
        return "fail"
    if any(v in ("warning", "unknown") for v in values.values()):
        return "pass_with_warnings"
    return "pass"


def finalize_quality(
    kind: str,
    suggested_dims: dict[str, str],
    fitness: dict[str, bool] | None = None,
    notes: list[str] | None = None,
    spec: dict | None = None,
) -> dict:
    """质量定稿：补全维度、聚合 overall、保证 fitness 五键齐全。

    suggested_dims 中的非法值一律降为 unknown（worker 输出不可信）。
    """
    cleaned: dict[str, str] = {}
    for dim in DIMENSIONS:
        v = suggested_dims.get(dim, "unknown")
        cleaned[dim] = v if v in DIM_VALUES else "unknown"
    fit = {k: bool((fitness or {}).get(k, False)) for k in FITNESS_KEYS}
    fit["authenticity_assessment"] = False  # 恒 false：摄取 ≠ 取证
    quality: dict[str, Any] = {"overall": aggregate_overall(kind, cleaned, spec)}
    quality.update(cleaned)
    quality["fitness"] = fit
    quality["notes"] = list(notes or [])
    return quality


def default_fitness(
    kind: str,
    dims: dict[str, str],
    *,
    text_chars: int = 0,
    truncated: bool = False,
    anchor_problems: int = 0,
) -> dict[str, bool]:
    """fitness 默认推导：parser 可覆盖，但推导保证下限诚实。

    - fulltext_search：有可用文本且 text_extraction 非 fail
    - fact_extraction：存在文本、text_extraction=pass 且未截断（截断后事实可能缺半）
    - quotation：文本完整可用、未截断、source_anchoring=pass 且无锚点问题
    - layout_sensitive_analysis：layout_preservation=pass
    - authenticity_assessment：恒 false
    """
    fit = {
        "fulltext_search": text_chars > 0 and dims.get("text_extraction") in ("pass", "warning"),
        "fact_extraction": text_chars > 0 and dims.get("text_extraction") == "pass" and not truncated,
        "quotation": (
            text_chars > 0
            and dims.get("text_extraction") == "pass"
            and not truncated
            and dims.get("source_anchoring") == "pass"
            and anchor_problems == 0
        ),
        "layout_sensitive_analysis": dims.get("layout_preservation") == "pass",
        "authenticity_assessment": False,
    }
    return fit


def reliably_read(rendition_qualities: list[dict]) -> bool:
    """adg status 判据：存在 rendition overall∈{pass,pass_with_warnings} 且 fact_extraction。"""
    for q in rendition_qualities:
        if q.get("overall") in ("pass", "pass_with_warnings") and q.get("fitness", {}).get("fact_extraction"):
            return True
    return False


def reliably_read_item(kind: str, status: str, rendition_qualities: list[dict]) -> bool:
    """item 级判据。容器类（archive）须全部成员展开（status=parsed）才算可靠——
    parsed_with_warnings 意味着有成员被安全拦截，内容缺失；其余按 rendition 质量。"""
    if kind == "archive":
        return status == "parsed"
    return reliably_read(rendition_qualities)


def reliably_read_ids(items, qualities_of) -> set[str]:
    """包级判定：返回"内容已可靠读取"的 source_id 集合。

    单看自身 rendition 是不够的：transform 型原件（扫描 PDF → 页位图、DWG → DXF/渲染图、
    视频 → 抽帧、PST → EML）自己没有内容 rendition，内容在派生物里。若只按自身 rendition 判，
    报告会把"已经通过派生物读到"误报成"没读到"——这正是本项目要防的那类失败。

    规则：① 自身 rendition 可靠 → 可靠；② 自身无内容 rendition 但已 parsed，且至少一个
    派生物/成员可靠 → 可靠（沿派生链传递，支持 PDF → 页位图 → OCR 这样的多级）。
    """
    ok: set[str] = set()
    for it in items:
        if reliably_read_item(it.kind, it.status, qualities_of(it.source_id)):
            ok.add(it.source_id)

    children: dict[str, list] = {}
    for it in items:
        parent = None
        if getattr(it, "derived_from", None):
            parent = it.derived_from.get("source_id")
        elif getattr(it, "origin", None):
            parent = it.origin.get("container_source_id")
        if parent:
            children.setdefault(parent, []).append(it)

    changed = True
    while changed:  # 逐层上溯直到不动点
        changed = False
        for it in items:
            if it.source_id in ok or it.status not in ("parsed", "parsed_with_warnings"):
                continue
            if it.kind == "archive":  # 有成员被安全拦截的容器内容缺失，不因其余成员可读而回填
                continue
            if any(child.source_id in ok for child in children.get(it.source_id, ())):
                ok.add(it.source_id)
                changed = True
    return ok


def best_quality_rank(overall: str | None) -> int:
    return {"pass": 2, "pass_with_warnings": 1, "fail": 0, None: -1}[overall]
