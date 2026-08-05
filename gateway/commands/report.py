"""adg report — 包级质量报告（Markdown 板 + JSON + exit code）。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..manifest import atomic_write_json, atomic_write_text, list_rendition_docs, load_manifest
from ..quality import DIMENSIONS, FITNESS_KEYS, reliably_read_item
from ._common import out_hint, print_json, resolve_out_dir, summary_line


def build(sub) -> None:
    p = sub.add_parser("report", help="包级质量报告")
    p.add_argument("path", help="材料包目录")
    p.add_argument("--out", help="输出目录（须与 scan 一致）")
    p.add_argument("--json", action="store_true", help="完整 JSON 到 stdout（同时写 reports/report.json）")
    p.set_defaults(handler=run)


def run(args) -> int:
    package_path = Path(args.path).resolve()
    out_dir = resolve_out_dir(args, package_path)
    manifest = load_manifest(out_dir)
    if manifest is None:
        summary_line("未找到 manifest——请先 adg scan")
        return 2

    # 聚合所有 rendition 的质量数据
    dim_counter: dict[str, Counter] = {d: Counter() for d in DIMENSIONS}
    fitness_counter: Counter = Counter()
    per_item: list[dict] = []
    reliable = 0
    for it in manifest.items:
        docs = list_rendition_docs(out_dir, it.source_id)
        qualities = [d.get("quality", {}) for d in docs]
        is_reliable = reliably_read_item(it.kind, it.status, qualities)
        reliable += 1 if is_reliable else 0
        best = None
        for d in docs:
            q = d.get("quality", {})
            for dim in DIMENSIONS:
                dim_counter[dim][q.get(dim, "unknown")] += 1
            if best is None or _rank(q.get("overall")) > _rank(best.get("overall")):
                best = d
        fit = (best or {}).get("quality", {}).get("fitness", {})
        for k in FITNESS_KEYS:
            if fit.get(k):
                fitness_counter[k] += 1
        per_item.append(
            {
                "path": it.package_relative_path,
                "kind": it.kind,
                "status": it.status,
                "reliable": is_reliable,
                "best_overall": (best or {}).get("quality", {}).get("overall"),
                "renditions": len(it.renditions),
            }
        )

    total = len(manifest.items)
    report = {
        "package_root_name": manifest.package_root_name,
        "package_id": manifest.package_id,
        "overall": manifest.quality.get("overall"),
        "extraction_coverage": manifest.quality.get("extraction_coverage"),
        "reliably_read": reliable,
        "total_items": total,
        "dimension_histogram": {d: dict(dim_counter[d]) for d in DIMENSIONS},
        "fitness_coverage": {k: fitness_counter.get(k, 0) for k in FITNESS_KEYS},
        "unresolved_items": manifest.integrity.get("unresolved_items", []),
        "per_item": per_item,
    }

    reports_dir = out_dir / "reports"
    atomic_write_json(reports_dir / "report.json", report)
    atomic_write_text(reports_dir / "report.md", _render_md(report))
    if args.json:
        print_json(report)
    else:
        cov = (report["extraction_coverage"] or 0) * 100
        summary_line(
            f"report: 覆盖 {cov:.0f}%（可靠读取 {reliable}/{total}，未解决 {len(report['unresolved_items'])}）"
            f" → {out_hint(reports_dir)}/report.md（{report['overall']}）"
        )
    return {"pass": 0, "pass_with_warnings": 1, "fail": 2}.get(report["overall"], 2)


def _rank(overall: str | None) -> int:
    return {"pass": 2, "pass_with_warnings": 1, "fail": 0, None: -1}[overall]


def _render_md(report: dict) -> str:
    lines = [
        f"# 摄取质量报告：{report['package_root_name']}",
        "",
        f"- 包身份：`{report['package_id'][:23]}…`",
        f"- 总体状态：{report['overall']}",
        f"- 可靠读取：{report['reliably_read']}/{report['total_items']}（{(report['extraction_coverage'] or 0) * 100:.1f}%）",
        "",
        "## 维度分布（按 rendition）",
        "",
        "| 维度 | pass | warning | fail | unknown |",
        "|------|------|---------|------|---------|",
    ]
    dim_cn = {
        "discovery": "发现", "type_detection": "类型识别", "text_extraction": "文本提取",
        "layout_preservation": "版面保留", "ocr": "OCR", "attachments": "附件",
        "metadata": "元数据", "source_anchoring": "来源锚定",
    }
    for dim, hist in report["dimension_histogram"].items():
        lines.append(
            f"| {dim_cn.get(dim, dim)} | {hist.get('pass', 0)} | {hist.get('warning', 0)} | {hist.get('fail', 0)} | {hist.get('unknown', 0)} |"
        )
    fit_cn = {
        "fulltext_search": "全文检索", "fact_extraction": "事实提取", "quotation": "可靠引用",
        "layout_sensitive_analysis": "版面敏感分析", "authenticity_assessment": "真伪鉴定",
    }
    lines += ["", "## 适用性覆盖（按 item 最优 rendition）", "", "| 适用性 | 可用 item 数 |", "|--------|--------------|"]
    for k, n in report["fitness_coverage"].items():
        lines.append(f"| {fit_cn.get(k, k)} | {n} |")
    unresolved = report["unresolved_items"]
    if unresolved:
        lines += ["", f"## 未可靠读取与待办（{len(unresolved)}）", "", "| 文件 | 阶段 | 原因 | 建议 |", "|------|------|------|------|"]
        for u in unresolved:
            lines.append(f"| `{u['package_relative_path']}` | {u.get('stage', '')} | {u['reason']} | {u.get('suggested_action') or '—'} |")
    unreliable_items = [p for p in report["per_item"] if not p["reliable"] and p["status"] not in ("unsupported",)]
    if unreliable_items:
        lines += ["", f"## 未可靠读取清单（{len(unreliable_items)}）", ""]
        for p in unreliable_items:
            lines.append(f"- `{p['path']}`（{p['kind']}，{p['status']}）")
    lines.append("")
    return "\n".join(lines)
