"""adg index — 构建/更新检索索引（供 adg search / adg anchor / adg mcp）。"""

from __future__ import annotations

from pathlib import Path

from ..index import build_index
from ..manifest import load_manifest
from ._common import print_json, resolve_out_dir, summary_line


def build(sub) -> None:
    p = sub.add_parser("index", help="构建/更新检索索引（元素级，可回溯）")
    p.add_argument("path", help="材料包目录")
    p.add_argument("--out", help="输出目录（须与 scan 一致）")
    p.add_argument("--force", action="store_true", help="强制重建（忽略新鲜度指纹）")
    p.add_argument("--json", action="store_true", help="完整 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    package_path = Path(args.path).resolve()
    out_dir = resolve_out_dir(args, package_path)
    manifest = load_manifest(out_dir)
    if manifest is None:
        summary_line("未找到 manifest——请先 adg scan")
        return 2

    stats = build_index(out_dir, force=args.force, manifest=manifest)
    result = stats.to_dict()
    result["package_root_name"] = manifest.package_root_name
    if args.json:
        print_json(result)
    else:
        state = "已重建" if stats.rebuilt else "已是最新"
        engine = "FTS5 全文索引" if stats.engine == "fts5" else "LIKE 扫描（本机 SQLite 无 FTS5，检索会变慢）"
        summary_line(
            f"index: {stats.items} 项材料 / {stats.renditions} 个 rendition / {stats.elements} 个元素"
            f"（{engine}，{state}）"
        )
        if stats.truncated_elements:
            print(f"  ⚠️ {stats.truncated_elements} 个超长元素入库时被截断（原文仍以 rendition 为准）")
    return 0
