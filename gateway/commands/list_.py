"""adg list — 列出包内 items 及状态。

查询逻辑与其他只读子命令同源（gateway.agent_api），避免同一问题出现两种答案。
"""

from __future__ import annotations

from pathlib import Path

from ..agent_api import open_package
from ..errors import ADGError
from ._common import print_json, resolve_out_dir, summary_line


def build(sub) -> None:
    p = sub.add_parser("list", help="列出包内 items 及状态")
    p.add_argument("path", help="材料包目录")
    p.add_argument("--out", help="输出目录（须与 scan 一致）")
    p.add_argument("--kind", help="按类型过滤（如 pdf）")
    p.add_argument("--status", dest="status_filter", help="按状态过滤（如 fail/unresolved）")
    p.add_argument("--unreliable-only", action="store_true", help="只列尚未可靠读取的项")
    p.add_argument("--limit", type=int, default=1000, help="返回上限（默认 1000）")
    p.add_argument("--offset", type=int, default=0, help="分页偏移")
    p.add_argument("--json", action="store_true", help="完整 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    package_path = Path(args.path).resolve()
    out_dir = resolve_out_dir(args, package_path)
    status = args.status_filter
    if status:
        status = {"fail": "failed"}.get(status, status)

    try:
        with open_package(package_path, out_dir) as view:
            result = view.list_package_items(
                kind=args.kind,
                status=status,
                unreliable_only=args.unreliable_only,
                limit=args.limit,
                offset=args.offset,
            )
    except ADGError as e:
        summary_line(str(e))
        return 2

    items = result["items"]
    if args.json:
        print_json(result)
        return 0

    summary_line(f"list: {result['total']} 项" + (f"（本页 {len(items)} 项）" if len(items) != result["total"] else ""))
    for e in items:
        bq = e.get("best_quality") or "—"
        mark = " " if e["reliably_read"] else "❔"
        print(f"  {mark}[{e['status']:<22}] {e['kind']:<8} {bq:<20} {e['package_relative_path']}")
        if e.get("duplicate_paths"):
            others = [p for p in e["duplicate_paths"] if p != e["package_relative_path"]]
            print(f"      ↳ 同一文件另有 {len(others)} 处：{'、'.join(others[:3])}")
    if result["total"] > result["offset"] + len(items):
        print(f"  … 其余 {result['total'] - result['offset'] - len(items)} 项用 --offset {result['offset'] + len(items)} 继续")
    return 0
