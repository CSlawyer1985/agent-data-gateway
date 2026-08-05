"""adg list — 列出包内 items 及状态。"""

from __future__ import annotations

from pathlib import Path

from ..manifest import load_manifest
from ._common import print_json, resolve_out_dir, summary_line


def build(sub) -> None:
    p = sub.add_parser("list", help="列出包内 items 及状态")
    p.add_argument("path", help="材料包目录")
    p.add_argument("--out", help="输出目录（须与 scan 一致）")
    p.add_argument("--kind", help="按类型过滤（如 pdf）")
    p.add_argument("--status", dest="status_filter", help="按状态过滤（如 fail/unresolved）")
    p.add_argument("--json", action="store_true", help="完整 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    package_path = Path(args.path).resolve()
    out_dir = resolve_out_dir(args, package_path)
    manifest = load_manifest(out_dir)
    if manifest is None:
        summary_line("未找到 manifest——请先 adg scan")
        return 2

    items = []
    for it in manifest.items:
        if args.kind and it.kind != args.kind:
            continue
        if args.status_filter:
            status_map = {"fail": "failed"}
            want = status_map.get(args.status_filter, args.status_filter)
            if it.status != want:
                continue
        items.append(it.to_manifest_entry())

    if args.json:
        print_json({"total": len(items), "items": items})
    else:
        summary_line(f"list: {len(items)} 项")
        for e in items[:200]:
            bq = e.get("best_quality") or "—"
            print(f"  [{e['status']:<22}] {e['kind']:<8} {bq:<20} {e['package_relative_path']}")
        if len(items) > 200:
            print(f"  … 其余 {len(items) - 200} 项见 manifest.json")
    return 0
