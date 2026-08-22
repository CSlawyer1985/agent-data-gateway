"""adg status — 摄取状态：哪些已读全、哪些未可靠读取。

判定逻辑与 adg search 的覆盖提示同源（gateway.agent_api），避免同一问题出现两种答案。
"""

from __future__ import annotations

from pathlib import Path

from ..agent_api import open_package
from ..errors import ADGError
from ._common import print_json, resolve_out_dir, summary_line


def build(sub) -> None:
    p = sub.add_parser("status", help="摄取状态：已读全 vs 未可靠读取")
    p.add_argument("path", help="材料包目录")
    p.add_argument("--out", help="输出目录（须与 scan 一致）")
    p.add_argument("--json", action="store_true", help="完整 JSON 输出")
    p.set_defaults(handler=run)


def run(args) -> int:
    package_path = Path(args.path).resolve()
    out_dir = resolve_out_dir(args, package_path)
    try:
        with open_package(package_path, out_dir) as view:
            result = view.get_ingestion_status()
    except ADGError as e:
        summary_line(str(e))
        return 2

    unreliable = result["not_reliably_read"]
    if args.json:
        print_json(result)
    else:
        summary_line(
            f"status: 已可靠读取 {result['reliably_read_count']} 项；未可靠读取 {len(unreliable)} 项"
            + (f"（含 {unreliable[0]['package_relative_path']} 等）" if unreliable else "——材料包已读全")
        )
        for e in unreliable[:20]:
            print(f"  ❔ {e['package_relative_path']}（{e['kind']}）: {e['reason']}")
    return 0 if not unreliable else 1
