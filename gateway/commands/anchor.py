"""adg anchor — 来源回溯：把 element_id 还原为原件位置。"""

from __future__ import annotations

from pathlib import Path

from ..agent_api import open_package
from ..render import format_locator
from ._common import print_json, resolve_out_dir, summary_line


def build(sub) -> None:
    p = sub.add_parser("anchor", help="把 element_id 回溯到原件位置（引用前核对）")
    p.add_argument("path", help="材料包目录")
    p.add_argument("element_id", help="元素 ID（adg search 结果里的 el_xxxx）")
    p.add_argument("--out", help="输出目录（须与 scan 一致）")
    p.add_argument("--rendition", dest="rendition_id", help="限定某个 rendition")
    p.add_argument("--json", action="store_true", help="完整 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    package_path = Path(args.path).resolve()
    out_dir = resolve_out_dir(args, package_path)
    with open_package(package_path, out_dir) as view:
        result = view.resolve_anchor(args.element_id, rendition_id=args.rendition_id)

    if args.json:
        print_json(result)
        return 0 if result["found"] else 1

    if not result["found"]:
        summary_line(f"anchor: 未找到 {args.element_id}——索引可能已过期，试 adg index --force")
        return 1
    summary_line(f"anchor: {args.element_id} 命中 {result['found']} 个 rendition")
    for m in result["matches"]:
        best = "最优 rendition" if m["is_best_rendition"] else "备选 rendition"
        quotable = "可引用" if m["quotable"] else "不可直接引用（诚实降级产物）"
        print(f"  原件: {m['package_relative_path']}（{m['kind']}，{m['item_status']}）")
        print(f"  位置: {format_locator(m['locator'])}〔{quotable}〕")
        print(f"  解析: {m['parser']['name']} {m['parser']['version'] or ''} / {best} / quality={m['quality']}")
        print(f"  原文: {m['text'][:300]}{'…' if len(m['text']) > 300 else ''}")
        ctx = m["context"]
        for c in ctx["before"]:
            print(f"    ↑ {c['text'][:80]}")
        for c in ctx["after"]:
            print(f"    ↓ {c['text'][:80]}")
    return 0
