"""adg search — 元素级检索：命中即带来源锚点，可直接回溯原件。"""

from __future__ import annotations

from pathlib import Path

from ..agent_api import open_package
from ..render import format_locator
from ._common import print_json, resolve_out_dir, summary_line


def build(sub) -> None:
    p = sub.add_parser("search", help="跨材料包检索元素（结果带来源锚点）")
    p.add_argument("path", help="材料包目录")
    p.add_argument("query", help="检索词；空格分隔为 AND，双引号内为短语")
    p.add_argument("--out", help="输出目录（须与 scan 一致）")
    p.add_argument("--kind", help="只搜某类材料（如 pdf/xlsx/image）")
    p.add_argument("--limit", type=int, default=20, help="返回条数上限（默认 20）")
    p.add_argument("--quotable", action="store_true", help="只返回可可靠引用（fitness.quotation）的命中；隐含搜全部 rendition")
    p.add_argument("--all-renditions", action="store_true", help="搜所有 rendition（默认只搜最优 rendition）")
    p.add_argument("--path-prefix", help="只搜某个子路径下的材料（容器成员用 :: 或 / 均可）")
    p.add_argument("--json", action="store_true", help="完整 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    package_path = Path(args.path).resolve()
    out_dir = resolve_out_dir(args, package_path)
    with open_package(package_path, out_dir) as view:
        result = view.search_elements(
            args.query,
            kind=args.kind,
            limit=args.limit,
            quotable_only=args.quotable,
            all_renditions=args.all_renditions,
            path_prefix=args.path_prefix,
        )

    hits = result["hits"]
    if args.json:
        print_json(result)
        return 0 if hits else 1

    summary_line(f"search: 「{args.query}」命中 {len(hits)} 条（{result['engine']}）")
    for i, h in enumerate(hits, 1):
        flags = "可引用" if h["quotable"] else "仅检索/不可直接引用"
        print(f"  {i}. {h['package_relative_path']} — {format_locator(h['locator'])}〔{flags}〕")
        print(f"     {h['snippet']}")
        print(f"     {h['element_id']}  parser={h['parser']} quality={h['quality']}")
    if result.get("stale_index_caveat"):
        print(f"  ⚠️ {result['stale_index_caveat']}")
    if result.get("candidate_cap_reached"):
        print("  ⚠️ 命中过多，LIKE 降级模式只在候选窗口内排序——结果可能不是全局最佳，请缩小检索词")
    if result.get("coverage_caveat"):
        print(f"  ⚠️ {result['coverage_caveat']}")
    if not hits:
        print("  未命中。检索范围只覆盖已可靠读取的材料——先看 adg status。")
    return 0 if hits else 1
